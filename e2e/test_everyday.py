"""The things people do every week: fix or remove an entry, pay an EMI, save towards a goal, change the language,
move around the app on a phone."""
from datetime import timedelta
from decimal import Decimal

from django.urls import reverse
from django.utils import timezone

from e2e.base import E2ETestCase, make_account, make_user


class Base(E2ETestCase):
    def setUp(self):
        super().setUp()
        self.user = make_user('everyday')
        self.bank = make_account(self.user, 'HDFC Savings', 'SAVINGS_ACCOUNT', 50000)

    def balance(self, account=None):
        from expenses.models import Account
        return Account.objects.get(pk=(account or self.bank).pk).balance

    def expense(self, amount='500', category='Food'):
        from expenses.models import Expense
        return Expense.objects.create(user=self.user, category=category, amount=Decimal(amount), base_amount=Decimal(amount),
                                      currency='₹', date=timezone.localdate(), account=self.bank, description='Lunch')


class ExpenseEditAndDeleteTests(Base):
    def test_deleting_an_expense_removes_it_and_gives_the_money_back(self):
        from expenses.models import Expense
        expense = self.expense('500')
        self.assertEqual(self.balance(), Decimal('49500.00'))
        page = self.new_page(self.user)
        self.open(page, reverse('expense-delete', kwargs={'pk': expense.uuid}))
        page.click('button:has-text("Delete")')
        page.wait_for_url(lambda url: '/delete' not in url)
        self.assertEqual(Expense.objects.filter(user=self.user).count(), 0)
        self.assertEqual(self.balance(), Decimal('50000.00'))

    def test_editing_an_expense_changes_it_and_the_balance(self):
        expense = self.expense('500')
        page = self.new_page(self.user)
        self.open(page, reverse('expense-edit', kwargs={'pk': expense.uuid}))
        page.fill('input[name="amount"]', '800')
        page.click('button[type="submit"]:visible')
        page.wait_for_url(lambda url: '/edit' not in url)
        expense.refresh_from_db()
        self.assertEqual(expense.amount, Decimal('800.00'))
        self.assertEqual(self.balance(), Decimal('49200.00'))


class PayEmiTests(Base):
    def test_one_confirm_records_the_emi_and_takes_it_from_the_account(self):
        from expenses.models import Loan, LoanRepayment
        Loan.objects.create(user=self.user, name='Home loan', loan_type='HOME', initial_principal=Decimal('1200000'),
                            duration_months=120, start_date=timezone.localdate() - timedelta(days=40), currency='₹')
        before = self.balance()
        page = self.new_page(self.user)
        self.open(page, reverse('pay-emi'))
        self.assertIn('Home loan', page.inner_text('body'))
        page.click('button:has-text("Record EMI")')
        page.wait_for_url(lambda url: '/pay-emi' not in url)
        repayment = LoanRepayment.objects.get(loan__user=self.user)
        self.assertEqual(repayment.from_account_id, self.bank.pk)
        self.assertEqual(self.balance(), before - repayment.amount)


class GoalTests(Base):
    def test_adding_a_contribution_from_the_goal_page(self):
        from expenses.models import GoalContribution, SavingsGoal
        goal = SavingsGoal.objects.create(user=self.user, name='Goa trip', target_amount=Decimal('50000'), currency='₹',
                                          target_date=timezone.localdate() + timedelta(days=200))
        page = self.new_page(self.user)
        self.open(page, reverse('goal-detail', kwargs={'pk': goal.uuid}))
        page.click('text=Add Contribution')
        page.fill('#addContributionModal input[name="amount"], .modal.show input[name="amount"]', '2500')
        page.click('.modal.show button:has-text("Save Contribution")')
        self.wait_until(lambda: GoalContribution.objects.filter(goal=goal).count() == 1, 'the contribution was not saved')
        self.assertEqual(GoalContribution.objects.get(goal=goal).amount, Decimal('2500.00'))
        self.assertEqual(self.balance(), Decimal('47500.00'))


class LanguageTests(Base):
    def test_choosing_hindi_changes_the_app_language(self):
        page = self.new_page(self.user)
        self.open(page, reverse('language-settings'))
        self.set_select(page, 'select[name="language"]', 'hi')
        page.click('button[type="submit"]:visible')
        page.wait_for_load_state('load')
        self.open(page, reverse('expense-list'))
        body = page.inner_text('body')
        self.assertRegex(body, '[ऀ-ॿ]')                       # Devanagari is on the page
        self.assertNotIn('Everything you have spent', body)


class NavigationTests(Base):
    def test_analytics_tabs_switch_without_a_full_reload(self):
        page = self.new_page(self.user)
        self.open(page, reverse('analytics'))
        page.click('.tmr-tabs >> text=Cashflow')
        page.wait_for_url('**/analytics/cashflow/**')
        page.click('.tmr-tabs >> text=Net worth')
        page.wait_for_url('**/analytics/net-worth/**')
        self.assertTrue(page.locator('.tmr-tab.active', has_text='Net worth').count() == 1)

    def test_the_phone_bottom_bar_and_more_sheet_reach_other_pages(self):
        page = self.new_page(self.user, phone=True)
        self.open(page, reverse('home'))
        page.click('.mobile-nav-label >> text=Accounts')
        page.wait_for_url('**/accounts/**')
        page.click('.mobile-nav-label >> text=Transactions')
        page.wait_for_url('**/transactions/**')
        page.click('.mobile-nav-label >> text=More')
        sheet_item = page.locator('text=Analytics >> visible=true').first  # the More sheet opened with the rest of the app
        sheet_item.wait_for()
        sheet_item.click()
        page.wait_for_url('**/analytics/**')


class FirstRunTests(E2ETestCase):
    def test_someone_with_no_data_who_has_not_finished_the_welcome_is_sent_to_it(self):
        from expenses.models import UserProfile
        user = make_user('newcomer')
        UserProfile.objects.filter(user=user).update(has_seen_tutorial=False)
        user = type(user).objects.get(pk=user.pk)
        page = self.new_page(user)
        page.goto(self.live_server_url + reverse('home'))
        self.assertIn('/onboarding', page.url)
        self.assertGreater(len(page.inner_text('body')), 100)
