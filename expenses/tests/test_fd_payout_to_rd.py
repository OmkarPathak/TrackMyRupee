"""An FD that pays its interest out every month can put each payout into a recurring deposit (RD), so the interest earns
interest too. The payout schedules (the interest income and the RD transfer) follow the deposit when it is edited."""
from decimal import Decimal

from dateutil.relativedelta import relativedelta
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from expenses.flows.registry import FlowRegistry
from expenses.forms import AccountForm
from expenses.models import Account, Income, RecurringTransaction, Transfer
from expenses.tests.test_flows_end_to_end import FlowTestBase
from expenses.views.mixins import process_user_recurring_transactions

D = Decimal


class Base(FlowTestBase):
    def setUp(self):
        super().setUp()
        self.today = timezone.localdate()
        self.start = self.today
        self.first = self.start + relativedelta(months=1)

    def payload(self, **overrides):
        data = dict(name='Linked FD', principal='1200000', annual_rate='7.5', deposit_start_date=self.start.isoformat(),
                    maturity_date=(self.start + relativedelta(years=2)).isoformat(), deposit_compounding='QUARTERLY',
                    from_account=self.cash, interest_payout='MONTHLY', payout_account=self.bank, payout_to_rd='on',
                    rd_rate='6.5', rd_tenure_months='36', rd_compounding='QUARTERLY')
        data.update(overrides)
        return data

    def make(self, **overrides):
        result = self.run_flow('fd', self.payload(**overrides))
        accounts = {a.account_type: a for a in self.created(result, Account)}
        schedules = self.created(result, RecurringTransaction)
        income = next((s for s in schedules if s.transaction_type == 'INCOME'), None)
        transfer = next((s for s in schedules if s.transaction_type == 'TRANSFER'), None)
        return accounts['FD'], accounts.get('RD'), income, transfer

    def edit(self, fd, **changes):
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


class CreationTests(Base):
    def test_it_creates_the_rd_and_the_transfer_that_feeds_it(self):
        fd, rd, income, transfer = self.make()
        self.assertEqual((rd.account_type, rd.deposit_rate, rd.deposit_compounding, rd.deposit_start_date,
                          rd.rd_installment_amount, rd.rd_installment_day, rd.name),
                         ('RD', D('6.50'), 'QUARTERLY', self.first, D('7500.00'), min(self.first.day, 28),
                          'Linked FD interest RD'))
        self.assertEqual(rd.deposit_maturity_date, self.first + relativedelta(months=36))
        self.assertEqual((transfer.transaction_type, transfer.amount, transfer.frequency, transfer.from_account,
                          transfer.to_account, transfer.start_date, transfer.payout_for),
                         ('TRANSFER', D('7500.00'), 'MONTHLY', self.bank, rd, self.first, fd))
        self.assertEqual(income.payout_for, fd)

    def test_the_transfer_carries_the_same_amount_as_the_payout(self):
        _fd, _rd, income, transfer = self.make()
        self.assertEqual(income.amount, transfer.amount)

    def test_a_typed_payout_amount_is_what_goes_into_the_rd(self):
        _fd, rd, income, transfer = self.make(payout_amount='7000')
        self.assertEqual((income.amount, transfer.amount, rd.rd_installment_amount), (D('7000.00'),) * 3)

    def test_the_rd_name_can_be_chosen(self):
        _fd, rd, _income, transfer = self.make(rd_name='Interest RD')
        self.assertEqual(rd.name, 'Interest RD')
        self.assertIn('Interest RD', transfer.description)

    def test_a_shorter_rd_ends_its_transfer_before_it_matures(self):
        _fd, rd, _income, transfer = self.make(rd_tenure_months='12')
        self.assertEqual(rd.deposit_maturity_date, self.first + relativedelta(months=12))
        self.assertEqual(transfer.end_date, rd.deposit_maturity_date - relativedelta(days=1))

    def test_a_longer_rd_gets_payouts_only_while_the_fd_pays(self):
        fd, _rd, income, transfer = self.make(rd_tenure_months='60')
        self.assertEqual(transfer.end_date, fd.deposit_maturity_date)
        self.assertEqual(income.end_date, fd.deposit_maturity_date)

    def test_without_the_option_there_is_no_rd(self):
        fd, rd, income, transfer = self.make(payout_to_rd='')
        self.assertIsNone(rd)
        self.assertIsNone(transfer)
        self.assertIsNotNone(income)

    def test_an_rd_is_for_monthly_payouts_only(self):
        fd, rd, income, transfer = self.make(interest_payout='QUARTERLY')
        self.assertIsNone(rd)
        self.assertEqual(income.frequency, 'QUARTERLY')

    def test_the_rd_is_funded_from_the_account_that_receives_the_interest(self):
        _fd, _rd, income, transfer = self.make(payout_account=self.cash)
        self.assertEqual((income.account, transfer.from_account), (self.cash, self.cash))

    def test_the_rd_counts_in_the_plans_limits(self):
        flow = FlowRegistry.get('fd')
        form = self.valid_form('fd', self.payload())
        steps = flow.plan(flow.derive({**form.cleaned_data, 'user': self.user}))
        self.assertEqual(sum(1 for step in steps if step.model is Account), 2)
        self.assertEqual(sum(1 for step in steps if step.model is RecurringTransaction), 2)


