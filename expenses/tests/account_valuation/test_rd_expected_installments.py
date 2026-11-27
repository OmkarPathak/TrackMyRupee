"""A running Recurring Deposit: what has been paid, what is still to come, and what it is worth at maturity."""
from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase

from expenses.account_valuation import (
    _get_rd_installments,
    get_baseline,
    get_current,
    get_current_deposit,
    get_maturity_value,
    get_rd_plan,
)
from expenses.models import Account, Transfer

D = Decimal
TODAY = date(2026, 11, 26)


class Base(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('rd-plan', password='x')
        self.bank = Account.objects.create(user=self.user, name='ICICI Savings', account_type='SAVINGS_ACCOUNT',
                                           balance=D('1000000'), currency='₹')

    def rd(self, rate='12', comp='SIMPLE', maturity=date(2027, 2, 7), closed=None, **extra):
        return Account.objects.create(
            user=self.user, name=f'RD {Account.objects.count()}', account_type='RD', balance=D('0'), currency='₹',
            deposit_rate=D(rate) if rate is not None else None, deposit_compounding=comp,
            deposit_start_date=date(2026, 2, 7), deposit_maturity_date=maturity, deposit_closed_date=closed,
            rd_installment_amount=D('20000'), rd_installment_day=7, **extra)

    def post(self, account, *dates, amount='20000'):
        for when in dates:
            Transfer.objects.create(user=self.user, from_account=self.bank, to_account=account, amount=D(amount),
                                    converted_amount=D(amount), date=when)


class ARunningRdWithSomeInstallmentsRecorded(Base):
    """The case that was wrong: ₹20,000 on the 7th for a year, 12% simple, transfers recorded April to November."""

    def setUp(self):
        super().setUp()
        self.account = self.rd()
        self.post(self.account, *[date(2026, month, 5) for month in range(4, 12)])
        self.plan = get_rd_plan(self.account, today=TODAY)

    def test_what_has_been_paid(self):
        # February and March come from the schedule (before the first recorded transfer), April to November are recorded
        self.assertEqual((self.plan['paid_count'], self.plan['paid_total']), (10, D('200000.00')))

    def test_the_installments_still_to_come_are_the_schedule(self):
        self.assertEqual([(u['date'], u['amount'], u['expected']) for u in self.plan['upcoming']],
                         [(date(2026, 12, 7), D('20000'), True), (date(2027, 1, 7), D('20000'), True)])
        self.assertEqual((self.plan['upcoming_count'], self.plan['upcoming_total']), (2, D('40000.00')))

    def test_the_whole_deposit_is_twelve_installments(self):
        self.assertEqual((self.plan['planned_count'], self.plan['planned_total']), (12, D('240000.00')))

    def test_the_maturity_value_includes_the_installments_still_to_come(self):
        self.assertEqual(self.plan['maturity_value'], D('255813.70'))               # was 2,15,202 (ten installments only)
        self.assertEqual(get_maturity_value(self.account, today=TODAY), D('255813.70'))
        self.assertEqual(self.plan['interest_at_maturity'], D('15813.70'))

    def test_the_balance_today_is_what_has_been_paid_with_its_interest(self):
        self.assertEqual(get_current(self.account, today=TODAY), D('210402.19'))
        self.assertEqual(get_baseline(self.account, today=TODAY), D('200000.00'))

    def test_once_the_recurring_transfers_post_the_projection_does_not_change_its_shape(self):
        self.post(self.account, date(2026, 12, 5), date(2027, 1, 5))
        plan = get_rd_plan(self.account, today=date(2027, 1, 20))
        self.assertEqual((plan['paid_count'], plan['upcoming_count'], plan['planned_count']), (12, 0, 12))
        self.assertEqual(plan['planned_total'], D('240000.00'))
        self.assertEqual(plan['maturity_value'], D('255840.00'))


class InstallmentRules(Base):
    def test_a_transfer_already_recorded_for_a_coming_month_is_counted_once_not_expected_again(self):
        account = self.rd()
        self.post(account, *[date(2026, month, 5) for month in range(4, 12)], date(2026, 12, 1))
        plan = get_rd_plan(account, today=TODAY)
        december = [u for u in plan['upcoming'] if u['date'].month == 12]
        self.assertEqual([(u['date'], u['expected']) for u in december], [(date(2026, 12, 1), False)])     # recorded, not expected
        self.assertEqual((plan['upcoming_count'], plan['planned_count']), (2, 12))

    def test_a_month_that_passed_without_a_transfer_stays_skipped(self):
        account = self.rd()
        self.post(account, date(2026, 4, 5), date(2026, 6, 5))                    # May was missed
        plan = get_rd_plan(account, today=date(2026, 6, 20))
        self.assertEqual(plan['paid_total'], D('80000.00'))                       # Feb, Mar (schedule), Apr, Jun
        months_paid = {d.month for d, _ in _get_rd_installments(account, date(2026, 6, 20))}
        self.assertNotIn(5, months_paid)
        self.assertNotIn(5, {u['date'].month for u in plan['upcoming']})           # and it is not "expected" later either

    def test_a_year_long_rd_has_twelve_installments_not_thirteen(self):
        account = self.rd()                                                       # no transfers recorded: the schedule alone
        plan = get_rd_plan(account, today=TODAY)
        self.assertEqual((plan['planned_count'], plan['planned_total']), (12, D('240000.00')))
        self.assertEqual(get_baseline(account, today=date(2027, 2, 7)), D('240000.00'))     # on the maturity date itself
        self.assertEqual(get_current_deposit(account, ledger_balance=D('0'), today=date(2027, 2, 7)), D('255708.49'))
        self.assertEqual(plan['maturity_value'], D('255708.49'))

    def test_the_installment_day_falls_back_to_the_start_day_and_short_months_use_their_last_day(self):
        account = Account.objects.create(
            user=self.user, name='RD Jan31', account_type='RD', balance=D('0'), currency='₹', deposit_rate=D('6'),
            deposit_compounding='SIMPLE', deposit_start_date=date(2026, 1, 31), deposit_maturity_date=date(2026, 5, 31),
            rd_installment_amount=D('1000'), rd_installment_day=31)
        plan = get_rd_plan(account, today=date(2026, 1, 31))
        self.assertEqual(plan['planned_count'], 4)                                # Jan 31, Feb 28, Mar 31, Apr 30
        self.assertEqual([u['date'] for u in plan['upcoming']], [date(2026, 2, 28), date(2026, 3, 31), date(2026, 4, 30)])

    def test_after_maturity_nothing_is_still_to_come(self):
        account = self.rd()
        plan = get_rd_plan(account, today=date(2027, 3, 1))
        self.assertEqual((plan['upcoming_count'], plan['paid_count']), (0, 12))
        self.assertTrue(plan['matured'])
        self.assertFalse(get_rd_plan(account, today=TODAY)['matured'])

    def test_a_closed_rd_expects_nothing_more(self):
        account = self.rd(closed=date(2026, 8, 7))
        plan = get_rd_plan(account, today=TODAY)
        self.assertEqual(plan['upcoming_count'], 0)
        self.assertEqual(plan['maturity_date'], date(2026, 8, 7))                  # worked out to the day it closed

    def test_no_projection_without_a_maturity_date_or_a_rate(self):
        self.assertIsNone(get_rd_plan(self.rd(maturity=None), today=TODAY))
        self.assertIsNone(get_maturity_value(self.rd(maturity=None), today=TODAY))
        no_rate = self.rd(rate=None)
        self.assertIsNone(get_maturity_value(no_rate, today=TODAY))
        self.assertIsNone(get_rd_plan(no_rate, today=TODAY)['maturity_value'])

    def test_a_deposit_that_is_not_an_rd_has_no_plan_and_keeps_its_maturity_value(self):
        fd = Account.objects.create(user=self.user, name='FD', account_type='FD', balance=D('100000'), currency='₹',
                                    deposit_rate=D('7'), deposit_compounding='QUARTERLY', deposit_principal=D('100000'),
                                    deposit_start_date=date(2026, 2, 7), deposit_maturity_date=date(2027, 2, 7))
        self.assertIsNone(get_rd_plan(fd, today=TODAY))
        self.assertEqual(get_maturity_value(fd, ledger_balance=D('100000'), today=TODAY),
                         get_current_deposit(fd, ledger_balance=D('100000'), today=date(2027, 2, 7)))

    def test_compounding_applies_to_the_installments_still_to_come(self):
        quarterly = get_rd_plan(self.rd('9.75', 'QUARTERLY'), today=TODAY)
        self.assertAlmostEqual(float(quarterly['maturity_value']), 253044.81, delta=0.5)         # close to a bank's ₹2.53L
        simple_10 = get_rd_plan(self.rd('10', 'SIMPLE'), today=TODAY)
        self.assertEqual(simple_10['maturity_value'], D('253090.41'))


class TheAccountPage(Base):
    """What the person sees: the Installments card with the transfers still expected."""

    def setUp(self):
        from datetime import timedelta

        from django.utils import timezone

        from expenses.models import UserProfile
        super().setUp()
        UserProfile.objects.filter(user=self.user).update(
            tier='PRO', subscription_end_date=timezone.now() + timedelta(days=30), has_seen_tutorial=True,
            consent_granted=True, currency='₹')
        self.user = User.objects.get(pk=self.user.pk)          # (a stale cached profile would be saved back on sign-in)
        self.client.force_login(self.user)
        self.account = self.rd()
        self.post(self.account, *[date(2026, month, 5) for month in range(4, 12)])

    @staticmethod
    def text_of(html):
        """The words and numbers on the page (the app wraps amounts in a tap-to-see-exact element)."""
        import re
        return re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', '', html))

    def page(self, account=None):
        from unittest import mock

        from django.urls import reverse
        with mock.patch('django.utils.timezone.localdate', return_value=TODAY):
            response = self.client.get(reverse('account-detail', kwargs={'pk': (account or self.account).uuid}))
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def test_the_card_shows_what_is_paid_what_is_expected_and_the_maturity_value(self):
        html = self.page()
        self.assertIn('id="rd-installments"', html)
        words = self.text_of(html)
        for text in ('Installments', '10 of 12 paid', 'Paid so far', 'Still to come', 'In total', 'Coming up',
                     '₹2,00,000', '₹40,000', '₹2,40,000', 'Expected on 07 Feb 2027: ₹2,55,814, including ₹15,814 interest'):
            self.assertIn(text, words, text)

    def test_the_expected_transfers_are_listed_with_their_dates(self):
        html = self.page()
        upcoming = html[html.index('id="rd-upcoming"'):]
        upcoming = upcoming[:upcoming.index('</ul>')]
        self.assertIn('07 Dec 2026', upcoming)
        self.assertIn('07 Jan 2027', upcoming)
        self.assertEqual(upcoming.count('is-expected'), 2)
        self.assertNotIn('07 Feb 2027', upcoming)                # the maturity day is not another installment

    def test_the_header_card_says_expected_at_maturity_with_the_full_amount(self):
        words = self.text_of(self.page())
        self.assertIn('Installments: 10 of 12 paid', words)
        self.assertRegex(words, r'Expected at maturity: ₹2,55,814')
        self.assertNotIn('2,15,202', words)

    def test_a_recorded_transfer_for_a_coming_month_is_tagged_recorded(self):
        self.post(self.account, date(2026, 12, 1))
        html = self.page()
        upcoming = html[html.index('id="rd-upcoming"'):]
        upcoming = upcoming[:upcoming.index('</ul>')]
        self.assertIn('Recorded', upcoming)
        self.assertEqual(upcoming.count('is-expected'), 1)

    def test_other_deposits_and_accounts_do_not_get_the_card(self):
        fd = Account.objects.create(user=self.user, name='FD', account_type='FD', balance=D('100000'), currency='₹',
                                    deposit_rate=D('7'), deposit_start_date=date(2026, 2, 7),
                                    deposit_maturity_date=date(2027, 2, 7))
        self.assertNotIn('id="rd-installments"', self.page(fd))
        self.assertNotIn('id="rd-installments"', self.page(self.bank))

    def test_the_card_is_translated(self):
        from expenses.models import UserProfile
        for lang, marker in (('hi', 'किस्त'), ('mr', 'हप्ते')):
            UserProfile.objects.filter(user=self.user).update(language=lang)
            html = self.page()
            card = html[html.index('id="rd-installments"'):html.index('id="rd-installments"') + 4000]
            self.assertNotIn('Paid so far', card, lang)
            self.assertNotIn('Still to come', card, lang)
            self.assertNotIn('Expected installments come', card, lang)
            self.assertIn(marker, card, lang)

    def test_the_principal_row_is_what_has_been_paid_in(self):
        words = self.text_of(self.page())
        self.assertIn('Principal: ₹2,00,000', words)             # not the ledger balance (the first two months predate the app)

    def test_a_deposit_that_has_matured_says_matured_not_expected(self):
        from unittest import mock

        from django.urls import reverse
        finished = Account.objects.create(
            user=self.user, name='RD Finished', account_type='RD', balance=D('0'), currency='₹', deposit_rate=D('7'),
            deposit_compounding='QUARTERLY', deposit_start_date=date(2025, 6, 5), deposit_maturity_date=date(2026, 6, 5),
            rd_installment_amount=D('5000'), rd_installment_day=5)
        with mock.patch('django.utils.timezone.localdate', return_value=TODAY):
            html = self.client.get(reverse('account-detail', kwargs={'pk': finished.uuid})).content.decode()
        words = self.text_of(html)
        self.assertIn('Matured on 05 Jun 2026:', words)
        self.assertNotIn('Expected on 05 Jun 2026', words)
        self.assertIn('At maturity:', words)
        self.assertNotIn('Expected at maturity:', words)
        self.assertIn('12 of 12 paid', words)
        self.assertIn('No installments are still to come.', words)
