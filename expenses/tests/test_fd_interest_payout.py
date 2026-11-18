"""A fixed deposit can pay its interest out (every month, 3 months or year) instead of adding it to the deposit.

Such a deposit stays worth its principal: the interest lands in another account as income, so accruing it on the deposit
as well would count it twice in net worth. The FD setup records the payouts as a recurring income into the chosen account.
"""
from datetime import date
from decimal import Decimal

from dateutil.relativedelta import relativedelta
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import timezone

from expenses.account_valuation import get_baseline, get_current
from expenses.account_types import get_fields_for_account_type
from expenses.flows.registry import FlowRegistry
from expenses.forms import AccountForm
from expenses.models import Account, CapitalEvent, Income, RecurringTransaction
from expenses.services_recurring import RecurringService
from expenses.tests.test_flows_end_to_end import FlowTestBase
from expenses.views.mixins import process_user_recurring_transactions

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


class BackfillTests(FlowTestBase):
    """An FD booked in the past can record the payouts it has already made."""

    def setUp(self):
        super().setUp()
        # Booked three months and five days ago: three monthly payouts have fallen due since.
        self.start = timezone.localdate() - relativedelta(months=3, days=5)

    def payload(self, **overrides):
        data = dict(name='Old FD', principal='1200000', annual_rate='7.5', deposit_start_date=self.start.isoformat(),
                    maturity_date=(self.start + relativedelta(years=3)).isoformat(), deposit_compounding='QUARTERLY',
                    from_account=self.cash, interest_payout='MONTHLY', payout_account=self.bank)
        data.update(overrides)
        return data

    def interest_entries(self):
        return Income.objects.filter(user=self.user, account=self.bank, source='Interest Income')

    def run_and_process(self, **overrides):
        self.run_flow('fd', self.payload(**overrides))
        process_user_recurring_transactions(self.user)

    def test_backfilling_records_every_payout_that_has_fallen_due(self):
        self.run_and_process(create_historical_entries='on')
        self.assertEqual(self.interest_entries().count(), 3)
        self.assertEqual({entry.amount for entry in self.interest_entries()}, {D('7500.00')})

    def test_the_backfilled_payouts_are_dated_on_their_due_dates(self):
        self.run_and_process(create_historical_entries='on')
        dates = sorted(entry.date for entry in self.interest_entries())
        self.assertEqual(dates, [self.start + relativedelta(months=n) for n in (1, 2, 3)])

    def test_without_the_option_no_past_payout_is_recorded(self):
        self.run_and_process()
        self.assertEqual(self.interest_entries().count(), 0)

    def test_without_the_option_the_schedule_starts_from_the_next_payout(self):
        result = self.run_flow('fd', self.payload())
        schedule = [obj for obj in result.created if isinstance(obj, RecurringTransaction)][0]
        self.assertEqual(schedule.last_processed_date, self.start + relativedelta(months=3))

    def test_the_option_without_a_payout_is_ignored(self):
        result = self.run_flow('fd', self.payload(interest_payout='', create_historical_entries='on'))
        self.assertEqual([obj for obj in result.created if isinstance(obj, RecurringTransaction)], [])

    def test_a_deposit_that_starts_today_has_nothing_to_backfill(self):
        today = timezone.localdate()
        self.run_and_process(create_historical_entries='on', deposit_start_date=today.isoformat(),
                             maturity_date=(today + relativedelta(years=1)).isoformat())
        self.assertEqual(self.interest_entries().count(), 0)

    def test_payouts_are_not_recorded_past_maturity(self):
        today = timezone.localdate()
        self.run_and_process(create_historical_entries='on',
                             deposit_start_date=(today - relativedelta(months=10)).isoformat(),
                             maturity_date=(today - relativedelta(months=7)).isoformat())
        self.assertEqual(self.interest_entries().count(), 3)

    def test_running_the_catch_up_again_does_not_duplicate_payouts(self):
        self.run_and_process(create_historical_entries='on')
        process_user_recurring_transactions(self.user)
        self.assertEqual(self.interest_entries().count(), 3)

    def test_the_setup_page_offers_the_option(self):
        html = self.client.get(reverse('flow-detail', kwargs={'key': 'fd'})).content.decode()
        self.assertIn('name="create_historical_entries"', html)
        self.assertIn('Record the payouts already received', html)


