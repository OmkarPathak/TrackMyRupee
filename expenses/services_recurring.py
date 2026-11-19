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

    # A deposit's payout setting (Account.deposit_payout) -> the recurring schedule that records it, and payouts a year.
    PAYOUT_FREQUENCY = {'MONTHLY': 'MONTHLY', 'QUARTERLY': 'QUARTERLY', 'ANNUAL': 'YEARLY'}
    PAYOUTS_PER_YEAR = {'MONTHLY': 12, 'QUARTERLY': 4, 'ANNUAL': 1}

    @staticmethod
    def payout_amount(principal, annual_rate, payout: str) -> Decimal:
        """One payout of a deposit that pays its interest out: simple interest, split evenly over the year."""
        per_year = RecurringService.PAYOUTS_PER_YEAR.get(payout)
        principal_dec = Decimal(str(principal or 0))
        rate_dec = Decimal(str(annual_rate or 0))
        if not per_year or principal_dec <= 0 or rate_dec <= 0:
            return Decimal('0.00')
        return (principal_dec * rate_dec / Decimal('100') / Decimal(per_year)).quantize(Decimal('0.01'))

    @staticmethod
    def first_payout_date(start_date: date, payout: str) -> date:
        """The first payout falls one period after the deposit is booked."""
        return RecurringTransaction.get_next_date(start_date, RecurringService.PAYOUT_FREQUENCY[payout], start_date)

    @staticmethod
    def sync_deposit_payouts(account, payout_changed: bool = False, previous: dict | None = None) -> None:
        """Make a deposit's payout schedules follow the deposit.

        The schedules are the interest income the FD setup creates and, when the payouts go into a recurring deposit
        (an RD), the monthly transfer that funds it. They end on the day the deposit is closed, or else on its
        maturity date (a transfer also stops before its RD matures), so closing early or renewing for longer moves them.
        A deposit that no longer pays out stops them (the RD transfer also stops unless the payouts stay monthly).
        One that starts paying out again, or whose end moved past today after a schedule had run out, starts it again.

        `previous` ({'principal', 'rate', 'payout'}, as they were before the edit) lets an amount that was worked out
        from the rate follow the principal, the rate and the payout period when they change; an amount the person
        typed is left alone. Payouts that fell due before the end are still recorded the next time recurring items
        are processed."""
        today = timezone.localdate()
        end = account.deposit_closed_date or account.deposit_maturity_date
        payout = account.deposit_payout
        new_auto = RecurringService.payout_amount(account.deposit_principal, account.deposit_rate, payout) if payout else Decimal('0.00')
        old_auto = None
        if previous and previous.get('payout'):
            old_auto = RecurringService.payout_amount(previous.get('principal'), previous.get('rate'), previous['payout'])

        for schedule in account.payout_schedules.select_related('to_account'):
            changed = []
            into_rd = schedule.transaction_type == 'TRANSFER'
            if not payout or (into_rd and payout != 'MONTHLY'):
                if schedule.is_active:
                    schedule.is_active = False
                    changed.append('is_active')
                if changed:
                    schedule.save(update_fields=changed)
                continue

            schedule_end = end
            if into_rd and schedule.to_account_id and schedule.to_account.deposit_maturity_date:
                rd_last_day = schedule.to_account.deposit_maturity_date - timedelta(days=1)
                schedule_end = min(end, rd_last_day) if end else rd_last_day

            was_over = schedule.end_date is not None and schedule.end_date < today
            if schedule.end_date != schedule_end:
                schedule.end_date = schedule_end
                changed.append('end_date')
            comes_back = payout_changed or (was_over and (schedule_end is None or schedule_end >= today))
            if comes_back and not schedule.is_active and (schedule_end is None or schedule_end >= today):
                schedule.is_active = True
                changed.append('is_active')

            frequency = RecurringService.PAYOUT_FREQUENCY[payout]
            if not into_rd and schedule.frequency != frequency:
                schedule.frequency = frequency
                changed.append('frequency')
                if new_auto > 0:                        # (the old figure was for a different period)
                    schedule.amount = new_auto
                    changed.append('amount')
            elif old_auto is not None and new_auto > 0 and old_auto != new_auto and schedule.amount == old_auto:
                schedule.amount = new_auto
                changed.append('amount')
            if 'amount' in changed and into_rd and schedule.to_account_id and schedule.to_account.rd_installment_amount is not None:
                schedule.to_account.rd_installment_amount = schedule.amount
                schedule.to_account.save(update_fields=['rd_installment_amount'])
            if changed:
                schedule.save(update_fields=changed)

    MONTH_STEPS = {'MONTHLY': 1, 'QUARTERLY': 3, 'SEMIANNUALLY': 6, 'YEARLY': 12}

    @staticmethod
    def last_due_before_today(start_date: date, frequency: str, is_last_day_of_month: bool = False,
                              is_last_working_day: bool = False) -> date | None:
        """Return the most recent real occurrence on or before today.

        Occurrences follow the same calendar rules as the recurring engine
        (RecurringTransaction.get_next_date): day-based frequencies step by exact
        days, month-based ones keep the start day of month (clamped to month length),
        so the next due date derived from this value is always in the future.
        """
        today = timezone.localdate()
        if start_date > today:
            return None

        if frequency == 'MONTHLY' and (is_last_day_of_month or is_last_working_day):
            # Month-end schedules do not follow the start day: walk the real occurrences.
            nxt = RecurringTransaction.get_next_date(
                start_date - timedelta(days=1), frequency, start_date, is_last_day_of_month, is_last_working_day)
            if nxt > today:
                return None
            while True:
                after = RecurringTransaction.get_next_date(nxt, frequency, start_date, is_last_day_of_month, is_last_working_day)
                if after > today:
                    return nxt
                nxt = after

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