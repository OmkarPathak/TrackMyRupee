"""EMIs that post by themselves: the subscription made for a loan repayment, what it posts on each due date (the
interest and principal split, with the rate and balance of that day), catching up past dates, and when it stops."""
from datetime import timedelta
from decimal import Decimal

from django.urls import reverse
from django.utils import timezone

from e2e.base import E2ETestCase, make_account, make_user
from e2e.loan_helpers import make_loan, make_repayment

D = Decimal


class Base(E2ETestCase):
    def setUp(self):
        super().setUp()
        self.user = make_user('emipayer')
        self.bank = make_account(self.user, 'HDFC Savings', 'SAVINGS_ACCOUNT', 500000)
        self.today = timezone.localdate()
        self.loan = make_loan(self.user, 'Home Loan', 1000000, 120, '12', days_ago=100)

    def balance(self):
        self.bank.refresh_from_db()
        return self.bank.balance

    def fresh(self, loan=None):
        loan = loan or self.loan
        loan.refresh_from_db()
        return loan

    def schedule(self, start, amount='20000', frequency='MONTHLY', loan=None, active=True, **extra):
        from expenses.models import RecurringTransaction
        return RecurringTransaction.objects.create(
            user=self.user, transaction_type='LOAN', amount=D(amount), base_amount=D(amount), currency='₹', account=self.bank,
            loan=loan or self.loan, frequency=frequency, start_date=start, is_active=active, description='Loan EMI: Home Loan', **extra)

    def process(self):
        from expenses.views.mixins import process_user_recurring_transactions
        process_user_recurring_transactions(self.user, force=True)

    def repayments(self, loan=None):
        return list((loan or self.loan).repayments.order_by('date'))

    def create_through_the_form(self, page, start, amount='20000', historical=True):
        self.open(page, reverse('recurring-create'))
        self.set_select(page, 'select[name="transaction_type"]', 'LOAN')
        page.fill('textarea[name="description"]', 'Loan EMI: Home Loan')
        page.fill('input[name="amount"]', amount)
        page.click('button:has-text("Next") >> visible=true')
        self.set_select(page, 'select[name="frequency"]', 'MONTHLY')
        page.fill('input[name="start_date"]', start.isoformat())
        if historical:
            page.check('input[name="create_historical_entries"]')
        page.click('button:has-text("Next") >> visible=true')
        self.set_select(page, 'select[name="account"]', str(self.bank.pk))
        self.set_select(page, 'select[name="loan"]', str(self.loan.pk))
        page.click('button[type="submit"]:has-text("Add")')
        page.wait_for_url(lambda url: '/recurring/create' not in url)


class CreateThroughTheFormTests(Base):
    def test_a_schedule_started_in_the_past_posts_every_missed_emi_with_its_own_split(self):
        page = self.new_page(self.user)
        self.create_through_the_form(page, self.today - timedelta(days=65))
        rows = self.repayments()
        self.assertEqual(len(rows), 3)
        self.assertEqual([r.interest_portion for r in rows][0], D('10000.00'))      # 10,00,000 at 12%: a month's interest
        balance = D('1000000')
        for row in rows:
            self.assertEqual(row.amount, D('20000.00'))
            self.assertEqual(row.interest_portion, (balance * D('0.01')).quantize(D('0.01')))
            balance -= row.principal_portion
        self.assertEqual(self.fresh().remaining_principal, balance)
        self.assertEqual(self.balance(), D('500000') - D('60000'))

    def test_without_the_catch_up_switch_the_past_is_left_alone(self):
        page = self.new_page(self.user)
        self.create_through_the_form(page, self.today - timedelta(days=65), historical=False)
        self.assertEqual(self.repayments(), [])
        self.assertEqual(self.balance(), D('500000.00'))

    def test_a_schedule_starting_later_posts_nothing_yet_and_is_listed_with_its_next_date(self):
        page = self.new_page(self.user)
        start = self.today + timedelta(days=12)
        self.create_through_the_form(page, start)
        self.assertEqual(self.repayments(), [])
        self.open(page, reverse('recurring-list'))
        self.assertIn('Loan EMI: Home Loan', page.inner_text('body'))
        self.assertIn(start.strftime('%d %b %Y'), page.inner_text('body'))


