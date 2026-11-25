"""The Top up screen: the form, recording a top-up (with the money received, a new rate and added months), deleting it,
the EMI the loan page suggests afterwards and the button that applies it, and the data export."""
import csv
import io
import zipfile
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.contrib.messages import get_messages
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from expenses.forms import LoanForm, LoanTopUpForm
from expenses.models import Account, Loan, LoanDisbursement, LoanInterestRate, LoanRepayment, LoanTopUp, RecurringTransaction, UserProfile
from expenses.services import LoanService
from expenses.views.mixins import process_user_recurring_transactions

D = Decimal


class Base(TestCase):
    def setUp(self):
        self.user = self.make_user('topper')
        self.client.force_login(self.user)
        self.today = timezone.localdate()
        self.bank = Account.objects.create(user=self.user, name='HDFC Savings', account_type='SAVINGS_ACCOUNT', balance=D('100000'), currency='₹')
        self.loan = Loan.objects.create(user=self.user, name='Home Loan', loan_type='HOME', initial_principal=D('1000000'),
                                        duration_months=120, start_date=self.today - timedelta(days=200), currency='₹')
        LoanInterestRate.objects.create(loan=self.loan, interest_rate=D('9'), effective_date=self.loan.start_date)

    def make_user(self, name, tier='PRO'):
        user = User.objects.create_user(name, password='pw')
        profile, _ = UserProfile.objects.get_or_create(user=user)
        profile.tier = tier
        profile.currency = '₹'
        profile.save()
        user.refresh_from_db()
        return user

    def url(self, name, *args):
        return reverse(name, args=args or [self.loan.uuid])

    def post(self, **fields):
        data = {'date': (self.today - timedelta(days=1)).isoformat(), 'amount': '200000'}
        data.update(fields)
        return self.client.post(self.url('loan-topup-create'), data)

    def fresh(self):
        return Loan.objects.get(pk=self.loan.pk)

    def messages(self, response):
        return [str(m) for m in get_messages(response.wsgi_request)]

    def emi_schedule(self, amount='13000', active=True):
        return RecurringTransaction.objects.create(
            user=self.user, transaction_type='LOAN', amount=D(amount), base_amount=D(amount), currency='₹', account=self.bank,
            loan=self.loan, frequency='MONTHLY', start_date=self.loan.start_date, is_active=active,
            last_processed_date=self.today - timedelta(days=5), description='Loan EMI: Home Loan')


class FormTests(Base):
    def form(self, **fields):
        data = {'date': self.today.isoformat(), 'amount': '100000'}
        data.update(fields)
        return LoanTopUpForm(data, user=self.user, loan=self.loan)

    def test_the_amount_and_date_are_enough(self):
        self.assertTrue(self.form().is_valid())

    def test_the_amount_must_be_more_than_zero(self):
        self.assertIn('amount', self.form(amount='0').errors)

    def test_it_cannot_be_dated_before_the_loan_or_in_the_future(self):
        self.assertIn('date', self.form(date=(self.loan.start_date - timedelta(days=1)).isoformat()).errors)
        self.assertIn('date', self.form(date=(self.today + timedelta(days=5)).isoformat()).errors)

    def test_the_whole_term_cannot_pass_six_hundred_months(self):
        self.assertIn('extra_months', self.form(extra_months='481').errors)
        self.assertTrue(self.form(extra_months='480').is_valid())

    def test_a_new_rate_must_be_between_zero_and_a_hundred(self):
        self.assertIn('new_rate', self.form(new_rate='101').errors)
        self.assertTrue(self.form(new_rate='0').is_valid())

    def test_only_the_persons_own_accounts_can_receive_it(self):
        other = self.make_user('someone-else')
        theirs = Account.objects.create(user=other, name='Theirs', account_type='SAVINGS_ACCOUNT', balance=D('0'), currency='₹')
        self.assertIn('received_account', self.form(received_account=theirs.pk).errors)

    def test_a_loan_account_cannot_receive_it(self):
        card = Account.objects.create(user=self.user, name='Home loan acct', account_type='HOME_LOAN', balance=D('0'), currency='₹')
        self.assertIn('received_account', self.form(received_account=card.pk).errors)

    def test_saving_with_everything_records_everything(self):
        form = self.form(received_account=self.bank.pk, extra_months='24', new_rate='10.5', note='Renovation')
        self.assertTrue(form.is_valid(), form.errors)
        topup = form.save()
        self.assertEqual((topup.amount, topup.extra_months, topup.note), (D('100000.00'), 24, 'Renovation'))
        self.assertEqual(topup.disbursement.account_id, self.bank.pk)
        self.assertEqual(topup.disbursement.amount, D('100000.00'))
        self.assertEqual(sorted(str(r.interest_rate) for r in self.loan.interest_rates.all()), ['10.50', '9.00'])
        self.assertEqual(self.loan.interest_rates.get(interest_rate=D('10.5')).effective_date, self.today)
        self.bank.refresh_from_db()
        self.assertEqual(self.bank.balance, D('200000.00'))

    def test_saving_with_the_amount_alone_touches_nothing_else(self):
        form = self.form()
        form.is_valid()
        topup = form.save()
        self.assertIsNone(topup.disbursement)
        self.assertEqual(LoanDisbursement.objects.count(), 0)
        self.assertEqual(self.loan.interest_rates.count(), 1)
        self.bank.refresh_from_db()
        self.assertEqual(self.bank.balance, D('100000.00'))

    def test_if_one_part_fails_nothing_is_kept(self):
        form = self.form(received_account=self.bank.pk, new_rate='10')
        form.is_valid()
        with patch.object(LoanInterestRate.objects, 'create', side_effect=RuntimeError('boom')):
            with self.assertRaises(RuntimeError):
                form.save()
        self.assertEqual(LoanTopUp.objects.count(), 0)
        self.assertEqual(LoanDisbursement.objects.count(), 0)
        self.bank.refresh_from_db()
        self.assertEqual(self.bank.balance, D('100000.00'))

    def test_the_loan_form_points_a_person_editing_the_principal_to_top_up(self):
        form = LoanForm(instance=self.loan, user=self.user)
        self.assertIn('Top up', str(form.fields['initial_principal'].help_text))
        self.assertNotIn('Top up', str(LoanForm(user=self.user).fields['initial_principal'].help_text))


