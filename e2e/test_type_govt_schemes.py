"""Government and retirement schemes (PPF, EPF, VPF, NPS, SSY, Post Office, APY): long-running deposits with a rate.

They share the deposit valuation with an FD, but differ in what the form asks (NPS has no fixed rate), how long they run
(many have no maturity date, or one decades away) and in being topped up over the years.
"""
import unittest
from datetime import date
from decimal import Decimal

from dateutil.relativedelta import relativedelta
from django.urls import reverse
from django.utils import timezone

from e2e.base import E2ETestCase, make_account, make_user
from e2e.type_helpers import (
    balance_on,
    deposit_value,
    net_worth,
    open_account,
)

D = Decimal
TODAY = timezone.localdate()
SCHEMES = ('PPF', 'EPF', 'VPF', 'NPS', 'SSY', 'POST_OFFICE', 'APY')


class Base(E2ETestCase):
    def setUp(self):
        super().setUp()
        self.user = make_user('govt-edge')

    def scheme(self, kind='PPF', name=None, principal=100000, rate='7.1', compounding='ANNUAL', start=None, maturity=None, **extra):
        return make_account(self.user, name or f'My {kind}', kind, principal, deposit_principal=D(str(principal)),
                            deposit_rate=None if rate is None else D(str(rate)), deposit_compounding=compounding,
                            deposit_start_date=start, deposit_maturity_date=maturity, **extra)


class ValueTests(Base):
    def test_a_ppf_grows_by_the_year_toward_its_fifteen_year_maturity(self):
        start = TODAY - relativedelta(years=5, days=40)
        maturity = start + relativedelta(years=15)
        ppf = self.scheme('PPF', start=start, maturity=maturity)
        page = self.new_page(self.user)
        open_account(self, page, ppf)
        self.assertEqual(balance_on(page), deposit_value(100000, '7.1', start, TODAY, 'ANNUAL'))
        self.assertGreater(balance_on(page), D('140000'))                   # five years at 7.1%

    def test_every_scheme_with_a_rate_is_worked_out_the_same_way(self):
        start = TODAY - relativedelta(years=2)
        page = self.new_page(self.user)
        for kind in SCHEMES:
            with self.subTest(kind):
                account = self.scheme(kind, rate='8', compounding='QUARTERLY', start=start)
                open_account(self, page, account)
                self.assertEqual(balance_on(page), deposit_value(100000, 8, start, TODAY, 'QUARTERLY'))

    def test_a_scheme_with_no_maturity_date_keeps_growing_to_today(self):
        start = TODAY - relativedelta(years=3)
        epf = self.scheme('EPF', rate='8.25', start=start, maturity=None)
        page = self.new_page(self.user)
        open_account(self, page, epf)
        self.assertEqual(balance_on(page), deposit_value(100000, '8.25', start, TODAY, 'ANNUAL'))
        self.assertNotIn('Expected at maturity', page.inner_text('body'))

    def test_an_nps_with_no_rate_is_worth_what_was_paid_in(self):
        nps = self.scheme('NPS', principal=250000, rate=None, compounding='ANNUAL', start=None)
        page = self.new_page(self.user)
        open_account(self, page, nps)
        self.assertEqual(balance_on(page), D('250000.00'))
        self.assertEqual(net_worth(self, page), D('250000.00'))

    def test_an_nps_with_an_assumed_rate_grows_at_that_rate(self):
        start = TODAY - relativedelta(years=1)
        nps = self.scheme('NPS', principal=100000, rate='10', start=start)
        page = self.new_page(self.user)
        open_account(self, page, nps)
        self.assertEqual(balance_on(page), deposit_value(100000, 10, start, TODAY, 'ANNUAL'))

    def test_a_scheme_that_matured_stays_at_its_maturity_value(self):
        start, maturity = TODAY - relativedelta(years=20), TODAY - relativedelta(years=5)
        ppf = self.scheme('PPF', start=start, maturity=maturity)
        page = self.new_page(self.user)
        open_account(self, page, ppf)
        self.assertEqual(balance_on(page), deposit_value(100000, '7.1', start, maturity, 'ANNUAL'))

    def test_the_value_counts_in_net_worth_beside_cash(self):
        start = TODAY - relativedelta(years=1)
        self.scheme('PPF', start=start)
        make_account(self.user, 'Savings', 'SAVINGS_ACCOUNT', 50000)
        page = self.new_page(self.user)
        self.assertEqual(net_worth(self, page), deposit_value(100000, '7.1', start, TODAY, 'ANNUAL') + D(50000))

    @unittest.expectedFailure
    def test_KNOWN_GAP_money_paid_into_the_scheme_later_is_part_of_its_value(self):
        """A PPF, EPF or NPS is topped up every year. Today the value comes only from the principal typed in when the
        account was made, so a transfer into it changes nothing (the value stays the same, and the money has left the
        bank): net worth drops by the amount paid. Remove the decorator if transfers in start to count."""
        from expenses.models import Transfer
        start = TODAY - relativedelta(years=2)
        ppf = self.scheme('PPF', start=start)
        bank = make_account(self.user, 'Savings', 'SAVINGS_ACCOUNT', 200000)
        page = self.new_page(self.user)
        open_account(self, page, ppf)
        before = balance_on(page)
        Transfer.objects.create(user=self.user, from_account=bank, to_account=ppf, amount=D('50000'), converted_amount=D('50000'), date=TODAY)
        open_account(self, page, ppf)
        self.assertGreaterEqual(balance_on(page) - before, D('50000'))


