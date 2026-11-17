"""A fixed deposit can pay its interest out (every month, 3 months or year) instead of adding it to the deposit.

Such a deposit stays worth its principal: the interest lands in another account as income, so accruing it on the deposit
as well would count it twice in net worth. The FD setup records the payouts as a recurring income into the chosen account.
"""
from datetime import date
from decimal import Decimal

from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import timezone

from expenses.account_valuation import get_baseline, get_current
from expenses.account_types import get_fields_for_account_type
from expenses.flows.registry import FlowRegistry
from expenses.forms import AccountForm
from expenses.models import Account, CapitalEvent, RecurringTransaction
from expenses.services_recurring import RecurringService
from expenses.tests.test_flows_end_to_end import FlowTestBase

D = Decimal


class PayoutMathTests(SimpleTestCase):
    def test_a_monthly_payout_is_a_twelfth_of_a_years_simple_interest(self):
        self.assertEqual(RecurringService.payout_amount(D('2500000'), D('7.1'), 'MONTHLY'), D('14791.67'))

    def test_quarterly_and_yearly_payouts(self):
        self.assertEqual(RecurringService.payout_amount(D('100000'), D('8'), 'QUARTERLY'), D('2000.00'))
        self.assertEqual(RecurringService.payout_amount(D('100000'), D('8'), 'ANNUAL'), D('8000.00'))

    def test_no_principal_no_rate_or_an_unknown_payout_is_zero(self):
        self.assertEqual(RecurringService.payout_amount(0, D('7'), 'MONTHLY'), D('0.00'))
        self.assertEqual(RecurringService.payout_amount(D('1000'), 0, 'MONTHLY'), D('0.00'))
        self.assertEqual(RecurringService.payout_amount(D('1000'), D('7'), 'WEEKLY'), D('0.00'))

    def test_the_first_payout_is_one_period_after_the_deposit(self):
        self.assertEqual(RecurringService.first_payout_date(date(2026, 10, 15), 'MONTHLY'), date(2026, 11, 15))
        self.assertEqual(RecurringService.first_payout_date(date(2026, 10, 15), 'QUARTERLY'), date(2027, 1, 15))
        self.assertEqual(RecurringService.first_payout_date(date(2026, 10, 15), 'ANNUAL'), date(2027, 10, 15))

    def test_month_end_start_dates_are_clamped(self):
        self.assertEqual(RecurringService.first_payout_date(date(2026, 1, 31), 'MONTHLY'), date(2026, 2, 28))


class ValuationTests(FlowTestBase):
    def fd(self, payout=None, **kw):
        values = dict(user=self.user, name='FD', account_type='FD', balance=D('0'), currency='₹',
                      deposit_principal=D('2500000'), deposit_rate=D('7.1'), deposit_start_date=date(2025, 1, 1),
                      deposit_maturity_date=date(2030, 1, 1), deposit_compounding='QUARTERLY', deposit_payout=payout)
        values.update(kw)
        return Account.objects.create(**values)

    def test_a_deposit_that_pays_out_stays_at_its_principal(self):
        account = self.fd('MONTHLY')
        self.assertEqual(get_current(account, today=date(2026, 6, 1)), D('2500000.00'))
        self.assertEqual(get_baseline(account, today=date(2026, 6, 1)), D('2500000.00'))

    def test_every_payout_choice_stays_at_the_principal(self):
        for payout in ('MONTHLY', 'QUARTERLY', 'ANNUAL'):
            account = self.fd(payout, name=f'FD {payout}')
            self.assertEqual(get_current(account, today=date(2026, 6, 1)), D('2500000.00'), payout)

    def test_a_deposit_that_does_not_pay_out_still_accrues(self):
        account = self.fd(None)
        self.assertGreater(get_current(account, today=date(2026, 6, 1)), D('2500000'))

    def test_the_interest_is_not_counted_twice(self):
        """Principal 25L + the interest the bank already paid into savings: net worth rises by the interest once."""
        paying = self.fd('MONTHLY')
        accruing = self.fd(None, name='Cumulative FD')
        interest_in_the_deposit = get_current(accruing, today=date(2026, 6, 1)) - get_current(paying, today=date(2026, 6, 1))
        self.assertGreater(interest_in_the_deposit, 0)

    def test_the_value_does_not_move_after_maturity_either(self):
        account = self.fd('MONTHLY')
        self.assertEqual(get_current(account, today=date(2031, 1, 1)), D('2500000.00'))