class ValidationTests(Base):
    def test_the_rate_and_the_tenure_are_needed(self):
        self.assertErrorOn('fd', self.payload(rd_rate=''), 'rd_rate')
        self.assertErrorOn('fd', self.payload(rd_tenure_months=''), 'rd_tenure_months')

    def test_a_rate_of_zero_and_a_tenure_of_zero_are_refused(self):
        self.assertErrorOn('fd', self.payload(rd_rate='0'), 'rd_rate')
        self.assertErrorOn('fd', self.payload(rd_tenure_months='0'), 'rd_tenure_months')

    def test_a_tenure_over_ten_years_is_refused(self):
        self.assertErrorOn('fd', self.payload(rd_tenure_months='121'), 'rd_tenure_months')

    def test_the_rd_needs_a_different_name_from_the_deposit(self):
        self.assertErrorOn('fd', self.payload(rd_name='linked fd'), 'rd_name')

    def test_the_rd_cannot_take_the_name_of_an_existing_account(self):
        self.assertErrorOn('fd', self.payload(rd_name='Bank'), 'rd_name')

    def test_the_rd_fields_are_not_needed_when_the_option_is_off(self):
        self.valid_form('fd', self.payload(payout_to_rd='', rd_rate='', rd_tenure_months=''))

    def test_the_rd_fields_are_ignored_for_a_payout_that_is_not_monthly(self):
        form = self.valid_form('fd', self.payload(interest_payout='ANNUAL', rd_rate='', rd_tenure_months=''))
        self.assertFalse(form.cleaned_data['payout_to_rd'])

    def test_the_rd_fields_are_ignored_without_a_payout(self):
        form = self.valid_form('fd', self.payload(interest_payout='', payout_account='', rd_rate=''))
        self.assertFalse(form.cleaned_data['payout_to_rd'])


