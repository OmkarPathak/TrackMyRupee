"""Loan amounts received are listed in the main Transactions list, as money in that is neither income nor spending: they are
left out of every total, can be filtered, searched and deleted, and one that came with a top-up is deleted with the top-up."""
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.contrib.messages import get_messages
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from expenses.models import Account, FXRate, Income, Loan, LoanDisbursement, LoanTopUp, UserProfile

D = Decimal


class Base(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('lister', password='pw')
        profile, _ = UserProfile.objects.get_or_create(user=self.user)
        profile.tier = 'PRO'
        profile.currency = '₹'
        profile.save()
        self.user.refresh_from_db()
        self.client.force_login(self.user)
        self.today = timezone.localdate()
        self.bank = Account.objects.create(user=self.user, name='HDFC Savings', account_type='SAVINGS_ACCOUNT', balance=D('10000'), currency='₹')
        self.loan = Loan.objects.create(user=self.user, name='Car Loan', loan_type='CAR', initial_principal=D('260000'), duration_months=60,
                                        start_date=self.today, currency='₹')

    def receive(self, amount='260000', when=None, **extra):
        return LoanDisbursement.objects.create(loan=self.loan, account=self.bank, amount=D(amount), date=when or self.today, **extra)

    def page(self, **query):
        query.setdefault('time_period', 'all')
        return self.client.get(reverse('all-transactions'), query)

    def rows(self, **query):
        return list(self.page(**query).context['transactions'])


class ListedTests(Base):
    def test_it_is_listed_with_its_loan_account_and_amount(self):
        self.receive()
        rows = self.rows()
        self.assertEqual([r['type'] for r in rows], ['LOAN_RECEIVED'])
        self.assertEqual((rows[0]['cat'], rows[0]['acc'], rows[0]['unified_amount']), ('Car Loan', 'HDFC Savings', D('260000.00')))

    def test_the_page_shows_it_as_money_in_with_its_own_label(self):
        self.receive()
        html = self.page().content.decode()
        self.assertIn('Loan received', html)
        self.assertIn('type-loan_received', html)
        self.assertRegex(html, r'amount-loan_received[^>]*>\s*\+\s*₹<span[^>]*>2\.6L</span>')

    def test_it_is_not_income_and_changes_no_total(self):
        before = self.page().context
        self.receive()
        after = self.page().context
        self.assertFalse(Income.objects.filter(user=self.user).exists())
        for key in ('total_income', 'total_expenses', 'savings_rate', 'net_remaining'):
            if key in before:
                self.assertEqual(before[key], after[key], key)

    def test_a_foreign_loan_shows_in_the_persons_own_currency(self):
        for (src, dst), rate in {('USD', 'INR'): '80', ('INR', 'USD'): '0.0125'}.items():
            FXRate.objects.update_or_create(from_currency=src, to_currency=dst, as_of_date=self.today, defaults={'rate': D(rate), 'source': 'test'})
        cache.clear()
        self.loan.currency = '$'
        self.loan.save()
        received = self.receive('1000')
        self.assertEqual(received.base_amount, D('80000.00'))
        self.assertEqual(self.rows()[0]['unified_amount'], D('80000.00'))

    def test_the_amount_in_the_base_currency_is_kept_for_an_ordinary_loan(self):
        received = self.receive('5000')
        self.assertEqual((received.exchange_rate, received.base_amount), (D('1'), D('5000.00')))


class FilterTests(Base):
    def setUp(self):
        super().setUp()
        self.receive('260000')
        Income.objects.create(user=self.user, account=self.bank, amount=D('500'), base_amount=D('500'), source='Salary', source_type='Salary',
                              date=self.today, currency='₹')

    def types(self, **query):
        return sorted(r['type'] for r in self.rows(**query))

    def test_all_types_show_both(self):
        self.assertEqual(self.types(), ['INCOME', 'LOAN_RECEIVED'])

    def test_the_type_filter_picks_it_out(self):
        self.assertEqual(self.types(type='LOAN_RECEIVED'), ['LOAN_RECEIVED'])
        self.assertEqual(self.types(type='INCOME'), ['INCOME'])

    def test_the_filter_offers_it(self):
        from expenses.filters.definitions import ALL_TRANSACTIONS_FILTERS
        values = [o['value'] for f in ALL_TRANSACTIONS_FILTERS.filters if f.key == 'type' for o in f.options]
        self.assertIn('LOAN_RECEIVED', values)

    def test_searching_by_the_loans_name(self):
        self.assertEqual(self.types(search='Car'), ['LOAN_RECEIVED'])
        self.assertEqual(self.types(search='nothing like it'), [])

    def test_the_account_filter(self):
        other = Account.objects.create(user=self.user, name='Other', account_type='SAVINGS_ACCOUNT', balance=D('0'), currency='₹')
        self.assertEqual(self.types(account=self.bank.pk), ['INCOME', 'LOAN_RECEIVED'])
        self.assertEqual(self.types(account=other.pk), [])

    def test_a_category_filter_leaves_it_out(self):
        self.assertEqual(self.types(category='Rent'), [])

    def test_the_date_filter(self):
        LoanDisbursement.objects.create(loan=self.loan, account=self.bank, amount=D('1000'), date=self.today - timedelta(days=400))
        self.assertEqual(self.types(time_period='this_month').count('LOAN_RECEIVED'), 1)
        self.assertEqual(self.types(time_period='all').count('LOAN_RECEIVED'), 2)


class DeleteTests(Base):
    def test_deleting_it_from_the_list_takes_the_amount_out_of_the_account_again(self):
        received = self.receive()
        self.bank.refresh_from_db()
        self.assertEqual(self.bank.balance, D('270000.00'))
        self.assertContains(self.page(), f'/loans/received/{received.uuid.hex}/delete/')       # (the list gives the id without hyphens)
        self.client.post(reverse('loan-disbursement-delete', kwargs={'pk': received.uuid}))
        self.bank.refresh_from_db()
        self.assertEqual(self.bank.balance, D('10000.00'))
        self.assertEqual(self.rows(), [])

    def test_what_came_with_a_top_up_is_deleted_with_the_top_up_not_alone(self):
        received = self.receive('50000')
        LoanTopUp.objects.create(loan=self.loan, date=self.today, amount=D('50000'), disbursement=received)
        response = self.client.post(reverse('loan-disbursement-delete', kwargs={'pk': received.uuid}))
        self.assertEqual(response.status_code, 302)
        self.assertTrue(LoanDisbursement.objects.filter(pk=received.pk).exists())
        self.assertTrue(any('came with a top-up' in str(m) for m in get_messages(response.wsgi_request)))
        self.bank.refresh_from_db()
        self.assertEqual(self.bank.balance, D('60000.00'))
