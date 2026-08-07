import calendar
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.db.models import F, Sum
from django.db.models.functions import Coalesce, TruncMonth
from django.utils import timezone

import logging
from .models import CapitalEvent, Expense, Income, Loan, LoanRepayment

logger = logging.getLogger(__name__)


def _month_start_offset(today, months):
    """Return the first day of the month that is `months` calendar months before today.
    Uses proper calendar arithmetic instead of approximating months as 30 days."""
    year = today.year
    month = today.month - months
    while month <= 0:
        month += 12
        year -= 1
    return today.replace(year=year, month=month, day=1)


class FinancialService:
    @staticmethod
    def get_monthly_history(user, months=6):
        """
        Returns a list of monthly income and expense totals for the last N months.
        """
        today = timezone.now().date()
        history = []
        
        # We can optimize this by getting all data in 2 queries and grouping in Python,
        # or using TruncMonth. Let's use TruncMonth for robustness.
        
        start_date = _month_start_offset(today, months - 1)
        
        income_qs = Income.objects.filter(
            user=user, date__gte=start_date, date__lte=today
        ).annotate(month=TruncMonth('date')).values('month').annotate(total=Sum('base_amount'))
        
        expense_qs = Expense.objects.filter(
            user=user, date__gte=start_date, date__lte=today
        ).annotate(month=TruncMonth('date')).values('month').annotate(total=Sum('base_amount'))

        # Include Loan Repayment interest as expense
        
        loan_repayment_qs = LoanRepayment.objects.filter(
            loan__user=user, date__gte=start_date, date__lte=today
        ).annotate(month=TruncMonth('date')).values('month').annotate(
            total_interest=Sum(F('interest_portion') * F('exchange_rate')),
            total_emi=Sum('base_amount')
        )
        
        income_map = {item['month'].date() if hasattr(item['month'], 'date') else item['month']: item['total'] for item in income_qs}
        expense_map = {item['month'].date() if hasattr(item['month'], 'date') else item['month']: item['total'] for item in expense_qs}
        loan_interest_map = {item['month'].date() if hasattr(item['month'], 'date') else item['month']: item['total_interest'] for item in loan_repayment_qs}
        loan_emi_map = {item['month'].date() if hasattr(item['month'], 'date') else item['month']: item['total_emi'] for item in loan_repayment_qs}
        
        curr = start_date
        for _ in range(months):
            inc = float(income_map.get(curr, 0))
            exp = float(expense_map.get(curr, 0)) + float(loan_interest_map.get(curr, 0))
            emi = float(loan_emi_map.get(curr, 0))
            
            # Savings = Income - Expenses (including interest) - Principal portion
            # Which is same as Income - (Expenses + Principal portion) = Income - (Expenses without interest + EMI)
            # Actually, Income - Expense - EMI_Principal = Income - (Expense_with_interest) - EMI_Principal
            
            history.append({
                'month': curr,
                'income': inc,
                'expense': exp,
                'savings': inc - exp - (emi - float(loan_interest_map.get(curr, 0))) # EMI - Interest = Principal
            })
            # Move to next month
            if curr.month == 12:
                curr = curr.replace(year=curr.year + 1, month=1)
            else:
                curr = curr.replace(month=curr.month + 1)
                
        return history

    @staticmethod
    def get_categorical_spending(user, year, month):
        """
        Returns spending breakdown by category for a specific month.
        """
        return Expense.objects.filter(
            user=user, date__year=year, date__month=month
        ).values('category').annotate(
            total=Sum('base_amount')
        ).order_by('-total')

    @staticmethod
    def get_spending_streak(user, daily_budget_allowed, days=3):
        """
        Returns the number of consecutive days (up to `days`) the user has overspent.
        """
        if daily_budget_allowed <= 0:
            return 0
            
        today = timezone.now().date()
        start_date = today - timedelta(days=days - 1)
        
        daily_spend = Expense.objects.filter(
            user=user, date__gte=start_date, date__lte=today
        ).values('date').annotate(total=Sum('base_amount'))
        
        spend_map = {item['date']: item['total'] for item in daily_spend}
        
        streak = 0
        for i in range(days):
            d = today - timedelta(days=i)
            if float(spend_map.get(d, 0)) > float(daily_budget_allowed):
                streak += 1
            else:
                break
        return streak

    @staticmethod
    def get_historical_average(user, months=3):
        """
        Returns average monthly income and expense for the last N completed months.
        """
        today = timezone.now().date()
        # Start from the previous month
        start_date = _month_start_offset(today, months)
        end_date = today.replace(day=1) - timedelta(days=1)
        
        agg = Expense.objects.filter(
            user=user, date__gte=start_date, date__lte=end_date
        ).aggregate(
            total=Sum('base_amount')
        )

        loan_interest_agg = LoanRepayment.objects.filter(
            loan__user=user, date__gte=start_date, date__lte=end_date
        ).aggregate(
            total_interest=Sum(F('interest_portion') * F('exchange_rate'))
        )
        
        income_agg = Income.objects.filter(
            user=user, date__gte=start_date, date__lte=end_date
        ).aggregate(
            total=Sum('base_amount')
        )
        
        total_expense = float(agg['total'] or 0) + float(loan_interest_agg['total_interest'] or 0)

        return {
            'avg_income': float(income_agg['total'] or 0) / months,
            'avg_expense': total_expense / months
        }

    @staticmethod
    def get_consistency_metrics(user, months=10):
        """
        Returns the number of months with positive savings out of the last N months.
        """
        today = timezone.now().date()
        
        start_date = _month_start_offset(today, months)
        
        # This is a bit complex in one query if we want to join income and expense.
        # It's cleaner to get both lists and compare in Python.
        history = FinancialService.get_monthly_history(user, months + 1)
        # Exclude current month
        history = [m for m in history if m['month'] < today.replace(day=1)]
        
        positive_savings_count = sum(1 for m in history if m['savings'] > 0 and m['income'] > 0)
        return {
            'positive_savings_count': positive_savings_count,
            'total_months': len(history)
        }

    @staticmethod
    def get_cumulative_net_worth_history(user, current_net_worth, months=6):
        """
        Returns a list of cumulative net worth values for the last N months.
        Uses a 'burn-back' approach from the current net worth.
        """
        history = FinancialService.get_monthly_history(user, months)
        # Reverse to burn back from newest to oldest
        history.reverse()
        
        cumulative_history = []
        running_nw = float(current_net_worth)
        
        # history[0] is the current month
        for i, m in enumerate(history):
            cumulative_history.append(running_nw)
            # To get previous month's value, subtract current month's savings
            running_nw -= m['savings']
            
        # Reverse back so it's oldest to newest
        cumulative_history.reverse()
        return cumulative_history