class FollowsTheDepositTests(FlowTestBase):
    """The payout schedule is tied to its deposit: it ends when the deposit is closed or matures, follows a changed
    maturity date, stops when the deposit stops paying out, and goes when the deposit does."""

    def setUp(self):
        super().setUp()
        self.today = timezone.localdate()
        self.start = self.today - relativedelta(months=5, days=3)
        self.maturity = self.start + relativedelta(years=2)

    def payload(self, **overrides):
        data = dict(name='Linked FD', principal='1200000', annual_rate='7.5', deposit_start_date=self.start.isoformat(),
                    maturity_date=self.maturity.isoformat(), deposit_compounding='QUARTERLY', from_account=self.cash,
                    interest_payout='MONTHLY', payout_account=self.bank)
        data.update(overrides)
        return data

    def make(self, **overrides):
        result = self.run_flow('fd', self.payload(**overrides))
        fd = [obj for obj in result.created if isinstance(obj, Account)][0]
        schedule = [obj for obj in result.created if isinstance(obj, RecurringTransaction)][0]
        return fd, schedule

    def edit(self, fd, **changes):
        from expenses.forms import AccountForm
        data = {'name': fd.name, 'account_type': 'FD', 'balance': '0', 'currency': '₹',
                'deposit_principal': str(fd.deposit_principal), 'deposit_rate': str(fd.deposit_rate),
                'deposit_start_date': fd.deposit_start_date.isoformat(),
                'deposit_maturity_date': fd.deposit_maturity_date.isoformat(),
                'deposit_compounding': fd.deposit_compounding, 'deposit_payout': fd.deposit_payout or ''}
        if fd.deposit_closed_date:
            data['deposit_closed_date'] = fd.deposit_closed_date.isoformat()
        data.update(changes)
        form = AccountForm(data=data, instance=fd, user=self.user)
        self.assertTrue(form.is_valid(), dict(form.errors))
        form.save()
        fd.refresh_from_db()

    def test_the_schedule_is_linked_to_its_deposit(self):
        fd, schedule = self.make()
        self.assertEqual(schedule.payout_for, fd)
        self.assertEqual(list(fd.payout_schedules.all()), [schedule])

    def test_a_deposit_booked_as_already_closed_pays_out_until_then(self):
        closed = self.today - relativedelta(months=2)
        _fd, schedule = self.make(deposit_closed_date=closed.isoformat())
        self.assertEqual(schedule.end_date, closed)

    def test_closing_the_deposit_early_ends_its_schedule_that_day(self):
        fd, schedule = self.make()
        closed = self.today + relativedelta(months=1)
        self.edit(fd, deposit_closed_date=closed.isoformat())
        schedule.refresh_from_db()
        self.assertEqual(schedule.end_date, closed)
        self.assertTrue(schedule.is_active)

    def test_no_payout_is_recorded_after_the_day_it_was_closed(self):
        fd, schedule = self.make(create_historical_entries='on')
        closed = self.start + relativedelta(months=2, days=10)         # two payouts were due before it
        self.edit(fd, deposit_closed_date=closed.isoformat())
        process_user_recurring_transactions(self.user)
        dates = sorted(Income.objects.filter(user=self.user, source='Interest Income').values_list('date', flat=True))
        self.assertEqual(dates, [self.start + relativedelta(months=1), self.start + relativedelta(months=2)])
        schedule.refresh_from_db()
        self.assertFalse(schedule.is_active)

    def test_payouts_due_before_the_closing_day_are_still_recorded(self):
        fd, _schedule = self.make(create_historical_entries='on')
        self.edit(fd, deposit_closed_date=(self.today - relativedelta(days=1)).isoformat())
        process_user_recurring_transactions(self.user)
        self.assertEqual(Income.objects.filter(user=self.user, source='Interest Income').count(), 5)

    def test_a_longer_maturity_date_moves_the_end_of_the_schedule(self):
        fd, schedule = self.make()
        later = self.maturity + relativedelta(years=1)
        self.edit(fd, deposit_maturity_date=later.isoformat())
        schedule.refresh_from_db()
        self.assertEqual(schedule.end_date, later)

    def test_a_schedule_that_had_run_out_starts_again_when_the_deposit_is_renewed(self):
        fd, schedule = self.make()
        RecurringTransaction.objects.filter(pk=schedule.pk).update(
            end_date=self.today - relativedelta(days=5), is_active=False)
        self.edit(fd, deposit_maturity_date=(self.today + relativedelta(years=1)).isoformat())
        schedule.refresh_from_db()
        self.assertTrue(schedule.is_active)
        self.assertEqual(schedule.end_date, self.today + relativedelta(years=1))

    def test_a_deposit_that_stops_paying_out_stops_its_schedule(self):
        fd, schedule = self.make()
        self.edit(fd, deposit_payout='')
        schedule.refresh_from_db()
        self.assertFalse(schedule.is_active)

    def test_paying_out_again_starts_the_schedule_again(self):
        fd, schedule = self.make()
        self.edit(fd, deposit_payout='')
        self.edit(fd, deposit_payout='MONTHLY')
        schedule.refresh_from_db()
        self.assertTrue(schedule.is_active)

    def test_a_schedule_the_person_paused_stays_paused_when_something_else_is_edited(self):
        fd, schedule = self.make()
        RecurringTransaction.objects.filter(pk=schedule.pk).update(is_active=False)
        self.edit(fd, name='Renamed FD')
        schedule.refresh_from_db()
        self.assertFalse(schedule.is_active)

    def test_another_deposits_schedule_is_left_alone(self):
        fd, _schedule = self.make()
        other, other_schedule = self.make(name='Other FD')
        self.edit(fd, deposit_closed_date=(self.today + relativedelta(months=1)).isoformat())
        other_schedule.refresh_from_db()
        self.assertEqual(other_schedule.end_date, self.maturity)

    def test_a_deposit_that_does_not_pay_out_is_unaffected_by_edits(self):
        result = self.run_flow('fd', self.payload(interest_payout='', payout_account='', name='Plain FD'))
        fd = [obj for obj in result.created if isinstance(obj, Account)][0]
        self.edit(fd, deposit_closed_date=(self.today + relativedelta(months=1)).isoformat())
        self.assertEqual(fd.payout_schedules.count(), 0)

    def test_deleting_the_deposit_deletes_its_schedule(self):
        fd, schedule = self.make()
        fd.delete()
        self.assertFalse(RecurringTransaction.objects.filter(pk=schedule.pk).exists())

    def test_a_deactivated_deposit_stops_its_schedule_and_records_nothing(self):
        fd, schedule = self.make(create_historical_entries='on')
        Account.objects.filter(pk=fd.pk).update(is_active=False)
        process_user_recurring_transactions(self.user)
        schedule.refresh_from_db()
        self.assertFalse(schedule.is_active)
        self.assertEqual(Income.objects.filter(user=self.user, source='Interest Income').count(), 0)


