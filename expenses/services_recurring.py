from __future__ import annotations

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
    def calculate_period_interest(principal, annual_rate, frequency='MONTHLY') -> Decimal:
        principal_dec = Decimal(str(principal or 0))
        annual_rate_dec = Decimal(str(annual_rate or 0))
        if principal_dec <= 0 or annual_rate_dec <= 0:
            return Decimal('0.00')

        period_days = Decimal(str(RecurringService.PERIOD_DAYS.get(frequency, 30)))
        return (principal_dec * annual_rate_dec * period_days / Decimal('36500')).quantize(Decimal('0.01'))

    @staticmethod
    def last_due_before_today(start_date: date, frequency: str) -> date | None:
        """Return the most recent due date on or before today.

        Note: MONTHLY and YEARLY periods use fixed-day approximations (30 and
        365 days respectively), so results may drift slightly for long-running
        schedules compared to a true calendar-month calculation.
        """
        today = timezone.localdate()
        if start_date > today:
            return None

        # O(1) calculation — avoids looping for old start dates.
        step_days = RecurringService.PERIOD_DAYS.get(frequency, 30)
        delta_days = (today - start_date).days
        periods = delta_days // step_days
        return start_date + timedelta(days=periods * step_days)

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
        last_due = RecurringService.last_due_before_today(start_date, frequency)
        if last_due:
            recurring.last_processed_date = last_due
        recurring.save()
        return recurring