class RecordTests(Base):
    def test_a_top_up_is_recorded_and_what_is_owed_follows(self):
        response = self.post()
        self.assertRedirects(response, self.url('loan-detail'))
        topup = self.loan.topups.get()
        self.assertEqual(topup.amount, D('200000.00'))
        self.assertEqual(self.fresh().remaining_principal, D('1200000.00'))
        self.assertTrue(any('You now owe ₹12,00,000' in m for m in self.messages(response)))

    def test_it_can_put_the_money_into_an_account_and_start_a_new_rate_and_add_months(self):
        self.post(received_account=self.bank.pk, new_rate='10', extra_months='12')
        topup = self.loan.topups.get()
        self.assertEqual(topup.extra_months, 12)
        self.assertEqual(self.fresh().total_duration_months, 132)
        self.assertEqual(self.loan.interest_rates.count(), 2)
        self.bank.refresh_from_db()
        self.assertEqual(self.bank.balance, D('300000.00'))
        self.assertEqual(topup.disbursement.note, 'Top-up')

    def test_it_reopens_a_paid_off_loan(self):
        LoanRepayment.objects.create(loan=self.loan, from_account=self.bank, amount=D('1000000'), principal_portion=D('1000000'),
                                     interest_portion=D('0'), date=self.today - timedelta(days=2))
        self.assertFalse(self.fresh().is_active)
        self.post()
        self.assertTrue(self.fresh().is_active)

    def test_the_same_top_up_sent_twice_in_a_moment_is_recorded_once(self):
        self.post(received_account=self.bank.pk)
        self.post(received_account=self.bank.pk)
        self.assertEqual(self.loan.topups.count(), 1)
        self.bank.refresh_from_db()
        self.assertEqual(self.bank.balance, D('300000.00'))

    def test_a_bad_form_records_nothing_and_says_why(self):
        response = self.post(amount='0')
        self.assertEqual(self.loan.topups.count(), 0)
        self.assertTrue(any('Could not add the top-up' in m for m in self.messages(response)))

    def test_another_persons_loan_is_not_found(self):
        other = self.make_user('other-person')
        self.client.force_login(other)
        self.assertEqual(self.post().status_code, 404)

    def test_the_free_plan_is_sent_to_the_plans(self):
        UserProfile.objects.filter(user=self.user).update(tier='FREE')
        self.assertRedirects(self.post(), reverse('pricing'), fetch_redirect_response=False)

    def test_a_suggestion_about_the_emi_comes_with_it_when_there_is_a_schedule(self):
        self.emi_schedule('13000')
        response = self.post()
        self.assertTrue(any('the EMI works out to about' in m for m in self.messages(response)))

    def test_no_suggestion_without_a_schedule(self):
        response = self.post()
        self.assertFalse(any('the EMI works out to about' in m for m in self.messages(response)))