class PostingTests(Base):
    def test_each_instalment_uses_the_rate_in_force_on_its_own_date(self):
        from expenses.models import LoanInterestRate
        LoanInterestRate.objects.create(loan=self.loan, interest_rate=D('6'), effective_date=self.today - timedelta(days=40))
        self.schedule(self.today - timedelta(days=65))
        self.process()
        rows = self.repayments()
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0].interest_portion, D('10000.00'))                      # 12% a year
        self.assertLess(rows[1].interest_portion, D('5200'))                           # 6% a year after the change
        self.assertLess(rows[2].interest_portion, rows[1].interest_portion)

    def test_processing_again_posts_nothing_twice(self):
        self.schedule(self.today - timedelta(days=65))
        self.process()
        self.process()
        page = self.new_page(self.user)
        self.open(page, reverse('home'))
        self.open(page, reverse('loan-list'))
        self.assertEqual(len(self.repayments()), 3)

    def test_an_emi_smaller_than_the_interest_is_not_posted_and_the_schedule_stops(self):
        schedule = self.schedule(self.today - timedelta(days=35), amount='1000')
        self.process()
        schedule.refresh_from_db()
        self.assertEqual(self.repayments(), [])
        self.assertFalse(schedule.is_active)

    def test_the_last_instalment_is_only_what_is_left_and_the_schedule_then_stops(self):
        small = make_loan(self.user, 'Nearly done', 15000, 12, '12', days_ago=100)
        schedule = self.schedule(self.today - timedelta(days=5), amount='20000', loan=small)
        self.process()
        rows = self.repayments(small)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].principal_portion, D('15000.00'))
        self.assertFalse(self.fresh(small).is_active)
        self.process()
        schedule.refresh_from_db()
        self.assertFalse(schedule.is_active)
        self.assertEqual(len(self.repayments(small)), 1)

    def test_a_paused_schedule_posts_nothing(self):
        self.schedule(self.today - timedelta(days=65), active=False)
        self.process()
        self.assertEqual(self.repayments(), [])

    def test_a_schedule_for_a_loan_already_paid_off_stops_without_posting(self):
        done = make_loan(self.user, 'Done', 20000, 12, '12', days_ago=100)
        make_repayment(done, self.bank, 20000, 0, days_ago=3)
        schedule = self.schedule(self.today - timedelta(days=35), loan=done)
        self.process()
        schedule.refresh_from_db()
        self.assertFalse(schedule.is_active)
        self.assertEqual(len(self.repayments(done)), 1)

    def test_a_weekly_schedule_charges_a_weeks_interest(self):
        self.schedule(self.today - timedelta(days=15), amount='5000', frequency='WEEKLY')
        self.process()
        rows = self.repayments()
        self.assertGreaterEqual(len(rows), 2)
        self.assertEqual(rows[0].interest_portion, (D('1000000') * D('12') * D('7') / D('36500')).quantize(D('0.01')))

    def test_a_quarterly_schedule_charges_three_months_of_interest(self):
        self.schedule(self.today - timedelta(days=5), amount='50000', frequency='QUARTERLY')
        self.process()
        rows = self.repayments()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].interest_portion, D('30000.00'))

    def test_a_bi_monthly_schedule_charges_two_months_of_interest(self):
        self.schedule(self.today - timedelta(days=5), amount='40000', frequency='BIMONTHLY')
        self.process()
        rows = self.repayments()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].interest_portion, D('20000.00'))

    def test_a_prepayment_made_after_an_early_instalment_does_not_lower_that_instalment(self):
        from expenses.models import CapitalEvent
        CapitalEvent.objects.create(user=self.user, amount=D('500000'), date=self.today - timedelta(days=5), subtype='loan_prepayment',
                                    linked_loan=self.loan, account=self.bank, currency='₹')
        self.schedule(self.today - timedelta(days=65))
        self.process()
        self.assertEqual(self.repayments()[0].interest_portion, D('10000.00'))

    def test_the_money_posted_leaves_the_account_and_appears_in_its_activity(self):
        self.schedule(self.today - timedelta(days=35))
        self.process()
        page = self.new_page(self.user)
        self.open(page, reverse('account-detail', kwargs={'pk': self.bank.uuid}))
        text = page.inner_text('body')
        self.assertIn('LOAN REPAYMENT', text.upper())
        self.assertIn('Home Loan', text)


class ManageTests(Base):
    def test_the_schedule_is_listed_with_the_subscriptions_and_its_amount(self):
        self.schedule(self.today + timedelta(days=5))
        page = self.new_page(self.user)
        self.open(page, reverse('recurring-list'))
        text = page.inner_text('body')
        self.assertIn('Loan EMI: Home Loan', text)
        self.assertIn('20,000', text)

    def test_deleting_the_loan_removes_its_schedule_and_the_pages_still_open(self):
        from expenses.models import Loan, RecurringTransaction
        self.schedule(self.today + timedelta(days=5))
        page = self.new_page(self.user)
        page.request.post  # (the delete is a confirmed POST from the list)
        self.open(page, reverse('loan-list'))
        page.click(f'button[data-url="{reverse("loan-delete", kwargs={"pk": self.loan.uuid})}"]')
        page.wait_for_selector('#deleteModal.show')
        page.click('#deleteModalConfirmBtn')
        self.wait_until(lambda: not Loan.objects.filter(pk=self.loan.pk).exists())
        self.assertFalse(RecurringTransaction.objects.filter(user=self.user, transaction_type='LOAN', is_active=True).exists())
        for name in ('home', 'recurring-list', 'loan-list', 'pay-emi'):
            self.open(page, reverse(name))

    def test_the_schedules_amount_can_be_changed_and_the_next_emi_uses_it(self):
        schedule = self.schedule(self.today + timedelta(days=2))
        page = self.new_page(self.user)
        self.open(page, reverse('recurring-edit', kwargs={'pk': schedule.uuid}))
        page.fill('input[name="amount"]', '25000')
        page.click('button:has-text("Next") >> visible=true')
        page.click('button:has-text("Next") >> visible=true')
        page.click('button[type="submit"]:has-text("Edit"), button[type="submit"]:has-text("Save"), button[type="submit"]:has-text("Update")')
        page.wait_for_url(lambda url: '/edit' not in url)
        schedule.refresh_from_db()
        self.assertEqual(schedule.amount, D('25000.00'))
