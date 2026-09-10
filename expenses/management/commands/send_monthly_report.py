from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.contrib.auth.models import User
from django.core.mail import send_mail
from django.core.management.base import BaseCommand
from django.db.models import Sum
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.html import escape, mark_safe
from django.utils.translation import gettext as _

from expenses.account_types import resolve_category_selector
from expenses.ledger_read_service import LedgerReadService
from expenses.models import Account, CapitalEvent, EmailLog, Expense, Income, LoanRepayment
from expenses.templatetags.digit_filters import compact_amount
from expenses.utils import get_exchange_rate


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
        users = User.objects.filter(is_active=True, email__isnull=False).exclude(email='').exclude(username='demo')
        
        # Check for verified users via allauth if possible
        try:
            from allauth.account.models import EmailAddress
            verified_emails = EmailAddress.objects.filter(verified=True).values_list('user_id', flat=True)
            users = users.filter(id__in=verified_emails)
        except ImportError:
            # Fallback if allauth is not set up correctly or models missing
            pass

        if options.get('user_id'):
            users = users.filter(id=options['user_id'])

        total_users = users.count()
        self.stdout.write(self.style.SUCCESS(f"Generating reports for {total_users} users for {month_name}..."))

        sent_count = 0
        for user in users:
            try:
                month_name = end_date.strftime('%B %Y')
                subject = f"Your Monthly Financial Report - {month_name}"

                if EmailLog.objects.filter(user=user, subject=subject, status='SENT').exists():
                    continue

                report_data = self.get_report_data(user, start_date, end_date)
                if not report_data or not report_data.get('has_data'):
                    continue

                if options.get('test'):
                    self.stdout.write(f"--- Report Data for {user.username} ({start_date} to {end_date}) ---")
                    self.stdout.write(str(report_data))
                    continue

                html_message = render_to_string('emails/monthly_report.html', {
                    'user': user,
                    'start_date': start_date,
                    'end_date': end_date,
                    'data': report_data,
                    'currency_symbol': user.profile.currency if hasattr(user, 'profile') else '₹'
                })

                month_name = end_date.strftime('%B %Y')
                subject = f"Your Monthly Financial Report - {month_name}"

                send_mail(
                    subject=subject,
                    message="",
                    from_email=settings.DEFAULT_FROM_EMAIL,
                    recipient_list=[user.email],
                    html_message=html_message,
                    fail_silently=False
                )

                EmailLog.objects.create(
                    user=user,
                    to_email=user.email,
                    subject=subject,
                    body=subject,
                    html_body=html_message,
                    status='SENT'
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
        cb_rf_income = inc_qs.filter(source_type__in=['Cashback & Rewards', 'Refund / Reimbursement']).aggregate(Sum('base_amount'))['base_amount__sum'] or Decimal('0')
        savings_rate_denominator = total_income - cb_rf_income

        # Total regular expenses
        total_regular_expense = exp_qs.aggregate(Sum('base_amount'))['base_amount__sum'] or Decimal('0')

        # Include loan repayments in total monthly outflow/expense
        total_loan_repayments = LoanRepayment.objects.filter(
            loan__user=user, date__range=[start_date, end_date]
        ).aggregate(Sum('base_amount'))['base_amount__sum'] or Decimal('0')

        # Include capital events in total monthly outflow/expense
        total_cap_events = CapitalEvent.objects.filter(
            user=user, date__range=[start_date, end_date], is_deleted=False
        ).aggregate(Sum('base_amount'))['base_amount__sum'] or Decimal('0')

        total_expense = total_regular_expense + total_loan_repayments + total_cap_events

        if total_income == 0 and total_expense == 0:
            return {'has_data': False}

        savings = total_income - total_expense
        savings_rate = round((savings / savings_rate_denominator * 100), 1) if savings_rate_denominator > 0 else 0

        # 2. Top 4 Categories with percentage
        top_cats_raw = list(exp_qs.values('category').annotate(
            total=Sum('base_amount')
        ).order_by('-total')[:4])
        top_cat_total = sum(c['total'] for c in top_cats_raw) or Decimal('1')
        top_categories = [
            {**c, 'pct': round(float(c['total']) / float(top_cat_total) * 100)}
            for c in top_cats_raw
        ]

        # 3. Net Worth & Accounts Partitioning using LedgerReadService
        net_worth, account_base_balances = LedgerReadService.get_net_worth(user, as_of=end_date)
        nw_at_end = net_worth

        investment_codes = resolve_category_selector(['Investments'])
        acc_type_map = dict(user.accounts.filter(is_active=True).values_list('id', 'account_type'))
        total_investments = Decimal('0.00')
        credit_card_pending = Decimal('0.00')
        for pk, val in account_base_balances.items():
            atype = acc_type_map.get(pk, 'OTHER')
            if atype in investment_codes:
                total_investments += val
            if atype == 'CREDIT_CARD':
                credit_card_pending += abs(val)

        nw_at_start = nw_at_end - (total_income - total_expense)
        nw_change = nw_at_end - nw_at_start
        nw_change_pct = round((nw_change / nw_at_start * 100), 1) if nw_at_start > 0 else 0

        # 4. Capital Events
        capital_events_qs = CapitalEvent.objects.filter(
            user=user, date__range=[start_date, end_date], is_deleted=False
        ).order_by('-amount')
        capital_events = [
            {
                'label': f"{e.get_subtype_display()}: {e.note}" if e.note else e.get_subtype_display(),
                'date': e.date,
                'amount': e.base_amount,
            }
            for e in capital_events_qs
        ]

        # 5. AI Insight (Highlighted context)
        ai_insight = None
        if top_categories:
            top_cat = top_categories[0]
            top_pct = round(float(top_cat['total']) / float(total_expense) * 100) if total_expense > 0 else 0
            potential = float(top_cat['total']) * 0.15  # Suggest 15% saving

            ai_insight = _("You spent <b>{pct}%</b> of your total budget on <b>{cat}</b>. Reducing this by 15% next month could save you <b>{sym}{savings}</b>!").format(
                cat=escape(top_cat['category']),
                pct=top_pct,
                sym=currency_symbol,
                savings=compact_amount(potential, currency_symbol)
            )

        return {
            'has_data': True,
            'income': total_income,
            'expense': total_expense,
            'regular_expense': total_regular_expense,
            'loan_repayments': total_loan_repayments,
            'capital_events_total': total_cap_events,
            'savings': savings,
            'savings_rate': savings_rate,
            'total_investments': total_investments,
            'credit_card_pending': credit_card_pending,
            'top_categories': top_categories,
            'capital_events': capital_events,
            'nw_at_end': nw_at_end,
            'nw_change': nw_change,
            'nw_change_pct': nw_change_pct,
            'ai_insight': mark_safe(ai_insight) if ai_insight else None
        }