class DeleteTests(Base):
    def test_deleting_it_takes_everything_back(self):
        self.post(received_account=self.bank.pk, extra_months='12')
        topup = self.loan.topups.get()
        response = self.client.post(self.url('loan-topup-delete', topup.uuid))
        self.assertRedirects(response, self.url('loan-detail'))
        self.assertEqual(self.loan.topups.count(), 0)
        self.assertEqual(self.fresh().remaining_principal, D('1000000.00'))
        self.assertEqual(self.fresh().total_duration_months, 120)
        self.assertEqual(LoanDisbursement.objects.count(), 0)
        self.bank.refresh_from_db()
        self.assertEqual(self.bank.balance, D('100000.00'))

    def test_another_persons_top_up_cannot_be_deleted(self):
        self.post()
        topup = self.loan.topups.get()
        other = self.make_user('other-deleter')
        self.client.force_login(other)
        self.assertEqual(self.client.post(self.url('loan-topup-delete', topup.uuid)).status_code, 404)
        self.assertEqual(self.loan.topups.count(), 1)

    def test_a_get_only_goes_back_to_the_loan(self):
        self.post()
        topup = self.loan.topups.get()
        self.assertRedirects(self.client.get(self.url('loan-topup-delete', topup.uuid)), self.url('loan-detail'))
        self.assertEqual(self.loan.topups.count(), 1)


class SuggestionTests(Base):
    def test_the_emi_that_fits_the_balance_rate_and_months_left(self):
        self.post()
        loan = self.fresh()
        suggestion = LoanService.suggest_emi(loan)
        months_passed = max(0, (self.today.year - loan.start_date.year) * 12 + self.today.month - loan.start_date.month)
        expected = LoanService.calculate_emi(D('1200000'), D('9'), 120 - months_passed)
        self.assertEqual(suggestion['months'], 120 - months_passed)
        self.assertAlmostEqual(float(suggestion['emi']), expected, delta=0.01)

    def test_added_months_lower_it(self):
        self.post()
        plain = LoanService.suggest_emi(self.fresh())['emi']
        LoanTopUp.objects.all().delete()
        self.post(extra_months='60')
        self.assertLess(LoanService.suggest_emi(self.fresh())['emi'], plain)

    def test_a_bullet_loan_or_a_paid_off_one_has_none(self):
        bullet = Loan.objects.create(user=self.user, name='Bullet', loan_type='PERSONAL', initial_principal=D('50000'), duration_months=12,
                                     start_date=self.today - timedelta(days=30), currency='₹', repayment_type='BULLET')
        self.assertIsNone(LoanService.suggest_emi(bullet))
        done = Loan.objects.create(user=self.user, name='Done', loan_type='PERSONAL', initial_principal=D('50000'), duration_months=12,
                                   start_date=self.today - timedelta(days=30), currency='₹')
        LoanRepayment.objects.create(loan=done, from_account=self.bank, amount=D('50000'), principal_portion=D('50000'),
                                     interest_portion=D('0'), date=self.today)
        self.assertIsNone(LoanService.suggest_emi(Loan.objects.get(pk=done.pk)))

    def test_the_loan_page_offers_the_new_emi_after_a_top_up_and_not_before(self):
        self.emi_schedule('13000')
        before = self.client.get(self.url('loan-detail')).context['emi_card']
        self.assertFalse(before['offer'])
        self.post()
        after = self.client.get(self.url('loan-detail')).context['emi_card']
        self.assertTrue(after['offer'])
        self.assertTrue(after['differs'])
        self.assertContains(self.client.get(self.url('loan-detail')), reverse('loan-emi-update', args=[self.loan.uuid]))

    def test_no_card_without_a_schedule(self):
        self.post()
        self.assertIsNone(self.client.get(self.url('loan-detail')).context['emi_card'])

    def test_a_schedule_already_at_the_suggested_figure_is_not_offered_again(self):
        self.post()
        schedule = self.emi_schedule(str(LoanService.suggest_emi(self.fresh())['emi']))
        card = self.client.get(self.url('loan-detail')).context['emi_card']
        self.assertEqual(card['schedule'].pk, schedule.pk)
        self.assertFalse(card['offer'])