class LoanService:
    @staticmethod
    def calculate_emi(principal, annual_rate, months):
        """
        Standard EMI formula: E = P * r * (1 + r)^n / ((1 + r)^n - 1)
        r = monthly interest rate (annual_rate / 12 / 100)
        """
        principal_dec = Decimal(str(principal or 0))
        annual_rate_dec = Decimal(str(annual_rate or 0))

        if principal_dec <= 0 or months <= 0:
            return 0.0
        if annual_rate_dec == 0:
            return float(principal_dec / Decimal(months))

        monthly_rate = annual_rate_dec / Decimal('12') / Decimal('100')
        n = int(months)
        one_plus_r_pow_n = (Decimal('1') + monthly_rate) ** n
        emi = principal_dec * monthly_rate * one_plus_r_pow_n / (one_plus_r_pow_n - Decimal('1'))
        return float(emi.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP))

    @staticmethod
    def get_total_liabilities(user):
        """
        Returns the sum of remaining principal for all active loans.
        Mirrors get_loan_summary's formula: subtracts both EMI-based principal
        repayments AND any lump-sum capital-event prepayments (down payments,
        prepayments) so that net worth / total debt display is always consistent.
        """
        active_loans = Loan.objects.filter(user=user, is_active=True)
        total = sum(loan.remaining_principal for loan in active_loans)
        return float(total)

    @staticmethod
    def sync_loan_active_status(loan):
        """
        Auto-deactivates loan when remaining principal reaches 0 or less.
        Re-activates if remaining principal > 0.
        Returns the updated is_active boolean.
        """
        rem = loan.remaining_principal
        should_be_active = rem > Decimal('0.00')
        if loan.is_active != should_be_active:
            loan.is_active = should_be_active
            loan.save(update_fields=['is_active', 'updated_at'])
        return loan.is_active

    @staticmethod
    def get_loan_summary(loan):
        """
        Calculates total paid and remaining principal based on actual repayments
        and any capital event prepayments linked to the loan.
        Auto-syncs active status based on remaining principal.
        """
        LoanService.sync_loan_active_status(loan)

        repayments = loan.repayments.aggregate(
            total_principal=Sum('principal_portion'),
            total_interest=Sum('interest_portion'),
            total_amount=Sum('amount')
        )

        principal_paid = repayments['total_principal'] or Decimal('0.00')
        interest_paid = repayments['total_interest'] or Decimal('0.00')
        total_paid = repayments['total_amount'] or Decimal('0.00')

        # Factor in lump-sum payments recorded as CapitalEvents (down payments and prepayments)
        capital_prepaid = (
            CapitalEvent.objects
            .filter(linked_loan=loan, subtype__in=['loan_down_payment', 'loan_prepayment'])
            .aggregate(total=Sum('amount'))['total']
        ) or Decimal('0.00')

        remaining_principal = loan.remaining_principal

        return {
            'principal_paid': float(principal_paid),
            'capital_prepaid': float(capital_prepaid),
            'interest_paid': float(interest_paid),
            'total_paid': float(total_paid),
            'remaining_principal': float(remaining_principal)
        }

    @staticmethod
    def generate_amortization_schedule(loan):
        """
        Generates the planned amortization schedule from today until the end of the loan,
        taking into account current remaining principal and latest interest rate.
        """
        summary = LoanService.get_loan_summary(loan)
        remaining_principal = Decimal(str(summary['remaining_principal']))
        
        # Get latest interest rate
        latest_rate_obj = loan.interest_rates.order_by('-effective_date').first()
        annual_rate = Decimal(str(latest_rate_obj.interest_rate)) if latest_rate_obj else Decimal('0.00')
        
        # Approximate remaining months based on start date and duration
        # Or better, just calculate how many months of EMI are left based on remaining principal

        def _add_months(d, months):
            year = d.year + (d.month - 1 + months) // 12
            month = (d.month - 1 + months) % 12 + 1
            day = min(d.day, calendar.monthrange(year, month)[1])
            return date(year, month, day)
        
        today = date.today()
        # Find how many months have passed since start
        months_passed = (today.year - loan.start_date.year) * 12 + today.month - loan.start_date.month
        remaining_months = loan.duration_months - months_passed
        
        if remaining_months <= 0 and remaining_principal > 0:
            # If past term but still has balance, maybe they missed payments. Just use 1 month to clear it or recalculate.
            # Let's just assume remaining balance is paid in 1 final payment for the schedule display.
            remaining_months = 1
            
        if remaining_months <= 0 or remaining_principal <= 0:
            return []

        emi = Decimal(str(LoanService.calculate_emi(remaining_principal, annual_rate, remaining_months)))
        
        schedule = []
        current_date = _add_months(loan.start_date, max(0, months_passed))
        balance = remaining_principal
        
        r = annual_rate / Decimal('12') / Decimal('100')
        
        for i in range(remaining_months):
            if balance <= 0:
                break
                
            interest_payment = Decimal(str(balance)) * r
            principal_payment = emi - interest_payment
            
            # Adjust final payment
            if principal_payment > balance:
                principal_payment = balance
                emi = principal_payment + interest_payment
                
            balance = Decimal(str(balance)) - principal_payment
            
            schedule.append({
                'month': current_date.strftime('%b %Y'),
                'date': current_date,
                'emi': float(emi.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)),
                'principal': float(principal_payment.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)),
                'interest': float(interest_payment.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)),
                'balance': float(abs(balance).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP))
            })
            
            current_date = _add_months(current_date, 1)
            
        return schedule

    @staticmethod
    def calculate_extra_emi_savings(loan):
        """
        Calculates interest saved and months reduced by paying one extra EMI per year
        applied entirely toward principal reduction.
        """
        summary = LoanService.get_loan_summary(loan)
        remaining_principal = Decimal(str(summary['remaining_principal']))

        if remaining_principal <= 0:
            return None

        latest_rate_obj = loan.interest_rates.order_by('-effective_date').first()
        annual_rate = Decimal(str(latest_rate_obj.interest_rate)) if latest_rate_obj else Decimal('0.00')

        from datetime import date
        today = date.today()
        months_passed = (today.year - loan.start_date.year) * 12 + today.month - loan.start_date.month
        remaining_months = loan.duration_months - months_passed

        if remaining_months <= 0:
            return None

        r = annual_rate / Decimal('12') / Decimal('100')
        emi = Decimal(str(LoanService.calculate_emi(remaining_principal, annual_rate, remaining_months)))

        def _simulate(balance, emi, r, with_extra_emi):
            total_interest = Decimal('0')
            month = 0
            while balance > Decimal('0.01') and month < 1200:  # cap at 100 years
                month += 1
                interest_payment = balance * r
                principal_payment = emi - interest_payment
                if principal_payment <= 0:
                    # EMI no longer covers the interest — loan can't amortise at this rate.
                    # This should never trigger since EMI is freshly calculated for the
                    # remaining term, but log a warning if it does so it's visible.
                    logger.warning(
                        "_simulate: principal_payment <= 0 (interest=%.4f, emi=%.4f, balance=%.4f). "
                        "Loan cannot amortise; breaking early and returning understated savings.",
                        float(interest_payment), float(emi), float(balance),
                    )
                    break
                if principal_payment > balance:
                    principal_payment = balance
                total_interest += interest_payment
                balance -= principal_payment
                # Apply one extra EMI as pure principal at the end of each 12-month cycle
                if with_extra_emi and month % 12 == 0 and balance > 0:
                    extra = min(emi, balance)
                    balance -= extra
            return month, float(total_interest.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP))

        normal_months, normal_interest = _simulate(remaining_principal, emi, r, with_extra_emi=False)
        extra_months, extra_interest = _simulate(remaining_principal, emi, r, with_extra_emi=True)

        months_saved = normal_months - extra_months
        interest_saved = normal_interest - extra_interest

        return {
            'emi': float(emi.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)),
            'normal_months': normal_months,
            'extra_months': extra_months,
            'months_saved': months_saved,
            'years_saved': round(months_saved / 12, 1),
            'interest_saved': round(interest_saved, 2),
            'normal_interest': round(normal_interest, 2),
        }


