from decimal import Decimal
from datetime import date
from django.utils.translation import gettext as _
from .services import SalaryAnalysisService
from .models import SavingsGoal
from .templatetags.digit_filters import compact_amount

class DailyPredictionsService:
    @staticmethod
    def get_predictions_context(
        user,
        today,
        net_worth,
        avg_monthly_savings,
        total_monthly_budget,
        month_spent_so_far,
        days_in_current_month,
        currency_symbol,
        salary_cycle_active,
        salary_cycle_start,
        salary_cycle_end
    ):
        """
        Calculates runway, velocity, and milestone targets for the daily mode dashboard.
        """
        # 1. Salary runway calculation
        if salary_cycle_active:
            metrics = SalaryAnalysisService.calculate_salary_cycle_metrics(user, today)
            cycle_income = metrics['total_income']
            cycle_expenses = metrics['total_expenses']
            cycle_loans = metrics['total_loan_principal']
            if cycle_income > 0:
                remaining_in_cycle = Decimal(str(cycle_income)) - Decimal(str(cycle_expenses)) - Decimal(str(cycle_loans))
                cycle_days_left = max(0, (salary_cycle_end - today).days)
                cycle_days_passed = max(1, (today - salary_cycle_start).days + 1)
                cycle_total_days = (salary_cycle_end - salary_cycle_start).days + 1
                cycle_expenses_val = Decimal(str(cycle_expenses))
            else:
                remaining_in_cycle = Decimal(str(total_monthly_budget)) - month_spent_so_far
                cycle_days_left = max(0, (date(today.year, today.month, days_in_current_month) - today).days)
                cycle_days_passed = today.day
                cycle_total_days = days_in_current_month
                cycle_expenses_val = month_spent_so_far
        else:
            remaining_in_cycle = Decimal(str(total_monthly_budget)) - month_spent_so_far
            cycle_days_left = max(0, (date(today.year, today.month, days_in_current_month) - today).days)
            cycle_days_passed = today.day
            cycle_total_days = days_in_current_month
            cycle_expenses_val = month_spent_so_far

        cycle_daily_burn = cycle_expenses_val / cycle_days_passed if cycle_days_passed > 0 else Decimal('0.00')
        if cycle_daily_burn > 0 and remaining_in_cycle > 0:
            runway_days = float(remaining_in_cycle / cycle_daily_burn)
            runs_out_day = float(cycle_days_passed) + runway_days
            runs_out_day_rounded = int(round(runs_out_day))
            
            if runway_days >= cycle_days_left:
                runway_status = 'on_track'
            else:
                runway_status = 'running_short'
        elif remaining_in_cycle <= 0:
            runway_status = 'running_short'
            runs_out_day_rounded = cycle_days_passed
        else:
            runway_status = 'on_track'
            runs_out_day_rounded = cycle_total_days

        # 2. Net worth velocity formatting
        velocity_is_positive = avg_monthly_savings >= 0
        sign = "+" if velocity_is_positive else "-"
        abs_velocity = abs(avg_monthly_savings)
        if abs_velocity >= 10000000:
            velocity_compact = f"{abs_velocity / 10000000:.1f}Cr".replace('.0Cr', 'Cr')
        elif abs_velocity >= 100000:
            velocity_compact = f"{abs_velocity / 100000:.1f}L".replace('.0L', 'L')
        elif abs_velocity >= 1000:
            velocity_compact = f"{abs_velocity / 1000:.1f}K".replace('.0K', 'K')
        else:
            velocity_compact = f"{abs_velocity:.0f}"
        
        velocity_formatted = f"{sign}{currency_symbol}{velocity_compact} / mo"

        # 3. Next milestone prediction
        def get_next_net_worth_milestone(current_nw):
            if current_nw <= 0:
                return 100000
            if str(currency_symbol).upper() in ['INR', '₹']:
                milestones = [
                    10000, 25000, 50000, 100000, 200000, 500000, 1000000, 1500000,
                    2000000, 2500000, 3000000, 5000000, 7500000, 10000000, 20000000, 50000000, 100000000
                ]
            else:
                milestones = [
                    1000, 2500, 5000, 10000, 25000, 50000, 100000, 250000, 500000, 
                    1000000, 2500000, 5000000, 10000000
                ]
            for m in milestones:
                if float(current_nw) < m:
                    return m
            return int((float(current_nw) // 10000000 + 1) * 10000000)

        active_goal = SavingsGoal.objects.filter(user=user, is_completed=False).order_by('id').first()
        if active_goal:
            next_target = float(active_goal.target_amount)
            target_name = active_goal.name
        else:
            next_target = float(get_next_net_worth_milestone(net_worth))
            target_name = None

        diff_to_target = next_target - float(net_worth)
        if avg_monthly_savings > 0 and diff_to_target > 0:
            months_to_hit = int(round(diff_to_target / float(avg_monthly_savings)))
        else:
            months_to_hit = None

        target_formatted = compact_amount(next_target, currency_symbol)

        return {
            'runway': {
                'days_left': cycle_days_left,
                'remaining_in_cycle': round(remaining_in_cycle, 0),
                'status': runway_status,
                'runs_out_day': runs_out_day_rounded,
            },
            'velocity': {
                'formatted': velocity_formatted,
                'is_positive': velocity_is_positive,
                'months_to_hit': months_to_hit,
                'target_formatted': target_formatted,
                'target_name': target_name,
            },
            'net_worth_compact': compact_amount(net_worth, currency_symbol),
        }
