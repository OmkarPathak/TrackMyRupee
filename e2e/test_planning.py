"""Planning ahead: subscriptions, loans, goals, budgets, and the Guided Setup."""
from datetime import timedelta
from decimal import Decimal

from django.urls import reverse
from django.utils import timezone

from e2e.base import E2ETestCase, make_account, make_user


class Base(E2ETestCase):
    def setUp(self):
        super().setUp()
        self.user = make_user('planner')
        self.bank = make_account(self.user, 'HDFC Savings', 'SAVINGS_ACCOUNT', 100000)
        self.today = timezone.localdate()

    def fill_date(self, page, selector, value=None):
        page.fill(selector, (value or self.today).isoformat())


class RecurringTests(Base):
    def test_a_monthly_subscription_is_created_and_listed(self):
        from expenses.models import RecurringTransaction
        page = self.new_page(self.user)
        self.open(page, reverse('recurring-create'))
        page.fill('textarea[name="description"]', 'Netflix')       # step 1: what it is
        page.fill('input[name="amount"]', '649')
        page.click('button:has-text("Next") >> visible=true')
        self.set_select(page, 'select[name="frequency"]', 'MONTHLY')   # step 2: when
        self.fill_date(page, 'input[name="start_date"]', self.today + timedelta(days=5))
        page.click('button:has-text("Next") >> visible=true')
        self.set_select(page, 'select[name="account"]', str(self.bank.pk))   # step 3: where from
        self.set_select(page, 'select[name="category"]', 'Bills')
        page.click('button[type="submit"]:visible')
        page.wait_for_url(lambda url: '/recurring/create' not in url)
        recurring = RecurringTransaction.objects.get(user=self.user)
        self.assertEqual((recurring.description, recurring.amount, recurring.frequency),
                         ('Netflix', Decimal('649.00'), 'MONTHLY'))
        self.open(page, reverse('recurring-list'))
        self.assertIn('Netflix', page.inner_text('body'))


class LoanAndGoalTests(Base):
    def test_a_loan_is_created_and_shows_in_the_loans_list(self):
        from expenses.models import Loan
        page = self.new_page(self.user)
        self.open(page, reverse('loan-create'))
        page.fill('input[name="name"]', 'Car loan')
        page.fill('input[name="initial_principal"]', '500000')
        page.fill('input[name="interest_rate"]', '9')
        page.fill('input[name="duration_months"]', '60')
        self.fill_date(page, 'input[name="start_date"]')
        page.click('button[type="submit"]:visible')
        page.wait_for_url(lambda url: '/loans/add' not in url)
        loan = Loan.objects.get(user=self.user)
        self.assertEqual((loan.name, loan.initial_principal, loan.duration_months), ('Car loan', Decimal('500000.00'), 60))
        self.open(page, reverse('loan-list'))
        self.assertIn('Car loan', page.inner_text('body'))

    def test_a_goal_is_created_and_shows_in_the_goals_list(self):
        from expenses.models import SavingsGoal
        page = self.new_page(self.user)
        self.open(page, reverse('goal-create'))
        page.fill('input[name="name"]', 'Emergency fund')
        page.fill('input[name="target_amount"]', '200000')
        self.fill_date(page, 'input[name="target_date"]', self.today + timedelta(days=365))
        page.click('button[type="submit"]:visible')
        page.wait_for_url(lambda url: '/goals/add' not in url)
        goal = SavingsGoal.objects.get(user=self.user)
        self.assertEqual((goal.name, goal.target_amount), ('Emergency fund', Decimal('200000.00')))
        self.open(page, reverse('goal-list'))
        self.assertIn('Emergency fund', page.inner_text('body'))


class BudgetTests(Base):
    def test_a_budget_is_set_from_the_page_and_overspending_shows_as_needing_attention(self):
        from expenses.models import Category, Expense
        food = Category.objects.get(user=self.user, name='Food')
        page = self.new_page(self.user)
        self.open(page, reverse('budget'))
        page.click('[data-bdx-new] >> visible=true')
        page.wait_for_selector('#bdx-new', state='visible')
        self.set_select(page, '#bdx-new-cat', str(food.pk))
        page.fill('#bdx-new-amount', '1000')
        page.click('#bdx-new-save')
        page.wait_for_selector('#bdx-new', state='hidden')
        self.assertIn('₹1,000', page.inner_text('body'))          # the planned budget is now ₹1,000
        # spend more than the limit, and the page says so
        Expense.objects.create(user=self.user, category='Food', amount=Decimal('1400'), base_amount=Decimal('1400'),
                               currency='₹', date=self.today, account=self.bank, description='Feast')
        self.open(page, reverse('budget'))
        page.wait_for_selector('#bdx-attn')                        # the "Needs attention" group appears
        self.assertIn('Needs attention', page.inner_text('body'))


class GuidedSetupTests(Base):
    def test_the_salary_flow_walks_through_its_steps_and_records_the_salary(self):
        from expenses.models import Income, RecurringTransaction
        page = self.new_page(self.user)
        self.open(page, reverse('flow-detail', kwargs={'key': 'salary'}))
        form = page.locator('#tmr-flow-form-salary')
        for _step in range(8):
            if page.locator('#tmr-review-salary').is_visible():
                break
            step = form.locator('.wizard-step-content:visible')
            for field in step.locator('input:visible, select:visible, textarea:visible').all():
                kind = field.get_attribute('type') or field.evaluate('e => e.tagName.toLowerCase()')
                name = field.get_attribute('name') or ''
                if kind in ('checkbox', 'radio', 'hidden', 'submit', 'button'):
                    continue
                if kind == 'select':
                    options = field.locator('option').all()
                    values = [o.get_attribute('value') for o in options if o.get_attribute('value')]
                    if values and not field.evaluate('e => e.value'):
                        field.select_option(values[0])
                elif kind == 'date':
                    if not field.input_value():
                        field.fill(self.today.isoformat())
                elif not field.input_value():
                    # (a day of the month for "...date" numbers, the salary for the amount, anything small otherwise)
                    field.fill('15' if 'date' in name or 'day' in name else '60000' if name == 'amount' else '1')
            form.locator('button:has-text("Next") >> visible=true').first.click()
            page.wait_for_timeout(400)
        self.assertTrue(page.locator('#tmr-review-salary').is_visible(), 'the flow never reached its review step')
        page.locator('[data-confirm-btn]').first.click()
        self.wait_until(lambda: Income.objects.filter(user=self.user).exists() or
                        RecurringTransaction.objects.filter(user=self.user).exists(),
                        'the flow recorded nothing', seconds=15)