class SalaryAnalysisService:
    """Service for calculating financial metrics based on salary cycles."""
    
    @staticmethod
    def get_salary_cycle_dates(user, target_date):
        """
        Get the start and end dates of a salary cycle containing the target_date.
        
        Args:
            user: User instance
            target_date: The date to find the salary cycle for (date object)
            
        Returns:
            Tuple of (cycle_start_date, cycle_end_date)
            
        Example:
            If salary_date=15 and target_date=2026-05-25, returns (2026-05-15, 2026-06-14)
        """
        user_profile = user.profile
        salary_date = user_profile.salary_date
        
        # Handle day 31 in months with fewer days
        def get_safe_date(year, month, day):
            """Get the last day of month if day exceeds month length."""
            last_day = calendar.monthrange(year, month)[1]
            return date(year, month, min(day, last_day))
        
        # Determine which month's salary cycle contains target_date
        if target_date.day >= salary_date:
            # Salary cycle starts in current month
            cycle_start = get_safe_date(target_date.year, target_date.month, salary_date)
            
            # Next cycle starts next month
            if target_date.month == 12:
                cycle_end = get_safe_date(target_date.year + 1, 1, salary_date) - timedelta(days=1)
            else:
                cycle_end = get_safe_date(target_date.year, target_date.month + 1, salary_date) - timedelta(days=1)
        else:
            # Salary cycle started in previous month
            if target_date.month == 1:
                cycle_start = get_safe_date(target_date.year - 1, 12, salary_date)
            else:
                cycle_start = get_safe_date(target_date.year, target_date.month - 1, salary_date)
            
            # Current month's salary date is the end
            cycle_end = get_safe_date(target_date.year, target_date.month, salary_date) - timedelta(days=1)
        
        return cycle_start, cycle_end
    
    @staticmethod
    def get_transactions_in_salary_cycle(user, target_date):
        """
        Get all income and expense transactions within the salary cycle.
        
        Returns:
            Dict with 'income' and 'expenses' QuerySet lists
        """
        cycle_start, cycle_end = SalaryAnalysisService.get_salary_cycle_dates(user, target_date)
        
        income_list = Income.objects.filter(
            user=user,
            date__gte=cycle_start,
            date__lte=cycle_end
        ).order_by('date')
        
        expense_list = Expense.objects.filter(
            user=user,
            date__gte=cycle_start,
            date__lte=cycle_end
        ).order_by('date')
        
        return {
            'cycle_start': cycle_start,
            'cycle_end': cycle_end,
            'income': income_list,
            'expenses': expense_list,
        }
    
    @staticmethod
    def calculate_salary_cycle_metrics(user, target_date):
        """
        Calculate income, expenses, and savings for a salary cycle.
        
        Returns:
            Dict with 'income', 'expenses', 'savings', 'daily_burn', 'savings_rate'
        """
        transactions = SalaryAnalysisService.get_transactions_in_salary_cycle(user, target_date)
        
        cycle_start = transactions['cycle_start']
        cycle_end = transactions['cycle_end']
        income_list = transactions['income']
        expense_list = transactions['expenses']
        
        # Calculate totals in base currency
        total_income = Decimal('0')
        savings_rate_denominator = Decimal('0')
        for inc in income_list:
            total_income += inc.base_amount or Decimal('0')
            if inc.source_type not in ['Cashback & Rewards', 'Refund / Reimbursement']:
                savings_rate_denominator += inc.base_amount or Decimal('0')
        
        total_expenses = Decimal('0')
        for exp in expense_list:
            total_expenses += exp.base_amount or Decimal('0')
        
        # Calculate loan repayment amount in this cycle
        total_loan_principal = Decimal('0')
        loan_repayments = LoanRepayment.objects.filter(
            loan__user=user,
            date__gte=cycle_start,
            date__lte=cycle_end
        )
        for repay in loan_repayments:
            # Principal = EMI - Interest, adjusted for exchange rate
            principal = (repay.base_amount or Decimal('0')) - (repay.interest_portion or Decimal('0')) * (repay.exchange_rate or Decimal('1'))
            total_loan_principal += principal
        
        # Calculate savings = Income - Expenses - Loan Principal
        savings = total_income - total_expenses - total_loan_principal
        # Include loan interest in expenses for savings calculation
        total_loan_interest = Decimal('0')
        for repay in loan_repayments:
            interest = (repay.interest_portion or Decimal('0')) * (repay.exchange_rate or Decimal('1'))
            total_loan_interest += interest
        
        # Savings = Income - Expenses (including interest) - Principal
        total_expenses_with_interest = total_expenses + total_loan_interest
        savings = total_income - total_expenses_with_interest - total_loan_principal
        
        # Calculate daily burn
        num_days = (cycle_end - cycle_start).days + 1
        daily_burn = total_expenses / num_days if num_days > 0 else Decimal('0')
        
        # Calculate savings rate
        savings_rate = (savings / savings_rate_denominator * 100) if savings_rate_denominator > 0 else Decimal('0')
        
        return {
            'cycle_start': cycle_start,
            'cycle_end': cycle_end,
            'total_income': float(total_income),
            'total_expenses': float(total_expenses),
            'total_loan_principal': float(total_loan_principal),
            'savings': float(savings),
            'daily_burn': float(daily_burn.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)),
            'savings_rate': float(savings_rate.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)),
            'num_days': num_days,
        }