class ReviewTests(Base):
    def test_the_review_lists_the_rd(self):
        flow = FlowRegistry.get('fd')
        form = self.valid_form('fd', self.payload())
        cards = {str(card['title']): str(card['description']) for card in flow.get_what_we_create(self.user, form.cleaned_data)}
        self.assertEqual(cards['Recurring deposit'], '₹7,500 for 36 months')

    def test_the_review_has_no_rd_card_without_the_option(self):
        flow = FlowRegistry.get('fd')
        form = self.valid_form('fd', self.payload(payout_to_rd=''))
        titles = [str(card['title']) for card in flow.get_what_we_create(self.user, form.cleaned_data)]
        self.assertNotIn('Recurring deposit', titles)

    def test_the_preview_says_where_the_payouts_go(self):
        flow = FlowRegistry.get('fd')
        form = self.valid_form('fd', self.payload())
        text = ' '.join(str(b) for b in flow.preview(self.user, form.cleaned_data)['bullets'])
        self.assertIn('Linked FD interest RD', text)
        self.assertIn('36 months', text)

    def test_the_setup_page_has_the_rd_step_and_its_fields(self):
        html = self.client.get(reverse('flow-detail', kwargs={'key': 'fd'})).content.decode()
        for name in ('payout_to_rd', 'rd_name', 'rd_rate', 'rd_tenure_months', 'rd_compounding'):
            self.assertIn(f'name="{name}"', html)
        self.assertIn('data-step-key="fd_rd"', html)


class BackfillTests(Base):
    def setUp(self):
        super().setUp()
        self.start = self.today - relativedelta(months=3, days=5)
        self.first = self.start + relativedelta(months=1)

    def test_a_backfill_records_the_payouts_and_the_rd_instalments_together(self):
        fd, rd, _income, _transfer = self.make(create_historical_entries='on')
        process_user_recurring_transactions(self.user)
        self.assertEqual(Income.objects.filter(user=self.user, source='Interest Income').count(), 3)
        self.assertEqual(Transfer.objects.filter(to_account=rd).count(), 3)
        self.assertEqual(sum(t.amount for t in Transfer.objects.filter(to_account=rd)), D('22500.00'))

    def test_the_account_receiving_the_interest_ends_where_it_started(self):
        """Each payout comes in and goes straight into the RD."""
        self.make(create_historical_entries='on')
        before = Account.objects.get(pk=self.bank.pk).balance
        process_user_recurring_transactions(self.user)
        from expenses.account_valuation import get_current
        self.assertEqual(get_current(Account.objects.get(pk=self.bank.pk)), before)

    def test_the_rd_is_worth_what_was_put_in_and_a_little_more(self):
        _fd, rd, _income, _transfer = self.make(create_historical_entries='on')
        process_user_recurring_transactions(self.user)
        from expenses.account_valuation import get_baseline, get_current
        rd.refresh_from_db()
        self.assertEqual(get_baseline(rd), D('22500.00'))
        self.assertGreater(get_current(rd), D('22500.00'))

    def test_without_the_option_nothing_past_is_recorded(self):
        _fd, rd, income, transfer = self.make()
        process_user_recurring_transactions(self.user)
        self.assertEqual(Transfer.objects.filter(to_account=rd).count(), 0)
        self.assertEqual(Income.objects.filter(user=self.user, source='Interest Income').count(), 0)
        self.assertIsNotNone(income.last_processed_date)
        self.assertIsNotNone(transfer.last_processed_date)