class MigrationLinkTests(FlowTestBase):
    """Schedules made before the link existed are tied to the deposit they were named after."""

    def setUp(self):
        super().setUp()
        import importlib
        self.link = importlib.import_module(
            'expenses.migrations.0140_recurringtransaction_payout_for').link_existing_payout_schedules

    def deposit(self, name='Old FD'):
        return Account.objects.create(user=self.user, name=name, account_type='FD', balance=D('0'), currency='₹',
                                      deposit_payout='MONTHLY')

    def schedule(self, description):
        return RecurringTransaction.objects.create(
            user=self.user, transaction_type='INCOME', amount=D('1000'), currency='₹', account=self.bank,
            source='Interest Income', description=description, frequency='MONTHLY', start_date=date(2026, 1, 1),
            is_active=True)

    def run_link(self):
        from django.apps import apps
        self.link(apps, None)

    def test_an_old_schedule_is_linked_to_the_deposit_it_was_named_after(self):
        fd, schedule = self.deposit(), self.schedule('Interest payout: Old FD')
        self.run_link()
        schedule.refresh_from_db()
        self.assertEqual(schedule.payout_for, fd)

    def test_the_hindi_and_marathi_names_are_linked_too(self):
        hindi, marathi = self.deposit('हिंदी FD'), self.deposit('मराठी FD')
        a, b = self.schedule('ब्याज भुगतान: हिंदी FD'), self.schedule('व्याज payout: मराठी FD')
        self.run_link()
        a.refresh_from_db()
        b.refresh_from_db()
        self.assertEqual((a.payout_for, b.payout_for), (hindi, marathi))

    def test_a_schedule_named_after_nothing_is_left_alone(self):
        self.deposit()
        schedule = self.schedule('Interest payout: Some other FD')
        self.run_link()
        schedule.refresh_from_db()
        self.assertIsNone(schedule.payout_for)

    def test_other_income_schedules_are_left_alone(self):
        self.deposit()
        salary = RecurringTransaction.objects.create(
            user=self.user, transaction_type='INCOME', amount=D('50000'), currency='₹', account=self.bank,
            source='Salary', description='Interest payout: Old FD', frequency='MONTHLY', start_date=date(2026, 1, 1))
        self.run_link()
        salary.refresh_from_db()
        self.assertIsNone(salary.payout_for)

    def test_a_deposit_that_does_not_pay_out_is_not_linked(self):
        Account.objects.create(user=self.user, name='Old FD', account_type='FD', balance=D('0'), currency='₹')
        schedule = self.schedule('Interest payout: Old FD')
        self.run_link()
        schedule.refresh_from_db()
        self.assertIsNone(schedule.payout_for)