class ManualFormTests(FlowTestBase):
    def post(self, **extra):
        data = {'name': 'Manual FD', 'account_type': 'FD', 'balance': '0', 'currency': '₹',
                'deposit_principal': '100000', 'deposit_rate': '7', 'deposit_start_date': '2026-10-01',
                'deposit_maturity_date': '2027-10-01', 'deposit_compounding': 'QUARTERLY'}
        data.update(extra)
        return AccountForm(data=data, user=self.user)

    def test_the_payout_choice_is_saved(self):
        form = self.post(deposit_payout='MONTHLY')
        self.assertTrue(form.is_valid(), dict(form.errors))
        self.assertEqual(form.save().deposit_payout, 'MONTHLY')

    def test_left_empty_the_interest_is_paid_at_maturity(self):
        form = self.post(deposit_payout='')
        self.assertTrue(form.is_valid(), dict(form.errors))
        self.assertIsNone(form.save().deposit_payout)

    def test_the_field_is_offered_for_a_fixed_deposit_only(self):
        self.assertIn('deposit_payout', get_fields_for_account_type('FD'))
        self.assertNotIn('deposit_payout', get_fields_for_account_type('RD'))
        self.assertNotIn('deposit_payout', get_fields_for_account_type('SAVINGS_ACCOUNT'))

    def test_a_payout_sent_for_another_type_is_dropped(self):
        form = self.post(account_type='RD', deposit_payout='MONTHLY', rd_installment_amount='5000',
                         rd_installment_day='5')
        self.assertTrue(form.is_valid(), dict(form.errors))
        self.assertIsNone(form.save().deposit_payout)

    def test_the_account_page_offers_the_choice(self):
        response = self.client.get(reverse('account-create'))
        self.assertContains(response, 'name="deposit_payout"')
        self.assertContains(response, 'At maturity (added to the deposit)')