class FollowsTheDepositTests(Base):
    def test_closing_the_deposit_early_ends_the_interest_and_the_rd_transfer(self):
        fd, _rd, income, transfer = self.make()
        closed = self.today + relativedelta(months=3)
        self.edit(fd, deposit_closed_date=closed.isoformat())
        income.refresh_from_db(); transfer.refresh_from_db()
        self.assertEqual((income.end_date, transfer.end_date), (closed, closed))

    def test_a_longer_maturity_moves_the_interest_but_the_transfer_still_stops_before_the_rd_matures(self):
        fd, rd, income, transfer = self.make(rd_tenure_months='12')
        later = self.start + relativedelta(years=5)
        self.edit(fd, deposit_maturity_date=later.isoformat())
        income.refresh_from_db(); transfer.refresh_from_db()
        self.assertEqual(income.end_date, later)
        self.assertEqual(transfer.end_date, rd.deposit_maturity_date - relativedelta(days=1))

    def test_a_shorter_maturity_ends_the_transfer_with_it(self):
        fd, _rd, income, transfer = self.make(rd_tenure_months='60')
        sooner = self.start + relativedelta(months=18)
        self.edit(fd, deposit_maturity_date=sooner.isoformat())
        transfer.refresh_from_db()
        self.assertEqual(transfer.end_date, sooner)

    def test_a_deposit_that_stops_paying_out_stops_both(self):
        fd, _rd, income, transfer = self.make()
        self.edit(fd, deposit_payout='')
        income.refresh_from_db(); transfer.refresh_from_db()
        self.assertFalse(income.is_active)
        self.assertFalse(transfer.is_active)

    def test_paying_out_again_starts_both_again(self):
        fd, _rd, income, transfer = self.make()
        self.edit(fd, deposit_payout='')
        self.edit(fd, deposit_payout='MONTHLY')
        income.refresh_from_db(); transfer.refresh_from_db()
        self.assertTrue(income.is_active)
        self.assertTrue(transfer.is_active)

    def test_changing_to_a_quarterly_payout_changes_the_interest_and_stops_the_rd_transfer(self):
        fd, _rd, income, transfer = self.make()
        self.edit(fd, deposit_payout='QUARTERLY')
        income.refresh_from_db(); transfer.refresh_from_db()
        self.assertEqual((income.frequency, income.amount), ('QUARTERLY', D('22500.00')))
        self.assertFalse(transfer.is_active)

    def test_deleting_the_deposit_deletes_both_schedules_and_keeps_the_rd(self):
        fd, rd, income, transfer = self.make()
        fd.delete()
        self.assertFalse(RecurringTransaction.objects.filter(pk__in=[income.pk, transfer.pk]).exists())
        self.assertTrue(Account.objects.filter(pk=rd.pk).exists())


class PayoutAmountFollowsTests(Base):
    """An amount that was worked out from the rate follows the principal, the rate and the period; a typed one does not."""

    def test_a_bigger_principal_raises_the_worked_out_amount(self):
        fd, _rd, income, transfer = self.make()
        self.edit(fd, deposit_principal='2400000')
        income.refresh_from_db(); transfer.refresh_from_db()
        self.assertEqual((income.amount, transfer.amount), (D('15000.00'), D('15000.00')))

    def test_the_rd_instalment_follows_the_new_amount(self):
        fd, rd, _income, _transfer = self.make()
        self.edit(fd, deposit_principal='2400000')
        rd.refresh_from_db()
        self.assertEqual(rd.rd_installment_amount, D('15000.00'))

    def test_a_new_rate_changes_the_worked_out_amount(self):
        fd, _rd, income, _transfer = self.make()
        self.edit(fd, deposit_rate='9')
        income.refresh_from_db()
        self.assertEqual(income.amount, D('9000.00'))

    def test_a_typed_amount_is_left_alone(self):
        fd, rd, income, transfer = self.make(payout_amount='7000')
        self.edit(fd, deposit_principal='2400000')
        income.refresh_from_db(); transfer.refresh_from_db(); rd.refresh_from_db()
        self.assertEqual((income.amount, transfer.amount, rd.rd_installment_amount), (D('7000.00'),) * 3)

    def test_an_edit_that_changes_neither_leaves_the_amount_alone(self):
        fd, _rd, income, _transfer = self.make()
        self.edit(fd, name='Renamed FD')
        income.refresh_from_db()
        self.assertEqual(income.amount, D('7500.00'))

    def test_without_an_rd_the_interest_amount_still_follows(self):
        fd, _rd, income, transfer = self.make(payout_to_rd='')
        self.edit(fd, deposit_principal='2400000')
        income.refresh_from_db()
        self.assertEqual(income.amount, D('15000.00'))
        self.assertIsNone(transfer)


class StepTests(Base):
    def test_the_rd_step_has_a_circle_for_the_phone(self):
        html = self.client.get(reverse('flow-detail', kwargs={'key': 'fd'})).content.decode()
        self.assertIn('data-title="Recurring deposit"', html)
