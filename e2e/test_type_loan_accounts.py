"""Loan accounts (home, vehicle, education, personal, business, loan against property, gold loan): the amount still owed.

A loan account is tied to a Loan record and shows its outstanding principal. The loan pages themselves are covered by
the e2e/test_loans_* files; this covers the account side: the amount shown, net worth, and the add form.
"""
import unittest
from decimal import Decimal

from django.urls import reverse

from e2e.base import E2ETestCase, make_account, make_user
from e2e.loan_helpers import make_loan, make_repayment
from e2e.type_helpers import balance_on, net_worth, open_account, save_account_form

D = Decimal
KINDS = ('HOME_LOAN', 'VEHICLE_LOAN', 'EDUCATION_LOAN', 'PERSONAL_LOAN', 'BUSINESS_LOAN', 'LAP', 'GOLD_LOAN')


class Base(E2ETestCase):
    def setUp(self):
        super().setUp()
        self.user = make_user('loan-acct-edge')
        self.bank = make_account(self.user, 'HDFC Savings', 'SAVINGS_ACCOUNT', 2000000)

    def loan_account(self, kind='HOME_LOAN', name='Loan account', principal=1200000, **loan_terms):
        loan = make_loan(self.user, name=f'{name} loan', principal=principal, **loan_terms)
        account = make_account(self.user, name, kind, 0, linked_loan=loan)
        return loan, account


class OutstandingTests(Base):
    """What a loan account is worth is read from net worth (the figure that adds every account up)."""

    def owed(self, page, before):
        """How far below `before` net worth now is."""
        return before - net_worth(self, page)

    def test_each_kind_takes_the_principal_still_owed_off_net_worth(self):
        page = self.new_page(self.user)
        for kind in KINDS:
            with self.subTest(kind):
                before = net_worth(self, page)
                self.loan_account(kind, f'{kind} acct', principal=500000)
                self.assertEqual(self.owed(page, before), D('500000'))

    def test_only_the_principal_part_of_a_repayment_reduces_what_is_owed(self):
        loan, _ = self.loan_account()
        page = self.new_page(self.user)
        before = net_worth(self, page)                                      # bank 20,00,000 - 12,00,000 owed
        self.assertEqual(before, D('800000'))
        make_repayment(loan, self.bank, principal=100000, interest=9000)
        # 1,09,000 left the bank; 1,00,000 less is owed: net worth falls by the interest only
        self.assertEqual(net_worth(self, page), before - D('9000'))

    def test_a_fully_repaid_loan_owes_nothing(self):
        loan, _ = self.loan_account(principal=300000)
        make_repayment(loan, self.bank, principal=300000, interest=0)
        page = self.new_page(self.user)
        self.assertEqual(net_worth(self, page), D('1700000'))              # the bank gave up 3,00,000 and owes nothing

    def test_a_bullet_loan_stays_at_its_principal_while_only_interest_is_paid(self):
        loan, _ = self.loan_account(principal=800000, repayment_type='BULLET')
        page = self.new_page(self.user)
        before = net_worth(self, page)
        make_repayment(loan, self.bank, principal=0, interest=6000)
        make_repayment(loan, self.bank, principal=0, interest=6000, days_ago=2)
        self.assertEqual(net_worth(self, page), before - D('12000'))        # the principal owed is unchanged

    def test_a_loan_that_starts_in_the_future_already_owes_its_principal(self):
        page = self.new_page(self.user)
        before = net_worth(self, page)
        self.loan_account(principal=250000, days_ago=-30)
        self.assertEqual(self.owed(page, before), D('250000'))

    def test_two_loan_accounts_add_up_in_net_worth(self):
        self.loan_account('HOME_LOAN', 'Home', principal=900000)
        self.loan_account('PERSONAL_LOAN', 'Personal', principal=100000)
        page = self.new_page(self.user)
        self.assertEqual(net_worth(self, page), D('2000000') - D('1000000'))

    @unittest.expectedFailure
    def test_KNOWN_BUG_the_loan_accounts_own_page_shows_what_is_owed(self):
        """The accounts list and net worth count the outstanding principal, but the account's own page shows
        'Current balance ₹0' (it reads the ledger, which holds nothing for a loan account). Remove the decorator
        once the page shows -5,00,000."""
        _, account = self.loan_account(principal=500000)
        page = self.new_page(self.user)
        open_account(self, page, account)
        self.assertEqual(balance_on(page), D('-500000.00'))


class FormTests(Base):
    def test_a_loan_account_cannot_be_saved_without_a_loan(self):
        from expenses.models import Account
        page = self.new_page(self.user)
        self.open(page, reverse('account-create') + '?type=HOME_LOAN')
        page.fill('#id_name', 'No loan')
        save_account_form(page)
        page.wait_for_timeout(1200)
        self.assertFalse(Account.objects.filter(user=self.user, name='No loan').exists())
        self.assertIn('requires a linked Loan', page.inner_text('body'))

    def test_each_kind_can_be_created_by_choosing_a_loan(self):
        from expenses.models import Account
        page = self.new_page(self.user)
        for kind in KINDS:
            with self.subTest(kind):
                loan = make_loan(self.user, name=f'Loan for {kind}', principal=100000)
                self.open(page, reverse('account-create') + f'?type={kind}')
                page.fill('#id_name', f'New {kind}')
                self.set_select(page, '#id_linked_loan', str(loan.pk))
                save_account_form(page)
                self.wait_until(lambda k=kind: Account.objects.filter(user=self.user, name=f'New {k}').exists(),
                                f'{kind} was not saved', seconds=15)
                self.assertEqual(Account.objects.get(user=self.user, name=f'New {kind}').linked_loan_id, loan.pk)

    def test_a_loan_already_paid_off_is_not_offered(self):
        from expenses.models import Loan
        loan = make_loan(self.user, name='Closed loan', principal=100000)
        Loan.objects.filter(pk=loan.pk).update(is_active=False)
        page = self.new_page(self.user)
        self.open(page, reverse('account-create') + '?type=PERSONAL_LOAN')
        options = page.locator('#id_linked_loan option').all_inner_texts()
        self.assertFalse(any('Closed loan' in option for option in options))

    def test_another_persons_loan_is_not_offered(self):
        other = make_user('someone-else')
        make_loan(other, name='Their loan', principal=100000)
        page = self.new_page(self.user)
        self.open(page, reverse('account-create') + '?type=PERSONAL_LOAN')
        options = page.locator('#id_linked_loan option').all_inner_texts()
        self.assertFalse(any('Their loan' in option for option in options))
