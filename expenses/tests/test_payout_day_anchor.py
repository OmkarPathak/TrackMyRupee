"""A deposit booked on the 29th, 30th or 31st keeps paying on that day of the month (the 30th or 28th in a short month,
then back to the 31st), instead of settling on the 28th after its first short month."""
from datetime import date
from unittest.mock import patch

from expenses.flows.registry import FlowRegistry
from expenses.models import Account, Income, RecurringTransaction, Transfer
from expenses.tests.test_flows_end_to_end import FlowTestBase, valid_payloads
from expenses.views.mixins import process_user_recurring_transactions


class Base(FlowTestBase):
    def run_fd(self, start, maturity, payout='MONTHLY', historical=True, **extra):
        payload = dict(valid_payloads(self)['fd'], deposit_start_date=start.isoformat(), maturity_date=maturity.isoformat(),
                       interest_payout=payout, payout_amount='1000', **extra)
        if historical:
            payload['create_historical_entries'] = 'on'
        form = FlowRegistry.get('fd').form_class(data=payload, user=self.user)
        self.assertTrue(form.is_valid(), dict(form.errors))
        return FlowRegistry.get('fd').commit(self.user, form.cleaned_data, __import__('uuid').uuid4())

    def payout_dates(self):
        return sorted(Income.objects.filter(user=self.user, source='Interest Income').values_list('date', flat=True))

    def process_on(self, today):
        with patch('django.utils.timezone.localdate', return_value=today), \
             patch('django.utils.timezone.localtime') as localtime:
            localtime.return_value.date.return_value = today
            process_user_recurring_transactions(self.user, force=True)


class MonthlyTests(Base):
    def test_a_deposit_booked_on_the_31st_pays_on_the_last_day_of_every_month(self):
        self.run_fd(date(2025, 1, 31), date(2026, 1, 31))
        self.process_on(date(2026, 2, 1))
        self.assertEqual(self.payout_dates(), [
            date(2025, 2, 28), date(2025, 3, 31), date(2025, 4, 30), date(2025, 5, 31), date(2025, 6, 30), date(2025, 7, 31),
            date(2025, 8, 31), date(2025, 9, 30), date(2025, 10, 31), date(2025, 11, 30), date(2025, 12, 31), date(2026, 1, 31)])

    def test_a_deposit_booked_on_the_30th_pays_on_the_30th_and_the_28th_in_february(self):
        self.run_fd(date(2025, 1, 30), date(2025, 6, 30))
        self.process_on(date(2025, 7, 1))
        self.assertEqual(self.payout_dates(), [date(2025, 2, 28), date(2025, 3, 30), date(2025, 4, 30), date(2025, 5, 30), date(2025, 6, 30)])

    def test_a_deposit_booked_on_the_29th_in_a_leap_year_pays_on_the_29th(self):
        self.run_fd(date(2024, 1, 29), date(2024, 5, 29))
        self.process_on(date(2024, 6, 1))
        self.assertEqual(self.payout_dates(), [date(2024, 2, 29), date(2024, 3, 29), date(2024, 4, 29), date(2024, 5, 29)])

    def test_a_deposit_booked_on_the_28th_or_earlier_is_unchanged(self):
        self.run_fd(date(2025, 1, 15), date(2025, 5, 15))
        self.process_on(date(2025, 6, 1))
        self.assertEqual(self.payout_dates(), [date(2025, 2, 15), date(2025, 3, 15), date(2025, 4, 15), date(2025, 5, 15)])

    def test_without_the_catch_up_the_schedule_carries_on_from_the_next_payout_on_the_same_days(self):
        with patch('django.utils.timezone.localdate', return_value=date(2026, 9, 10)):          # (set up on 10 September)
            result = self.run_fd(date(2026, 8, 31), date(2027, 8, 31), historical=False)
        schedule = RecurringTransaction.objects.get(user=self.user, source='Interest Income')
        self.assertEqual(schedule.start_date.day, 31)
        self.assertEqual(Income.objects.filter(user=self.user, source='Interest Income').count(), 0)
        self.process_on(date(2026, 12, 15))
        self.assertEqual(self.payout_dates(), [date(2026, 9, 30), date(2026, 10, 31), date(2026, 11, 30)])
        self.assertTrue(result.flow_id)


class QuarterlyAndYearlyTests(Base):
    def test_a_quarterly_payout_from_the_31st_follows_the_quarter_ends(self):
        self.run_fd(date(2025, 1, 31), date(2026, 1, 31), payout='QUARTERLY')
        self.process_on(date(2026, 2, 1))
        self.assertEqual(self.payout_dates(), [date(2025, 4, 30), date(2025, 7, 31), date(2025, 10, 31), date(2026, 1, 31)])

    def test_a_yearly_payout_from_the_29th_of_february_returns_to_the_29th_in_a_leap_year(self):
        self.run_fd(date(2024, 2, 29), date(2028, 2, 29), payout='ANNUAL')
        self.process_on(date(2028, 3, 1))
        self.assertEqual(self.payout_dates(), [date(2025, 2, 28), date(2026, 2, 28), date(2027, 2, 28), date(2028, 2, 29)])


class RdTransferTests(Base):
    def test_the_monthly_transfer_into_a_recurring_deposit_pays_in_on_the_same_days_as_the_payouts(self):
        self.run_fd(date(2025, 1, 31), date(2025, 6, 30), payout='MONTHLY', payout_to_rd='on', rd_name='Interest RD',
                    rd_rate='6.5', rd_tenure_months='6', rd_compounding='QUARTERLY')
        self.process_on(date(2025, 7, 1))
        payouts = self.payout_dates()
        transfers = sorted(Transfer.objects.filter(user=self.user, to_account__name='Interest RD').values_list('date', flat=True))
        self.assertEqual(payouts[:4], [date(2025, 2, 28), date(2025, 3, 31), date(2025, 4, 30), date(2025, 5, 31)])
        self.assertEqual(transfers[:4], payouts[:4])
        self.assertTrue(Account.objects.filter(user=self.user, name='Interest RD').exists())
