"""Every main page opens for a person with real data, shows its heading and throws no script error."""
from datetime import timedelta
from decimal import Decimal

from django.urls import reverse
from django.utils import timezone

from e2e.base import E2ETestCase, make_account, make_user

# (url name, text that must be on the page)
PAGES = [
    ('home', 'Dashboard'),
    ('expense-list', 'Expenses'),
    ('all-transactions', 'All Transactions'),
    ('income-list', 'Income'),
    ('budget', 'Budget'),
    ('account-list', 'Accounts'),
    ('transfer-create', 'Move Money'),
    ('expense-create', 'Add'),
    ('income-create', 'Income'),
    ('analytics', 'Analytics'),
    ('analytics-cashflow', 'Analytics'),
    ('analytics-networth', 'Analytics'),
    ('goal-list', 'Goals'),
    ('loan-list', 'Loans'),
    ('recurring-list', 'Subscriptions'),
    ('capital-event-list', 'Big purchases'),
    ('calendar', timezone.localdate().strftime('%B')),        # (the calendar's heading is the month)
    ('settings-home', 'Settings'),
    ('profile-settings', 'Profile'),
    ('currency-settings', 'Currency'),
    ('language-settings', 'Language'),
    ('import-history', 'Import'),
    ('category-list', 'Categories'),
    ('pay-emi', 'EMI'),
]
SERVER_ERRORS = ('Server Error', 'TemplateSyntaxError', 'Traceback (most recent call last)', 'DoesNotExist at')


def build_world(user):
    """Accounts, a month of spending, income, a loan, a goal, a budget and a few moves: enough for every page."""
    from expenses.models import Category, Expense, Income, Loan, SavingsGoal, Transfer
    today = timezone.localdate()
    salary = make_account(user, 'Salary account', 'SALARY_ACCOUNT', 150000)
    savings = make_account(user, 'Savings account', 'SAVINGS_ACCOUNT', 40000)
    fd = make_account(user, 'ICICI FD', 'FD', 100000, deposit_maturity_date=today + timedelta(days=3))
    make_account(user, 'Credit card', 'CREDIT_CARD', -12000)
    for days_ago, category, amount in ((1, 'Food', 450), (3, 'Shopping', 2300), (6, 'Bills', 1800), (9, 'Food', 320)):
        Expense.objects.create(user=user, category=category, amount=Decimal(amount), base_amount=Decimal(amount),
                               currency='₹', date=today - timedelta(days=days_ago), account=salary,
                               description=f'{category} {days_ago}')
    Income.objects.create(user=user, amount=Decimal('85000'), base_amount=Decimal('85000'), currency='₹',
                          source='Salary', source_type='Salary', date=today.replace(day=1), account=salary)
    Transfer.objects.create(user=user, from_account=savings, to_account=fd, amount=Decimal('50000'),
                            converted_amount=Decimal('50000'), date=today - timedelta(days=2))
    Loan.objects.create(user=user, name='Home loan', loan_type='HOME', initial_principal=Decimal('2500000'),
                        duration_months=240, start_date=today - timedelta(days=90), currency='₹')
    SavingsGoal.objects.create(user=user, name='Goa trip', target_amount=Decimal('50000'), currency='₹',
                               target_date=today + timedelta(days=300))
    Category.objects.filter(user=user, name='Food').update(limit=Decimal('5000'))


class SmokeTests(E2ETestCase):
    def setUp(self):
        super().setUp()
        self.user = make_user('smoke')
        build_world(self.user)

    def crawl(self, phone):
        page = self.new_page(self.user, phone=phone)
        for name, text in PAGES:
            path = reverse(name)
            with self.subTest(page=name, phone=phone):
                self.open(page, path)
                body = page.inner_text('body')
                self.assertIn(text, body, f'{path}: expected {text!r}')
                for bad in SERVER_ERRORS:
                    self.assertNotIn(bad, body, f'{path} shows a server error')

    def test_every_main_page_opens_on_a_computer(self):
        self.crawl(phone=False)

    def test_every_main_page_opens_on_a_phone(self):
        self.crawl(phone=True)