class FormTests(Base):
    def fill(self, page, kind, name, *, principal='100000', rate=None, start=None, maturity=None):
        self.open(page, reverse('account-create') + f'?type={kind}')
        page.fill('#id_name', name)
        page.click('#nextBtn')
        page.wait_for_selector('#id_deposit_principal', state='visible')
        for field, value in (('deposit_principal', principal), ('deposit_rate', rate), ('deposit_start_date', start),
                             ('deposit_maturity_date', maturity)):
            if value is not None:
                page.fill(f'#id_{field}', value if isinstance(value, str) else value.isoformat())
        page.locator('form button[type="submit"]:visible, form input[type="submit"]:visible').first.click()

    def test_every_scheme_can_be_created_with_a_rate_and_a_start_date(self):
        from expenses.models import Account
        page = self.new_page(self.user)
        for kind in SCHEMES:
            with self.subTest(kind):
                self.fill(page, kind, f'New {kind}', rate='7.5', start=TODAY - relativedelta(years=1))
                self.wait_until(lambda k=kind: Account.objects.filter(user=self.user, name=f'New {k}').exists(), f'{kind} was not saved', seconds=15)

    def test_a_scheme_other_than_nps_must_have_a_rate_and_a_start_date(self):
        from expenses.models import Account
        page = self.new_page(self.user)
        for label, terms, message in (('no rate', dict(start=TODAY - relativedelta(years=1)), 'Interest rate is required'),
                                      ('no start', dict(rate='7'), 'Start date is required')):
            with self.subTest(label):
                self.fill(page, 'PPF', f'PPF {label}', **terms)
                page.wait_for_timeout(1000)
                self.assertIn(message, page.inner_text('body'))
                self.assertFalse(Account.objects.filter(user=self.user, name=f'PPF {label}').exists())

    def test_an_nps_can_be_created_with_neither_rate_nor_start(self):
        from expenses.models import Account
        page = self.new_page(self.user)
        self.fill(page, 'NPS', 'My NPS')
        self.wait_until(lambda: Account.objects.filter(user=self.user, name='My NPS').exists(), 'the NPS was not saved', seconds=15)

    def test_an_nps_with_only_one_of_rate_and_start_is_asked_for_the_other(self):
        from expenses.models import Account
        page = self.new_page(self.user)
        self.fill(page, 'NPS', 'NPS rate only', rate='9')
        page.wait_for_timeout(1000)
        self.assertIn('Enter the start date, or clear the rate', page.inner_text('body'))
        self.fill(page, 'NPS', 'NPS start only', start=TODAY - relativedelta(years=1))
        page.wait_for_timeout(1000)
        self.assertIn('Enter the assumed rate, or clear the start date', page.inner_text('body'))
        self.assertEqual(Account.objects.filter(user=self.user, name__startswith='NPS ').count(), 0)

    def test_the_rate_and_the_dates_are_checked(self):
        page = self.new_page(self.user)
        start = date(2020, 1, 1)
        for label, terms, message in (('rate 150', dict(rate='150', start=start), 'between 0 and 100'),
                                      ('maturity before start', dict(rate='7', start=start, maturity=date(2019, 1, 1)), 'after the start date')):
            with self.subTest(label):
                self.fill(page, 'PPF', f'PPF {label}', **terms)
                page.wait_for_timeout(1000)
                self.assertIn(message, page.inner_text('body'))
