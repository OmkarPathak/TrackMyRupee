from __future__ import annotations

import calendar
from datetime import date, timedelta
from decimal import Decimal

from django.utils import timezone

from .models import RecurringTransaction


class RecurringService:
    PERIOD_DAYS = {
        'DAILY': 1,
        'WEEKLY': 7,
        'BIWEEKLY': 14,
        'MONTHLY': 30,
        'QUARTERLY': 90,
        'SEMIANNUALLY': 180,
        'YEARLY': 365,
    }

    @staticmethod
    def calculate_interest_for_days(principal, annual_rate, days: int) -> Decimal:
        principal_dec = Decimal(str(principal or 0))
        annual_rate_dec = Decimal(str(annual_rate or 0))
        days_dec = Decimal(str(days or 0))
        if principal_dec <= 0 or annual_rate_dec <= 0 or days_dec <= 0:
            return Decimal('0.00')

        return (principal_dec * annual_rate_dec * days_dec / Decimal('36500')).quantize(Decimal('0.01'))

    @staticmethod
    def calculate_period_interest(principal, annual_rate, frequency='MONTHLY', days=None) -> Decimal:
        if days is not None:
            return RecurringService.calculate_interest_for_days(principal, annual_rate, days)
        period_days = RecurringService.PERIOD_DAYS.get(frequency, 30)
        return RecurringService.calculate_interest_for_days(principal, annual_rate, period_days)

    MONTH_STEPS = {'MONTHLY': 1, 'QUARTERLY': 3, 'SEMIANNUALLY': 6, 'YEARLY': 12}

    @staticmethod
    def last_due_before_today(start_date: date, frequency: str) -> date | None:
        """Return the most recent real occurrence on or before today.

        Occurrences follow the same calendar rules as the recurring engine
        (RecurringTransaction.get_next_date): day-based frequencies step by exact
        days, month-based ones keep the start day of month (clamped to month length),
        so the next due date derived from this value is always in the future.
        """
        today = timezone.localdate()
        if start_date > today:
            return None

        month_step = RecurringService.MONTH_STEPS.get(frequency)
        if month_step is None:
            step_days = RecurringService.PERIOD_DAYS.get(frequency, 30)
            periods = (today - start_date).days // step_days
            return start_date + timedelta(days=periods * step_days)

        def occurrence(n: int) -> date:
            months = start_date.month - 1 + n * month_step
            year, month = start_date.year + months // 12, months % 12 + 1
            return date(year, month, min(start_date.day, calendar.monthrange(year, month)[1]))

        months_elapsed = (today.year - start_date.year) * 12 + today.month - start_date.month
        periods = months_elapsed // month_step
        while periods > 0 and occurrence(periods) > today:
            periods -= 1
        return occurrence(periods)

    @staticmethod
    def make_recurring(
        user,
        transaction_type,
        amount,
        currency,
        account,
        description,
        frequency,
        start_date,
        *,
        loan=None,
        physical_asset=None,
        source=None,
        category=None,
        **kwargs,
    ) -> RecurringTransaction:
        recurring = RecurringTransaction.objects.filter(
            user=user,
            transaction_type=transaction_type,
            amount=amount,
            currency=currency,
            account=account,
            description=description,
            frequency=frequency,
            start_date=start_date,
            loan=loan,
            physical_asset=physical_asset,
            source=source,
            category=category,
            is_active=True,
        ).first()
        if recurring:
            return recurring

        recurring = RecurringTransaction(
            user=user,
            transaction_type=transaction_type,
            amount=amount,
            currency=currency,
            account=account,
            description=description,
            frequency=frequency,
            start_date=start_date,
            loan=loan,
            physical_asset=physical_asset,
            source=source,
            category=category,
            is_active=True,
            **kwargs,
        )
        if 'last_processed_date' not in kwargs:
            recurring.last_processed_date = None
        recurring.save()
        return recurring