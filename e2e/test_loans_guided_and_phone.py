"""The Guided Setup loan flow in full (every option, its checks, its review) and the Loans pages on a phone."""
from datetime import timedelta
from decimal import Decimal

from django.urls import reverse
from django.utils import timezone

from e2e.base import E2ETestCase, make_account, make_user
from e2e.loan_helpers import emi, make_loan, make_repayment

D = Decimal


class Base(E2ETestCase):
    def setUp(self):
        super().setUp()
        self.user = make_user('setupper')
        self.bank = make_account(self.user, 'HDFC Savings', 'SAVINGS_ACCOUNT', 500000)
        self.today = timezone.localdate()

    def balance(self):
        self.bank.refresh_from_db()
        return self.bank.balance

    def start_flow(self, page):
        self.open(page, reverse('flow-detail', kwargs={'key': 'loan'}))
        page.wait_for_selector('#stepCounterBadge')
        return page.locator('#tmr-flow-form-loan')

    def next(self, page, form):
        form.locator('button:has-text("Next") >> visible=true').first.click()
        page.wait_for_timeout(300)

    def basics(self, form, name='Home Loan', principal='1200000', rate='8.5', months='120', start=None):
        form.locator('input[name="name"]').fill(name)
        form.locator('input[name="principal"]').fill(principal)
        form.locator('input[name="annual_rate"]').fill(rate)
        form.locator('input[name="tenure_months"]').fill(months)
        form.locator('input[name="start_date"]').fill((start or self.today - timedelta(days=1)).isoformat())

    def to_review(self, page, form, *, steps=2):
        for _ in range(steps):
            self.next(page, form)
        page.wait_for_selector('#tmr-review-loan', state='visible')

    def confirm(self, page, expect_redirect=True):
        page.locator('[data-confirm-btn]').first.click()
        if expect_redirect:
            page.wait_for_url(lambda url: '/flows/loan' not in url, timeout=20000)

    def made(self, name='Home Loan'):
        from expenses.models import Loan
        return Loan.objects.get(user=self.user, name=name)


