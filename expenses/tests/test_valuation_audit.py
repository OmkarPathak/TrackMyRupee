"""Audit of account valuation: exchange rates stay fresh, deposits accrue the way a bank counts them,
and the interest summary reflects what was just recorded."""
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import MagicMock, patch

import requests
from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone

from expenses.account_valuation import (
    _years_between,
    get_baseline,
    get_current,
    get_interest_summary,
    process_matured_deposit_incomes,
)
from expenses.fx import FXService
from expenses.models import Account, FXRate, Income, Transfer
from expenses.utils import get_exchange_rate

D = Decimal


def api_returns(rate):
    response = MagicMock()
    response.json.return_value = {'rates': {'INR': rate, 'USD': rate, 'EUR': rate}}
    return patch('expenses.utils.requests.get', return_value=response)


def api_down():
    return patch('expenses.utils.requests.get', side_effect=requests.exceptions.ConnectionError('down'))


def store(rate, days_old=0, frm='USD', to='INR'):
    return FXRate.objects.create(from_currency=frm, to_currency=to, rate=D(str(rate)), source='t',
                                 as_of_date=timezone.localdate() - timedelta(days=days_old))


class ExchangeRateFreshnessTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_a_rate_stored_today_is_used_without_calling_the_service(self):
        store(81)
        with api_returns(99) as get:
            self.assertEqual(get_exchange_rate('$', '₹'), D('81'))
        get.assert_not_called()

    def test_an_old_stored_rate_is_refreshed_from_the_live_service(self):
        store(70, days_old=90)
        with api_returns(85.5) as get:
            self.assertEqual(get_exchange_rate('$', '₹'), D('85.5'))
        self.assertEqual(get.call_count, 1)
        self.assertEqual(FXRate.objects.get(as_of_date=timezone.localdate()).rate, D('85.500000'))

    def test_the_rate_is_fetched_once_a_day_not_once_ever_or_every_time(self):
        store(70, days_old=3)
        with api_returns(85) as get:
            get_exchange_rate('$', '₹')
            cache.clear()  # even without the cache, today's stored row now answers
            get_exchange_rate('$', '₹')
        self.assertEqual(get.call_count, 1)

    def test_when_both_services_are_down_the_latest_stored_rate_is_used_not_an_error(self):
        store(70, days_old=10)
        with api_down() as get:
            self.assertEqual(get_exchange_rate('$', '₹'), D('70'))
            calls = get.call_count
            self.assertEqual(get_exchange_rate('$', '₹'), D('70'))  # remembered briefly: no new timeouts
        self.assertEqual(get.call_count, calls)

    def test_with_no_stored_rate_and_no_service_it_fails_rather_than_guessing(self):
        with api_down(), self.assertRaises(RuntimeError):
            get_exchange_rate('$', '₹')

    def test_the_fallback_service_is_tried_when_the_first_fails(self):
        good = MagicMock()
        good.json.return_value = {'rates': {'INR': 84}}
        with patch('expenses.utils.requests.get', side_effect=[requests.exceptions.Timeout('x'), good]):
            self.assertEqual(get_exchange_rate('$', '₹'), D('84'))
        self.assertEqual(FXRate.objects.get().source, 'ExchangeRate-API')


class RateMapTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_todays_rate_is_used_as_it_is(self):
        store(81)
        with api_returns(99) as get:
            self.assertEqual(FXService.build_rate_map({'$'}, '₹')['$'], D('81'))
        get.assert_not_called()

    def test_an_old_rate_is_refreshed_for_a_current_valuation(self):
        store(70, days_old=40)
        with api_returns(86):
            self.assertEqual(FXService.build_rate_map({'$'}, '₹')['$'], D('86'))

    def test_an_old_rate_is_still_the_fallback_when_the_service_is_down(self):
        store(70, days_old=40)
        with api_down():
            self.assertEqual(FXService.build_rate_map({'$'}, '₹')['$'], D('70'))

    def test_a_historical_valuation_uses_the_rate_of_that_day_and_never_refreshes(self):
        store(70, days_old=40)
        store(75, days_old=5)
        with api_returns(99) as get:
            rate = FXService.build_rate_map({'$'}, '₹', as_of=timezone.localdate() - timedelta(days=20))['$']
        self.assertEqual(rate, D('70'))
        get.assert_not_called()

    def test_symbols_and_iso_codes_share_one_rate_and_the_base_currency_is_one(self):
        store(81)
        rates = FXService.build_rate_map({'$', 'USD', '₹'}, '₹')
        self.assertEqual((rates['$'], rates['USD'], rates['₹']), (D('81'), D('81'), D('1.0')))

    def test_to_base_converts_and_rounds_to_paise(self):
        user = User.objects.create_user(username='fx', password='pw')
        store(83.333333)
        self.assertEqual(FXService.to_base(D('10.00'), '$', user), D('833.33'))
        self.assertEqual(FXService.to_base(D('10.00'), '₹', user), D('10.00'))


class DepositAccrualTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='dep', password='pw')

    def fd(self, start, compounding='QUARTERLY', rate='7', principal='100000', **kw):
        return Account.objects.create(
            user=self.user, name=f'FD {start} {compounding}', account_type='FD', currency='₹', balance=D(principal),
            deposit_principal=D(principal), deposit_rate=D(rate), deposit_start_date=start,
            deposit_compounding=compounding, **kw)

    def test_years_are_counted_by_calendar_anniversary(self):
        self.assertEqual(_years_between(date(2025, 1, 1), date(2026, 1, 1)), D('1'))
        self.assertEqual(_years_between(date(2023, 6, 15), date(2025, 6, 15)), D('2'))
        self.assertEqual(_years_between(date(2025, 1, 1), date(2025, 7, 2)), D(182) / D(365))
        self.assertEqual(_years_between(date(2025, 1, 1), date(2025, 1, 1)), D('0'))
        self.assertEqual(_years_between(date(2025, 5, 1), date(2025, 1, 1)), D('0'))

    def test_a_one_year_deposit_is_exactly_one_year_even_across_a_leap_day(self):
        self.assertEqual(_years_between(date(2023, 9, 1), date(2024, 9, 1)), D('1'))
        self.assertEqual(_years_between(date(2023, 3, 1), date(2024, 3, 1)), D('1'))
        self.assertEqual(_years_between(date(2024, 2, 29), date(2025, 2, 28)), D('0') + D(365) / D(365))

    def test_a_quarterly_fd_reaches_the_banks_maturity_amount(self):
        # 1,00,000 at 7% compounded quarterly for one year: 100000 * 1.0175^4 = 107,185.90
        account = self.fd(date(2025, 1, 1), deposit_maturity_date=date(2026, 1, 1))
        self.assertEqual(get_current(account, today=date(2027, 1, 1)), D('107185.90'))

    def test_the_same_fd_across_a_leap_day_matures_to_the_same_amount(self):
        account = self.fd(date(2023, 9, 1), deposit_maturity_date=date(2024, 9, 1))
        self.assertEqual(get_current(account, today=date(2025, 1, 1)), D('107185.90'))

    def test_simple_monthly_and_annual_compounding(self):
        base = date(2025, 1, 1)
        end = date(2026, 1, 1)
        self.assertEqual(get_current(self.fd(base, 'SIMPLE'), today=end), D('107000.00'))
        self.assertEqual(get_current(self.fd(base, 'ANNUAL'), today=end), D('107000.00'))
        self.assertEqual(get_current(self.fd(base, 'MONTHLY'), today=end), D('107229.01'))

    def test_the_baseline_is_what_went_in_and_never_includes_interest(self):
        account = self.fd(date(2025, 1, 1))
        self.assertEqual(get_baseline(account, today=date(2026, 6, 1)), D('100000.00'))

    def test_a_deposit_not_started_yet_is_worth_its_principal(self):
        account = self.fd(date(2030, 1, 1))
        self.assertEqual(get_current(account, today=date(2026, 1, 1)), D('100000'))

    def test_a_recurring_deposit_adds_up_each_installment_from_its_own_date(self):
        rd = Account.objects.create(
            user=self.user, name='RD', account_type='RD', currency='₹', balance=D('0'),
            deposit_rate=D('6'), deposit_start_date=date(2025, 1, 5), deposit_compounding='SIMPLE',
            rd_installment_amount=D('1000'), rd_installment_day=5)
        # Five installments (Jan to May 5th); each earns simple interest from its own date to 5 May.
        today = date(2025, 5, 5)
        expected = sum(
            D('1000') * (1 + D('0.06') * _years_between(d, today))
            for d in [date(2025, m, 5) for m in range(1, 6)]
        ).quantize(D('0.01'))
        self.assertEqual(get_current(rd, today=today), expected)
        self.assertEqual(get_baseline(rd, today=today), D('5000.00'))

    def test_a_recurring_deposit_funded_from_a_dollar_account_counts_rupees(self):
        usd = Account.objects.create(user=self.user, name='USD', currency='$', balance=D('1000'))
        rd = Account.objects.create(
            user=self.user, name='RD2', account_type='RD', currency='₹', balance=D('0'),
            deposit_rate=D('6'), deposit_start_date=date(2025, 1, 5), deposit_compounding='SIMPLE',
            rd_installment_amount=D('1000'), rd_installment_day=5)
        store(80)
        Transfer.objects.create(user=self.user, from_account=usd, to_account=rd, amount=D('10'),
                                date=date(2025, 1, 5))
        self.assertEqual(get_baseline(rd, today=date(2025, 1, 31)), D('800.00'))


class MaturityIncomeTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='mat', password='pw')

    def matured(self, **kw):
        start = date.today() - timedelta(days=800)
        return Account.objects.create(
            user=self.user, name='Matured', account_type='FD', currency='₹', balance=D('100000'),
            deposit_principal=D('100000'), deposit_rate=D('8'), deposit_start_date=start,
            deposit_compounding='ANNUAL', deposit_maturity_date=start + timedelta(days=365),
            record_maturity_income=True, **kw)

    def test_the_interest_is_recorded_once_as_income_on_the_maturity_date(self):
        account = self.matured()
        first = process_matured_deposit_incomes(self.user)
        again = process_matured_deposit_incomes(self.user)
        self.assertEqual(len(first), 1)
        self.assertEqual(again, [])
        self.assertEqual(first[0].amount, D('8000.00'))
        self.assertEqual(first[0].date, account.deposit_maturity_date)
        self.assertEqual(Income.objects.filter(account=account, source_type='Investment Returns').count(), 1)

    def test_an_account_not_asked_to_record_it_gets_no_income(self):
        account = self.matured()
        Account.objects.filter(pk=account.pk).update(record_maturity_income=False)
        self.assertEqual(process_matured_deposit_incomes(self.user), [])

    def test_a_deposit_that_has_not_matured_gets_no_income(self):
        Account.objects.create(
            user=self.user, name='Open', account_type='FD', currency='₹', balance=D('1000'),
            deposit_principal=D('1000'), deposit_rate=D('8'), deposit_start_date=date.today(),
            deposit_maturity_date=date.today() + timedelta(days=100), record_maturity_income=True)
        self.assertEqual(process_matured_deposit_incomes(self.user), [])


class InterestSummaryTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(username='int', password='pw')
        self.account = Account.objects.create(user=self.user, name='Bank', currency='₹', balance=D('0'))

    def test_recording_interest_shows_up_at_once_not_after_the_cache_expires(self):
        self.assertEqual(get_interest_summary(self.user)['interest_earned'], D('0.00'))
        Income.objects.create(user=self.user, account=self.account, amount=D('250'), currency='₹',
                              source='Interest Income', source_type='Interest', date=date.today())
        self.assertEqual(get_interest_summary(self.user)['interest_earned'], D('250.00'))

    def test_deleting_it_takes_it_off_at_once(self):
        income = Income.objects.create(user=self.user, account=self.account, amount=D('250'), currency='₹',
                                       source='Interest Income', source_type='Interest', date=date.today())
        self.assertEqual(get_interest_summary(self.user)['interest_earned'], D('250.00'))
        income.delete()
        self.assertEqual(get_interest_summary(self.user)['interest_earned'], D('0.00'))

    def test_one_users_interest_never_counts_for_another(self):
        other = User.objects.create_user(username='int2', password='pw')
        acc = Account.objects.create(user=other, name='Bank', currency='₹', balance=D('0'))
        Income.objects.create(user=other, account=acc, amount=D('999'), currency='₹',
                              source='Interest Income', source_type='Interest', date=date.today())
        self.assertEqual(get_interest_summary(self.user)['interest_earned'], D('0.00'))