class UpdateEmiTests(Base):
    def test_it_sets_the_schedule_to_the_suggested_emi(self):
        schedule = self.emi_schedule('13000')
        self.post()
        expected = LoanService.suggest_emi(self.fresh())['emi']
        response = self.client.post(self.url('loan-emi-update'))
        self.assertRedirects(response, self.url('loan-detail'))
        schedule.refresh_from_db()
        self.assertEqual(schedule.amount, expected)
        self.assertTrue(any('Your EMI schedule now posts' in m for m in self.messages(response)))

    def test_it_restarts_a_schedule_the_payoff_had_stopped_without_posting_what_fell_due_meanwhile(self):
        LoanRepayment.objects.create(loan=self.loan, from_account=self.bank, amount=D('1000000'), principal_portion=D('1000000'),
                                     interest_portion=D('0'), date=self.today - timedelta(days=150))
        schedule = self.emi_schedule('13000', active=False)
        RecurringTransaction.objects.filter(pk=schedule.pk).update(last_processed_date=self.today - timedelta(days=150))
        self.post(date=self.today.isoformat())
        self.assertTrue(self.client.get(self.url('loan-detail')).context['emi_card']['stopped'])
        self.client.post(self.url('loan-emi-update'))
        schedule.refresh_from_db()
        self.assertTrue(schedule.is_active)
        before = self.loan.repayments.count()
        process_user_recurring_transactions(self.user, force=True)
        self.assertEqual(self.loan.repayments.count(), before)         # (nothing is caught up for the months it was stopped)

    def test_with_no_schedule_it_says_so_and_changes_nothing(self):
        self.post()
        response = self.client.post(self.url('loan-emi-update'))
        self.assertTrue(any('no EMI schedule' in m for m in self.messages(response)))

    def test_another_persons_loan_is_not_found(self):
        other = self.make_user('other-emi')
        self.client.force_login(other)
        self.assertEqual(self.client.post(self.url('loan-emi-update')).status_code, 404)


class PageTests(Base):
    def test_the_page_lists_the_top_ups_with_what_each_did(self):
        self.post(received_account=self.bank.pk, extra_months='12', note='Renovation')
        context = self.client.get(self.url('loan-detail')).context
        self.assertEqual([t.amount for t in context['topups']], [D('200000.00')])
        html = self.client.get(self.url('loan-detail')).content.decode()
        self.assertIn('Top-ups', html)
        self.assertIn('Renovation', html)
        self.assertIn('+12 months', html)

    def test_the_money_it_put_into_an_account_cannot_be_deleted_on_its_own(self):
        self.post(received_account=self.bank.pk)
        received = LoanDisbursement.objects.get()
        html = self.client.get(self.url('loan-detail')).content.decode()
        self.assertNotIn(reverse('loan-disbursement-delete', args=[received.uuid]), html)

    def test_a_plain_amount_received_can_still_be_deleted(self):
        received = LoanDisbursement.objects.create(loan=self.loan, account=self.bank, amount=D('1000000'), date=self.loan.start_date)
        html = self.client.get(self.url('loan-detail')).content.decode()
        self.assertIn(reverse('loan-disbursement-delete', args=[received.uuid]), html)

    def test_a_paid_off_loan_still_offers_top_up(self):
        LoanRepayment.objects.create(loan=self.loan, from_account=self.bank, amount=D('1000000'), principal_portion=D('1000000'),
                                     interest_portion=D('0'), date=self.today - timedelta(days=2))
        self.assertContains(self.client.get(self.url('loan-detail')), 'data-bs-target="#topupModal"')

    def test_the_loans_balance_history_and_schedule_pages_open_after_a_top_up(self):
        self.post(received_account=self.bank.pk)
        for name in ('loan-tab-schedule', 'loan-tab-history', 'loan-list', 'pay-emi'):
            kwargs = {'pk': self.loan.uuid} if name.startswith('loan-tab') else {}
            self.assertEqual(self.client.get(reverse(name, kwargs=kwargs)).status_code, 200, name)


class ExportTests(Base):
    def test_the_export_has_the_top_ups_and_the_amounts_received(self):
        self.post(received_account=self.bank.pk, extra_months='6', note='Renovation')
        response = self.client.post(reverse('export-data'), {'entities': ['loan_topups', 'loan_disbursements']})
        archive = zipfile.ZipFile(io.BytesIO(response.content))
        topups = list(csv.reader(io.StringIO(archive.read('loan_topups.csv').decode())))
        received = list(csv.reader(io.StringIO(archive.read('loan_amounts_received.csv').decode())))
        self.assertEqual(topups[1][1:], ['Home Loan', '200000.00', '₹', '6', 'Renovation'])
        self.assertEqual(received[1][1:5], ['Home Loan', '200000.00', '₹', 'HDFC Savings'])