class FlowTests(FlowTestBase):
    def payload(self, **overrides):
        data = dict(name='Big FD', principal='2500000', annual_rate='7.1', deposit_start_date='2026-10-01',
                    maturity_date='2029-10-01', deposit_compounding='QUARTERLY', from_account=self.cash,
                    interest_payout='MONTHLY', payout_account=self.bank)
        data.update(overrides)
        return data

    def test_a_payout_creates_the_deposit_the_funding_and_a_monthly_interest_income(self):
        result = self.run_flow('fd', self.payload())
        account = self.one(result, Account)
        self.assertEqual(account.deposit_payout, 'MONTHLY')
        self.assertEqual(self.one(result, CapitalEvent).amount, D('2500000.00'))
        income = self.one(result, RecurringTransaction)
        self.assertEqual((income.transaction_type, income.amount, income.frequency, income.account, income.source,
                          income.start_date, income.end_date, income.is_active),
                         ('INCOME', D('14791.67'), 'MONTHLY', self.bank, 'Interest Income',
                          date(2026, 11, 1), date(2029, 10, 1), True))
        self.assertIn('Big FD', income.description)

    def test_the_payout_goes_to_the_funding_account_when_none_is_chosen(self):
        income = self.one(self.run_flow('fd', self.payload(payout_account='')), RecurringTransaction)
        self.assertEqual(income.account, self.cash)

    def test_the_amount_can_be_set_by_hand(self):
        income = self.one(self.run_flow('fd', self.payload(payout_amount='14500')), RecurringTransaction)
        self.assertEqual(income.amount, D('14500.00'))

    def test_quarterly_and_yearly_payouts_use_matching_schedules(self):
        quarterly = self.one(self.run_flow('fd', self.payload(interest_payout='QUARTERLY')), RecurringTransaction)
        self.assertEqual((quarterly.frequency, quarterly.amount, quarterly.start_date),
                         ('QUARTERLY', D('44375.00'), date(2027, 1, 1)))
        yearly = self.one(self.run_flow('fd', self.payload(name='Yearly FD', interest_payout='ANNUAL')),
                          RecurringTransaction)
        self.assertEqual((yearly.frequency, yearly.amount, yearly.start_date),
                         ('YEARLY', D('177500.00'), date(2027, 10, 1)))

    def test_without_a_payout_nothing_changes(self):
        result = self.run_flow('fd', self.payload(interest_payout='', payout_account='', payout_amount=''))
        self.assertIsNone(self.one(result, Account).deposit_payout)
        self.assertEqual(self.created(result, RecurringTransaction), [])

    def test_payout_details_without_a_payout_are_ignored(self):
        result = self.run_flow('fd', self.payload(interest_payout='', payout_amount='9999'))
        self.assertEqual(self.created(result, RecurringTransaction), [])

    def test_a_payout_needs_a_rate_or_an_amount(self):
        self.assertErrorOn('fd', self.payload(annual_rate='0'), 'annual_rate')
        self.valid_form('fd', self.payload(annual_rate='0', payout_amount='1000'))

    def test_an_unknown_payout_choice_is_refused(self):
        self.assertErrorOn('fd', self.payload(interest_payout='DAILY'), 'interest_payout')

    def test_the_payout_account_must_be_the_users_own(self):
        from django.contrib.auth.models import User
        other = Account.objects.create(user=User.objects.create_user('other'), name='Theirs',
                                       account_type='SAVINGS_ACCOUNT', balance=D('0'), currency='₹')
        self.assertErrorOn('fd', self.payload(payout_account=other.pk), 'payout_account')

    def test_a_deposit_booked_earlier_does_not_backfill_past_payouts(self):
        result = self.run_flow('fd', self.payload(deposit_start_date='2026-01-01', maturity_date='2029-01-01'))
        income = self.one(result, RecurringTransaction)
        self.assertIsNotNone(income.last_processed_date)
        self.assertLessEqual(income.last_processed_date, timezone.localdate())

    def test_the_preview_says_what_will_be_paid_and_where(self):
        flow = FlowRegistry.get('fd')
        form = self.valid_form('fd', self.payload())
        preview = flow.preview(self.user, form.cleaned_data)
        self.assertEqual(preview['headline'], 2500000.0)
        text = ' '.join(str(b) for b in preview['bullets'])
        self.assertIn('14,791.67', text)
        self.assertIn('Bank', text)
        self.assertIn('every month', text)

    def test_the_preview_without_a_payout_still_shows_the_maturity_value(self):
        flow = FlowRegistry.get('fd')
        form = self.valid_form('fd', self.payload(interest_payout='', payout_account=''))
        self.assertGreater(flow.preview(self.user, form.cleaned_data)['headline'], 2500000.0)

    def test_the_setup_page_has_the_payout_step(self):
        html = self.client.get(reverse('flow-detail', kwargs={'key': 'fd'})).content.decode()
        self.assertIn('name="interest_payout"', html)
        self.assertIn('Interest payout', html)

    def test_the_review_lists_the_payout_among_what_will_be_created(self):
        flow = FlowRegistry.get('fd')
        form = self.valid_form('fd', self.payload())
        cards = {str(card['title']): str(card['description'])
                 for card in flow.get_what_we_create(self.user, form.cleaned_data)}
        self.assertIn('Interest payout', cards)
        self.assertIn('14,792', cards['Interest payout'])
        self.assertEqual(cards['Interest payout'], '₹14,792 every month')

    def test_the_review_has_no_payout_card_without_a_payout(self):
        flow = FlowRegistry.get('fd')
        form = self.valid_form('fd', self.payload(interest_payout='', payout_account=''))
        titles = [str(card['title']) for card in flow.get_what_we_create(self.user, form.cleaned_data)]
        self.assertNotIn('Interest payout', titles)
