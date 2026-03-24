import calendar
from datetime import date, timedelta
from decimal import Decimal

from django.conf import settings
from django.contrib.auth.models import User
from django.core.mail import send_mail
from django.core.management.base import BaseCommand
from django.db.models import Sum
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.html import mark_safe
from django.utils.translation import gettext as _

from expenses.models import Account, Expense, Income, Transfer
from expenses.utils import get_exchange_rate
from expenses.templatetags.digit_filters import compact_amount

class Command(BaseCommand):
    help = 'Sends beautiful monthly financial reports to verified users'

    def add_arguments(self, parser):
        parser.add_argument('--user-id', type=int, help='Send report only to a specific user ID')
        parser.add_argument('--test', action='store_true', help='Print data instead of sending email')

    def handle(self, *args, **options):
        # Calculate last month's range
        today = timezone.now().date()
        # Report for the previous full month
        first_day_curr_month = today.replace(day=1)
        last_day_prev_month = first_day_curr_month - timedelta(days=1)
        first_day_prev_month = last_day_prev_month.replace(day=1)
        
        # Ranges for report
        start_date = first_day_prev_month
        end_date = last_day_prev_month
        month_name = end_date.strftime('%B %Y')

        # Filter active users with emails
        users = User.objects.filter(is_active=True, email__isnull=False).exclude(email='')
        
        # Check for verified users via allauth if possible
        try:
            from allauth.account.models import EmailAddress
            verified_emails = EmailAddress.objects.filter(verified=True).values_list('user_id', flat=True)
            users = users.filter(id__in=verified_emails)
        except ImportError:
            # Fallback if allauth is not set up correctly or models missing
            pass

        if options['user_id']:
            users = users.filter(id=options['user_id'])

        total_users = users.count()
        self.stdout.write(self.style.SUCCESS(f"Generating reports for {total_users} users for {month_name}..."))

        sent_count = 0
        for user in users:
            try:
                data = self.get_report_data(user, start_date, end_date)
                if not data['has_data']:
                    continue

                if options['test']:
                    self.stdout.write(f"User: {user.email} - NW: {data['nw_at_end']}, Savings: {data['savings']}")
                    continue

                # Render and send email
                context = {
                    'user': user,
                    'month_name': month_name,
                    'data': data,
                    'currency_symbol': user.profile.currency if hasattr(user, 'profile') else '₹',
                }
                
                html_message = render_to_string('emails/monthly_report.html', context)
                
                send_mail(
                    subject=f"Your Financial Summary for {month_name} 📊",
                    message=f"Greetings {user.username}, Your monthly financial summary for {month_name} is ready. Check it out on TrackMyRupee!",
                    from_email=settings.DEFAULT_FROM_EMAIL,
                    recipient_list=[user.email],
                    html_message=html_message,
                )
                
                sent_count += 1
                if sent_count % 10 == 0:
                    self.stdout.write(f"Sent {sent_count} reports...")

            except Exception as e:
                self.stdout.write(self.style.ERROR(f"Error for {user.email}: {e}"))

        self.stdout.write(self.style.SUCCESS(f"Task complete! Sent {sent_count} reports."))

    def get_report_data(self, user, start_date, end_date):
        currency_symbol = user.profile.currency if hasattr(user, 'profile') else '₹'
        
        # 1. Transactions - Using base_amount for multi-currency compatibility
        inc_qs = Income.objects.filter(user=user, date__range=[start_date, end_date])
        exp_qs = Expense.objects.filter(user=user, date__range=[start_date, end_date])
        
        total_income = inc_qs.aggregate(Sum('base_amount'))['base_amount__sum'] or Decimal('0')
        total_expense = exp_qs.aggregate(Sum('base_amount'))['base_amount__sum'] or Decimal('0')
        
        if total_income == 0 and total_expense == 0:
            return {'has_data': False}

        savings = total_income - total_expense
        savings_rate = round((savings / total_income * 100), 1) if total_income > 0 else 0
        
        # 2. Top 3 Categories
        top_categories = exp_qs.values('category').annotate(
            total=Sum('base_amount')
        ).order_by('-total')[:3]
        
        # 3. Net Worth Change (Reconstruction)
        accounts = Account.objects.filter(user=user)
        current_nw = Decimal('0.00')
        for acc in accounts:
            if acc.currency == currency_symbol:
                current_nw += acc.balance
            else:
                rate = get_exchange_rate(acc.currency, currency_symbol)
                current_nw += (acc.balance * rate).quantize(Decimal('0.01'))
        
        # Calculate cashflow from end of report month until today to find NW at end of report month
        today = timezone.now().date()
        cashflow_since_report = Income.objects.filter(user=user, date__gt=end_date, date__lte=today).aggregate(Sum('base_amount'))['base_amount__sum'] or Decimal('0')
        expense_since_report = Expense.objects.filter(user=user, date__gt=end_date, date__lte=today).aggregate(Sum('base_amount'))['base_amount__sum'] or Decimal('0')
        
        nw_at_end = current_nw - (cashflow_since_report - expense_since_report)
        nw_at_start = nw_at_end - (total_income - total_expense)
        
        nw_change = nw_at_end - nw_at_start
        nw_change_pct = round((nw_change / nw_at_start * 100), 1) if nw_at_start > 0 else 0

        # 4. AI Insight (Highlighted context)
        ai_insight = None
        if top_categories:
            top_cat = top_categories[0]
            top_pct = round((float(top_cat['total']) / float(total_expense) * 100)) if total_expense > 0 else 0
            potential = float(top_cat['total']) * 0.15 # Suggest 15% saving
            
            ai_insight = _("You spent <b>{pct}%</b> of your total budget on <b>{cat}</b>. Reducing this by 15% next month could save you <b>{sym}{savings}</b>!").format(
                cat=top_cat['category'],
                pct=top_pct,
                sym=currency_symbol,
                savings=compact_amount(potential, currency_symbol)
            )

        return {
            'has_data': True,
            'income': total_income,
            'expense': total_expense,
            'savings': savings,
            'savings_rate': savings_rate,
            'top_categories': list(top_categories),
            'nw_at_end': nw_at_end,
            'nw_change': nw_change,
            'nw_change_pct': nw_change_pct,
            'ai_insight': mark_safe(ai_insight) if ai_insight else None
        }