class FlowOptionTests(Base):
    def run_default(self, page, **basics):
        form = self.start_flow(page)
        self.basics(form, **basics)
        self.set_select(page, 'select[name="loan_received_account"]', str(self.bank.pk))
        form.locator('input[name="record_loan_received"]').uncheck()
        self.next(page, form)
        self.set_select(page, 'select[name="payment_account"]', str(self.bank.pk))
        self.to_review(page, form, steps=2)
        self.confirm(page)

    def test_the_loan_the_recurring_emi_and_the_rate_are_created(self):
        from expenses.models import RecurringTransaction
        page = self.new_page(self.user)
        self.run_default(page)
        loan = self.made()
        self.assertEqual((loan.initial_principal, loan.duration_months, loan.repayment_type), (D('1200000.00'), 120, 'EMI'))
        self.assertEqual([str(r.interest_rate) for r in loan.interest_rates.all()], ['8.50'])
        schedule = RecurringTransaction.objects.get(loan=loan)
        self.assertEqual((schedule.transaction_type, schedule.account_id, schedule.frequency), ('LOAN', self.bank.pk, 'MONTHLY'))
        self.assertAlmostEqual(float(schedule.amount), emi(1200000, 8.5, 120), delta=0.01)

    def test_the_review_shows_the_emi_the_app_works_out(self):
        page = self.new_page(self.user)
        form = self.start_flow(page)
        self.basics(form)
        form.locator('input[name="record_loan_received"]').uncheck()
        self.next(page, form)
        self.set_select(page, 'select[name="payment_account"]', str(self.bank.pk))
        self.to_review(page, form, steps=2)
        page.wait_for_timeout(1500)                    # (the headline counts up to its figure)
        expected = emi(1200000, 8.5, 120)
        text = page.inner_text('#tmr-review-loan').replace(',', '')
        self.assertRegex(text, rf'{int(expected):d}')

    def test_each_repayment_type_is_kept(self):
        for index, kind in enumerate(('EMI', 'INTEREST_ONLY', 'BULLET')):
            with self.subTest(kind=kind):
                page = self.new_page(self.user)
                form = self.start_flow(page)
                self.basics(form, name=f'Loan {index}')
                self.set_select(page, 'select[name="repayment_type"]', kind)
                form.locator('input[name="record_loan_received"]').uncheck()
                self.next(page, form)
                self.set_select(page, 'select[name="payment_account"]', str(self.bank.pk))
                self.to_review(page, form, steps=2)
                self.confirm(page)
                self.assertEqual(self.made(f'Loan {index}').repayment_type, kind)

    def test_a_loan_already_part_paid_starts_from_where_it_is(self):
        page = self.new_page(self.user)
        form = self.start_flow(page)
        self.basics(form, name='Mid loan', start=self.today - timedelta(days=400))
        form.locator('input[name="record_loan_received"]').uncheck() if form.locator('input[name="record_loan_received"]').is_visible() else None
        self.next(page, form)
        self.set_select(page, 'select[name="payment_account"]', str(self.bank.pk))
        self.next(page, form)
        form.locator('input[name="mid_tenure"]').check()
        form.locator('input[name="opening_paid_principal"]').fill('300000')
        self.next(page, form)
        page.wait_for_selector('#tmr-review-loan', state='visible')
        self.confirm(page)
        loan = self.made('Mid loan')
        self.assertEqual(loan.opening_paid_principal, D('300000.00'))
        self.assertEqual(loan.remaining_principal, D('900000.00'))

    def test_a_down_payment_is_kept_as_a_big_purchase_linked_to_the_loan(self):
        from expenses.models import CapitalEvent
        page = self.new_page(self.user)
        form = self.start_flow(page)
        self.basics(form, name='Flat loan')
        form.locator('input[name="record_loan_received"]').uncheck()
        self.next(page, form)
        self.set_select(page, 'select[name="payment_account"]', str(self.bank.pk))
        self.next(page, form)
        form.locator('input[name="include_down_payment"]').check()
        form.locator('input[name="down_payment_amount"]').fill('250000')
        self.set_select(page, 'select[name="down_payment_account"]', str(self.bank.pk))
        self.next(page, form)
        page.wait_for_selector('#tmr-review-loan', state='visible')
        self.confirm(page)
        loan = self.made('Flat loan')
        event = CapitalEvent.objects.get(linked_loan=loan)
        self.assertEqual((event.subtype, event.amount, event.account_id), ('loan_down_payment', D('250000.00'), self.bank.pk))
        self.assertEqual(loan.remaining_principal, D('950000.00'))
        self.assertEqual(self.balance(), D('250000.00'))

    def test_the_past_emis_can_be_posted_at_once(self):
        page = self.new_page(self.user)
        form = self.start_flow(page)
        self.basics(form, name='Back dated', start=self.today - timedelta(days=65))
        form.locator('input[name="record_loan_received"]').uncheck()
        self.next(page, form)
        self.set_select(page, 'select[name="payment_account"]', str(self.bank.pk))
        form.locator('input[name="create_historical_entries"]').check()
        self.to_review(page, form, steps=2)
        self.confirm(page)
        loan = self.made('Back dated')
        self.assertGreaterEqual(loan.repayments.count(), 2)
        self.assertLess(loan.remaining_principal, D('1200000.00'))


