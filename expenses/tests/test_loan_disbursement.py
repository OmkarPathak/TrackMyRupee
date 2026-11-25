"""The loan amount (or a top-up) arriving in an account: it is recorded as money received, never as income, it raises
the account's balance and the loan's liability in the ledger, and everything that reads the account (its activity, its
reconciliation, deleting the loan) knows about it."""
import uuid
from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from expenses.flows.registry import FlowRegistry
from expenses.models import Account, FXRate, Income, JournalEntry, JournalLine, Loan, LoanDisbursement, UserProfile
from expenses.tests.test_flows_end_to_end import FlowTestBase, valid_payloads

D = Decimal


class Base(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('borrower', password='pw')
        profile, _ = UserProfile.objects.get_or_create(user=self.user)
        profile.tier = 'PRO'
        profile.currency = '₹'
        profile.save()
        self.user.refresh_from_db()
        self.client.force_login(self.user)
        self.bank = Account.objects.create(user=self.user, name='HDFC Savings', account_type='SAVINGS_ACCOUNT', balance=D('10000'), currency='₹')
        self.loan = Loan.objects.create(user=self.user, name='Car Loan', loan_type='CAR', initial_principal=D('500000'),
                                        duration_months=60, start_date=date(2026, 9, 1), currency='₹')

    def receive(self, amount='500000', account=None, **extra):
        return LoanDisbursement.objects.create(loan=self.loan, account=account or self.bank, amount=D(amount), date=date(2026, 9, 1), **extra)

    def balance(self, account=None):
        account = account or self.bank
        account.refresh_from_db()
        return account.balance


class MoneyTests(Base):
    def test_the_amount_raises_the_accounts_balance(self):
        self.receive()
        self.assertEqual(self.balance(), D('510000'))

    def test_it_is_not_income(self):
        self.receive()
        self.assertFalse(Income.objects.filter(user=self.user).exists())

    def test_deleting_it_takes_the_amount_out_again(self):
        received = self.receive()
        received.delete()
        self.assertEqual(self.balance(), D('10000'))

    def test_deleting_the_loan_takes_it_out_again(self):
        self.receive()
        self.loan.delete()
        self.assertEqual(self.balance(), D('10000'))

    def test_an_amount_in_another_currency_is_converted_for_the_account(self):
        for (src, dst), rate in {('USD', 'INR'): '80', ('INR', 'USD'): '0.0125'}.items():
            FXRate.objects.update_or_create(from_currency=src, to_currency=dst, as_of_date=timezone.localdate(),
                                            defaults={'rate': D(rate), 'source': 'test'})
        cache.clear()
        dollars = Account.objects.create(user=self.user, name='USD Account', account_type='SAVINGS_ACCOUNT', balance=D('0'), currency='$')
        received = self.receive('100000', account=dollars)
        self.assertEqual(self.balance(dollars), D('1250.00'))
        received.delete()
        self.assertEqual(self.balance(dollars), D('0.00'))


class LedgerTests(Base):
    def lines(self, received):
        entry = JournalEntry.objects.filter(source_type='LOAN_DISBURSEMENT', source_id=received.id, status='POSTED').first()
        self.assertIsNotNone(entry, 'nothing was posted to the ledger')
        return {(line.ledger_account.account_type, line.direction): line.amount for line in JournalLine.objects.filter(journal_entry=entry)}

    def test_the_account_is_debited_and_the_loan_liability_credited(self):
        received = self.receive()
        self.assertEqual(self.lines(received), {('ASSET', 'DEBIT'): D('500000.00'), ('LIABILITY', 'CREDIT'): D('500000.00')})

    def test_deleting_it_reverses_the_posting(self):
        received = self.receive()
        received_id = received.id
        received.delete()
        net = D('0')
        for line in JournalLine.objects.filter(journal_entry__source_type='LOAN_DISBURSEMENT', journal_entry__source_id=received_id,
                                               ledger_account__account_type='ASSET'):
            net += line.amount if line.direction == 'DEBIT' else -line.amount
        self.assertEqual(net, D('0'))


class RuleTests(Base):
    def test_the_amount_must_be_more_than_zero(self):
        with self.assertRaises(ValidationError):
            self.receive('0')

    def test_it_needs_an_account(self):
        with self.assertRaises(ValidationError):
            LoanDisbursement.objects.create(loan=self.loan, account=None, amount=D('100'), date=date(2026, 9, 1))

    def test_the_account_must_be_the_persons_own(self):
        other = User.objects.create_user('someone-else', password='pw')
        theirs = Account.objects.create(user=other, name='Theirs', account_type='SAVINGS_ACCOUNT', balance=D('0'), currency='₹')
        with self.assertRaises(ValidationError):
            self.receive(account=theirs)

    def test_the_date_and_note_can_be_changed_but_not_the_amount_or_account(self):
        received = self.receive()
        received.note = 'Disbursed by the bank'
        received.save()
        received.amount = D('1')
        with self.assertRaises(ValidationError):
            received.save()
        self.assertEqual(self.balance(), D('510000'))


class ReadingTests(Base):
    def test_the_account_reconciliation_counts_it(self):
        from expenses.reconciliation import dated_flows
        self.receive()
        flows = dated_flows(self.bank)
        self.assertEqual([(f.kind, f.amount) for f in flows if f.kind == 'loan'], [('loan', D('500000.00'))])

    def test_the_account_page_lists_it_as_money_in(self):
        self.receive()
        html = self.client.get(reverse('account-detail', kwargs={'pk': self.bank.uuid})).content.decode()
        self.assertIn('Loan received: Car Loan', html)

    def test_an_account_that_received_it_counts_as_having_activity(self):
        from expenses.forms import AccountForm
        self.receive()
        self.assertTrue(AccountForm(instance=self.bank, user=self.user)._has_activity())


class LoanPageTests(Base):
    def url(self, name, *args):
        return reverse(name, args=args or [self.loan.uuid])

    def test_the_loan_page_offers_to_record_it_and_lists_what_was_received(self):
        html = self.client.get(self.url('loan-detail')).content.decode()
        self.assertIn('Record amount received', html)
        self.assertIn('No amount recorded as received yet', html)
        self.receive()
        html = self.client.get(self.url('loan-detail')).content.decode()
        self.assertIn('HDFC Savings', html)
        self.assertNotIn('No amount recorded as received yet', html)

    def test_recording_it_from_the_loan_page_raises_the_balance(self):
        response = self.client.post(self.url('loan-disbursement-create'),
                                    {'account': self.bank.pk, 'amount': '500000', 'date': '2026-09-01', 'note': ''})
        self.assertRedirects(response, self.url('loan-detail'))
        self.assertEqual(self.balance(), D('510000'))
        self.assertEqual(self.loan.disbursements.count(), 1)

    def test_the_same_amount_sent_twice_in_a_moment_is_recorded_once(self):
        data = {'account': self.bank.pk, 'amount': '500000', 'date': '2026-09-01', 'note': ''}
        self.client.post(self.url('loan-disbursement-create'), data)
        self.client.post(self.url('loan-disbursement-create'), data)
        self.assertEqual(self.loan.disbursements.count(), 1)
        self.assertEqual(self.balance(), D('510000'))

    def test_a_bad_amount_records_nothing(self):
        self.client.post(self.url('loan-disbursement-create'), {'account': self.bank.pk, 'amount': '0', 'date': '2026-09-01'})
        self.assertEqual(self.loan.disbursements.count(), 0)
        self.assertEqual(self.balance(), D('10000'))

    def test_another_persons_loan_is_not_found(self):
        other = User.objects.create_user('other-person', password='pw')
        profile, _ = UserProfile.objects.get_or_create(user=other)
        profile.tier = 'PRO'
        profile.save()
        other.refresh_from_db()
        self.client.force_login(other)
        response = self.client.post(self.url('loan-disbursement-create'), {'account': self.bank.pk, 'amount': '1', 'date': '2026-09-01'})
        self.assertEqual(response.status_code, 404)

    def test_deleting_it_from_the_page_takes_the_amount_out_again(self):
        received = self.receive()
        response = self.client.post(self.url('loan-disbursement-delete', received.uuid))
        self.assertEqual(response.status_code, 302)
        self.assertFalse(LoanDisbursement.objects.filter(pk=received.pk).exists())
        self.assertEqual(self.balance(), D('10000'))

    def test_the_form_starts_with_what_is_still_to_be_recorded(self):
        from expenses.forms import LoanDisbursementForm
        self.assertEqual(LoanDisbursementForm(user=self.user, loan=self.loan).fields['amount'].initial, D('500000'))
        self.receive('200000')
        self.assertEqual(LoanDisbursementForm(user=self.user, loan=self.loan).fields['amount'].initial, D('300000'))


class LoanFlowTests(FlowTestBase):
    def payload(self, **extra):
        data = dict(valid_payloads(self)['loan'])
        data.update(extra)
        return data

    def commit(self, **extra):
        return self.run_flow('loan', self.payload(**extra))

    def test_the_amount_is_added_to_the_chosen_account_when_it_is_on(self):
        before = Account.objects.get(pk=self.bank.pk).balance
        self.commit(record_loan_received='on', loan_received_account=self.bank.pk)
        loan = Loan.objects.get(user=self.user, name='Home Loan')
        received = loan.disbursements.get()
        self.assertEqual((received.account_id, received.amount, received.date), (self.bank.pk, D('1200000'), date(2026, 10, 1)))
        self.assertEqual(Account.objects.get(pk=self.bank.pk).balance, before + D('1200000'))

    def test_nothing_is_added_when_it_is_off(self):
        before = Account.objects.get(pk=self.bank.pk).balance
        self.commit()
        self.assertFalse(LoanDisbursement.objects.exists())
        self.assertEqual(Account.objects.get(pk=self.bank.pk).balance, before)

    def test_a_loan_taken_long_ago_never_adds_the_amount(self):
        before = Account.objects.get(pk=self.bank.pk).balance
        self.commit(record_loan_received='on', loan_received_account=self.bank.pk, mid_tenure='on', opening_paid_principal='100000')
        self.assertFalse(LoanDisbursement.objects.exists())
        self.assertEqual(Account.objects.get(pk=self.bank.pk).balance, before)

    def test_it_asks_for_the_account_when_it_is_on(self):
        form = self.form('loan', self.payload(record_loan_received='on'))
        self.assertFalse(form.is_valid())
        self.assertIn('loan_received_account', form.errors)

    def test_the_account_is_not_asked_for_when_it_is_off(self):
        self.assertTrue(self.form('loan', self.payload()).is_valid())

    def test_the_form_starts_with_it_on_and_the_usual_account_chosen(self):
        fields = FlowRegistry.get('loan').form_class(user=self.user).fields
        self.assertTrue(fields['record_loan_received'].initial)
        self.assertIsNotNone(fields['loan_received_account'].initial)

    def test_the_preview_says_where_the_amount_goes(self):
        flow = FlowRegistry.get('loan')
        form = self.form('loan', self.payload(record_loan_received='on', loan_received_account=self.bank.pk))
        self.assertTrue(form.is_valid(), form.errors)
        bullets = [str(b) for b in flow.preview(self.user, form.cleaned_data)['bullets']]
        self.assertTrue(any('Adds the loan amount to Bank' in b for b in bullets), bullets)

    def test_the_review_lists_the_money_received_among_what_will_be_created(self):
        flow = FlowRegistry.get('loan')
        on = self.form('loan', self.payload(record_loan_received='on', loan_received_account=self.bank.pk))
        self.assertTrue(on.is_valid(), on.errors)
        cards = flow.get_what_we_create(self.user, on.cleaned_data)
        self.assertIn('Money received', [str(c['title']) for c in cards])
        self.assertIn('added to Bank', [c['description'] for c in cards if str(c['title']) == 'Money received'][0])
        off = self.form('loan', self.payload())
        self.assertTrue(off.is_valid(), off.errors)
        self.assertNotIn('Money received', [str(c['title']) for c in flow.get_what_we_create(self.user, off.cleaned_data)])

    def test_running_the_same_setup_twice_adds_the_amount_once(self):
        payload = self.payload(record_loan_received='on', loan_received_account=self.bank.pk)
        idem = uuid.uuid4()
        self.run_flow('loan', payload, idem)
        self.run_flow('loan', payload, idem)
        self.assertEqual(LoanDisbursement.objects.count(), 1)


class AccountSummaryTests(Base):
    def test_the_accounts_money_in_total_counts_what_was_received(self):
        self.receive()
        context = self.client.get(reverse('account-detail', kwargs={'pk': self.bank.uuid})).context
        self.assertEqual(context['money_in'], D('500000.00'))
