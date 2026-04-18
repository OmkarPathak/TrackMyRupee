from datetime import date, timedelta
import calendar
from django.db.models import Sum, Avg, Count
from django.utils import timezone
from .models import Expense, Income, Category

class FinancialService:
    @staticmethod
    def get_monthly_history(user, months=6, is_family_mode=False):
        """
        Returns a list of monthly income and expense totals for the last N months.
        """
        today = timezone.now().date()
        history = []
        
        # We can optimize this by getting all data in 2 queries and grouping in Python,
        # or using TruncMonth. Let's use TruncMonth for robustness.
        from django.db.models.functions import TruncMonth
        
        start_date = (today.replace(day=1) - timedelta(days=30 * (months - 1))).replace(day=1)
        
        income_base = Income.objects.for_user(user) if is_family_mode else Income.objects.filter(user=user)
        income_qs = income_base.filter(
            date__gte=start_date, date__lte=today
        ).annotate(month=TruncMonth('date')).values('month').annotate(total=Sum('base_amount'))
        
        expense_base = Expense.objects.for_user(user) if is_family_mode else Expense.objects.filter(user=user)
        expense_qs = expense_base.filter(
            date__gte=start_date, date__lte=today
        ).annotate(month=TruncMonth('date')).values('month').annotate(total=Sum('base_amount'))
        
        income_map = {item['month'].date() if hasattr(item['month'], 'date') else item['month']: item['total'] for item in income_qs}
        expense_map = {item['month'].date() if hasattr(item['month'], 'date') else item['month']: item['total'] for item in expense_qs}
        
        curr = start_date
        for _ in range(months):
            history.append({
                'month': curr,
                'income': float(income_map.get(curr, 0)),
                'expense': float(expense_map.get(curr, 0)),
                'savings': float(income_map.get(curr, 0) - expense_map.get(curr, 0))
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
        start_date = (today.replace(day=1) - timedelta(days=30 * months)).replace(day=1)
        end_date = today.replace(day=1) - timedelta(days=1)
        
        agg = Expense.objects.filter(
            user=user, date__gte=start_date, date__lte=end_date
        ).aggregate(
            total=Sum('base_amount')
        )
        
        income_agg = Income.objects.filter(
            user=user, date__gte=start_date, date__lte=end_date
        ).aggregate(
            total=Sum('base_amount')
        )
        
        return {
            'avg_income': float(income_agg['total'] or 0) / months,
            'avg_expense': float(agg['total'] or 0) / months
        }

    @staticmethod
    def get_consistency_metrics(user, months=10):
        """
        Returns the number of months with positive savings out of the last N months.
        """
        today = timezone.now().date()
        from django.db.models.functions import TruncMonth
        from django.db.models import Case, When, F, Value, IntegerField
        
        start_date = (today.replace(day=1) - timedelta(days=30 * months)).replace(day=1)
        
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