class FlowCheckTests(Base):
    def confirm_and_stay(self, page):
        page.locator('[data-confirm-btn]').first.click()
        page.wait_for_timeout(1200)

    def test_the_payment_account_is_needed_for_the_schedule(self):
        page = self.new_page(self.user)
        form = self.start_flow(page)
        self.basics(form)
        form.locator('input[name="record_loan_received"]').uncheck()
        self.next(page, form)
        page.evaluate("document.querySelector('select[name=\"payment_account\"]').value = ''")
        self.next(page, form)
        self.next(page, form)
        self.confirm_and_stay(page)
        from expenses.models import Loan
        self.assertEqual(Loan.objects.filter(user=self.user).count(), 0)
        self.assertIn('Select the account used to pay', page.inner_text('body'))

    def test_the_principal_already_paid_must_be_less_than_the_loan(self):
        page = self.new_page(self.user)
        form = self.start_flow(page)
        self.basics(form, principal='100000', start=self.today - timedelta(days=400))
        self.next(page, form)
        self.set_select(page, 'select[name="payment_account"]', str(self.bank.pk))
        self.next(page, form)
        form.locator('input[name="mid_tenure"]').check()
        form.locator('input[name="opening_paid_principal"]').fill('150000')
        self.next(page, form)
        self.confirm_and_stay(page)
        from expenses.models import Loan
        self.assertEqual(Loan.objects.filter(user=self.user).count(), 0)
        self.assertIn('must be less than the loan principal', page.inner_text('body'))

    def test_a_down_payment_needs_its_amount(self):
        page = self.new_page(self.user)
        form = self.start_flow(page)
        self.basics(form)
        form.locator('input[name="record_loan_received"]').uncheck()
        self.next(page, form)
        self.set_select(page, 'select[name="payment_account"]', str(self.bank.pk))
        self.next(page, form)
        form.locator('input[name="include_down_payment"]').check()
        self.next(page, form)
        self.confirm_and_stay(page)
        from expenses.models import Loan
        self.assertEqual(Loan.objects.filter(user=self.user).count(), 0)
        self.assertIn('Enter the down payment amount', page.inner_text('body'))

    def test_a_plus_plan_person_who_already_has_a_loan_is_offered_the_plans(self):
        from expenses.models import Loan, UserProfile
        UserProfile.objects.filter(user=self.user).update(tier='PLUS')
        make_loan(self.user, 'First', 100000, 12)
        page = self.new_page(self.user)
        form = self.start_flow(page)
        self.basics(form, name='Second')
        form.locator('input[name="record_loan_received"]').uncheck()
        self.next(page, form)
        self.set_select(page, 'select[name="payment_account"]', str(self.bank.pk))
        self.next(page, form)
        self.next(page, form)
        page.wait_for_selector('#tmr-review-loan', state='visible')
        page.wait_for_timeout(1000)
        self.assertIn('View plans', page.inner_text('body'))              # the review says the plan has no room
        self.assertTrue(page.locator('[data-confirm-btn]').first.is_disabled() or
                        'disabled' in (page.locator('[data-confirm-btn]').first.get_attribute('class') or ''))
        self.assertEqual(Loan.objects.filter(user=self.user).count(), 1)

    def test_a_double_click_on_confirm_creates_one_loan(self):
        from expenses.models import Loan
        page = self.new_page(self.user)
        form = self.start_flow(page)
        self.basics(form)
        form.locator('input[name="record_loan_received"]').uncheck()
        self.next(page, form)
        self.set_select(page, 'select[name="payment_account"]', str(self.bank.pk))
        self.next(page, form)
        self.next(page, form)
        page.wait_for_selector('#tmr-review-loan', state='visible')
        button = page.locator('[data-confirm-btn]').first
        button.click()
        try:
            button.click(timeout=300)
        except Exception:
            pass
        page.wait_for_url(lambda url: '/flows/loan' not in url, timeout=20000)
        self.assertEqual(Loan.objects.filter(user=self.user).count(), 1)

    def test_editing_from_the_review_goes_back_to_that_step(self):
        page = self.new_page(self.user)
        form = self.start_flow(page)
        self.basics(form)
        form.locator('input[name="record_loan_received"]').uncheck()
        self.next(page, form)
        self.set_select(page, 'select[name="payment_account"]', str(self.bank.pk))
        self.next(page, form)
        self.next(page, form)
        page.wait_for_selector('#tmr-review-loan', state='visible')
        page.locator('#tmr-review-loan >> text=Edit >> nth=0').click()
        page.wait_for_timeout(400)
        self.assertTrue(form.locator('input[name="principal"]').is_visible())

    def test_after_creating_a_loan_the_landing_shows_it_as_done(self):
        page = self.new_page(self.user)
        form = self.start_flow(page)
        self.basics(form)
        form.locator('input[name="record_loan_received"]').uncheck()
        self.next(page, form)
        self.set_select(page, 'select[name="payment_account"]', str(self.bank.pk))
        self.to_review(page, form, steps=2)
        page.locator('[data-confirm-btn]').first.click()
        page.wait_for_url(lambda url: '/flows/loan' not in url, timeout=20000)
        self.open(page, reverse('flow-landing'))
        self.assertIn('Edit existing', page.inner_text('body'))


class PhoneTests(Base):
    def overflow(self, page):
        return page.evaluate('document.documentElement.scrollWidth - window.innerWidth')

    def test_the_loans_pages_fit_a_phone_without_sideways_scrolling(self):
        loan = make_loan(self.user, 'Home Loan', 1200000, 120, '9', days_ago=100)
        make_repayment(loan, self.bank, 8000, 9000, days_ago=40)
        page = self.new_page(self.user, phone=True)
        for name, kwargs in (('loan-list', {}), ('loan-detail', {'pk': loan.uuid}), ('loan-create', {}), ('pay-emi', {}),
                             ('loan-edit', {'pk': loan.uuid})):
            self.open(page, reverse(name, kwargs=kwargs))
            page.wait_for_timeout(500)
            self.assertLessEqual(self.overflow(page), 2, name)

    def test_the_tabs_fit_a_phone_too(self):
        loan = make_loan(self.user, 'Home Loan', 1200000, 120, '9', days_ago=100)
        make_repayment(loan, self.bank, 8000, 9000, days_ago=40)
        page = self.new_page(self.user, phone=True)
        self.open(page, reverse('loan-detail', kwargs={'pk': loan.uuid}))
        for tab in ('schedule', 'history'):
            page.click(f'#tab-{tab}-btn')
            page.wait_for_selector(f'#tab-{tab}-pane .card')
            page.wait_for_timeout(400)
            self.assertLessEqual(self.overflow(page), 2, tab)

    def test_a_repayment_can_be_recorded_on_a_phone(self):
        loan = make_loan(self.user, 'Home Loan', 1200000, 120, '9', days_ago=100)
        page = self.new_page(self.user, phone=True)
        self.open(page, reverse('loan-detail', kwargs={'pk': loan.uuid}))
        page.click('button[data-bs-target="#repaymentModal"]')
        page.wait_for_selector('#repaymentModal.show')
        page.wait_for_timeout(400)
        page.fill('#repaymentModal input[name="amount"]', '20000')
        page.click('#repaymentModal button[type="submit"]')
        page.wait_for_load_state('load')
        page.wait_for_timeout(600)
        self.assertEqual(loan.repayments.get().amount, D('20000.00'))

    def test_the_emi_can_be_paid_from_the_pay_emi_page_on_a_phone(self):
        loan = make_loan(self.user, 'Home Loan', 1200000, 120, '9', days_ago=100)
        page = self.new_page(self.user, phone=True)
        self.open(page, reverse('pay-emi'))
        page.click('button:has-text("Record EMI")')
        page.wait_for_url(lambda url: '/pay-emi' not in url)
        self.assertEqual(loan.repayments.count(), 1)

    def test_the_loan_can_be_added_on_a_phone(self):
        from expenses.models import Loan
        page = self.new_page(self.user, phone=True)
        self.open(page, reverse('loan-create'))
        page.fill('input[name="name"]', 'Phone loan')
        page.fill('input[name="initial_principal"]', '90000')
        page.fill('input[name="interest_rate"]', '11')
        page.fill('input[name="duration_months"]', '18')
        page.fill('input[name="start_date"]', self.today.isoformat())
        page.click('button[type="submit"]:visible')
        page.wait_for_url(lambda url: '/loans/add' not in url)
        self.assertTrue(Loan.objects.filter(user=self.user, name='Phone loan').exists())

    def test_a_phone_sees_one_card_per_loan_with_its_actions(self):
        loan = make_loan(self.user, 'Card one', 500000, 60, '9', days_ago=60)
        page = self.new_page(self.user, phone=True)
        self.open(page, reverse('loan-list'))
        self.assertIn('Card one', page.inner_text('body'))
        self.assertTrue(page.locator(f'a[href="{reverse("loan-detail", kwargs={"pk": loan.uuid})}"]').first.is_visible())
