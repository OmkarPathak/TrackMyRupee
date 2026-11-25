"""Loans, the first half: adding one, the live EMI preview, the form's rules, editing, deleting, and the Loans list
(its status filters, its empty state and the plan limits)."""
from decimal import Decimal

from django.urls import reverse
from django.utils import timezone

from e2e.base import E2ETestCase, make_account, make_user
from e2e.loan_helpers import confirm_delete, emi, make_loan, make_repayment

D = Decimal


class Base(E2ETestCase):
    def setUp(self):
        super().setUp()
        self.user = make_user('borrower')
        self.bank = make_account(self.user, 'HDFC Savings', 'SAVINGS_ACCOUNT', 500000)
        self.today = timezone.localdate()

    def fill_form(self, page, name='Car Loan', principal='500000', rate='9', months='60', start=None, loan_type=None):
        page.fill('input[name="name"]', name)
        if loan_type:
            self.set_select(page, 'select[name="loan_type"]', loan_type)
        page.fill('input[name="initial_principal"]', principal)
        page.fill('input[name="interest_rate"]', rate)
        page.fill('input[name="duration_months"]', months)
        page.fill('input[name="start_date"]', (start or self.today).isoformat())

    def balance(self):
        self.bank.refresh_from_db()
        return self.bank.balance


class AddLoanTests(Base):
    def test_every_kind_of_loan_can_be_added_and_gets_its_first_rate(self):
        from expenses.models import Loan
        page = self.new_page(self.user)
        for loan_type in ('HOME', 'CAR', 'PERSONAL', 'EDUCATION', 'BUSINESS', 'OTHER'):
            self.open(page, reverse('loan-create'))
            self.fill_form(page, name=f'{loan_type.title()} loan', loan_type=loan_type)
            page.click('button[type="submit"]:visible')
            page.wait_for_url(lambda url: '/loans/add' not in url)
            loan = Loan.objects.get(user=self.user, name=f'{loan_type.title()} loan')
            self.assertEqual(loan.loan_type, loan_type)
            self.assertEqual([str(r.interest_rate) for r in loan.interest_rates.all()], ['9.00'])
            self.assertEqual(loan.interest_rates.get().effective_date, loan.start_date)
        self.assertEqual(Loan.objects.filter(user=self.user).count(), 6)

    def test_the_emi_preview_follows_what_is_typed(self):
        page = self.new_page(self.user)
        self.open(page, reverse('loan-create'))
        self.assertEqual(page.inner_text('#emi-preview-value').strip(), '--')
        self.fill_form(page, principal='1200000', rate='9', months='120')
        expected = emi(1200000, 9, 120)
        shown = page.inner_text('#emi-preview-value').replace(',', '')
        self.assertAlmostEqual(float(''.join(ch for ch in shown if ch.isdigit() or ch == '.')), expected, delta=0.01)
        page.fill('input[name="duration_months"]', '')
        self.assertEqual(page.inner_text('#emi-preview-value').strip(), '--')

    def test_a_loan_at_zero_interest_previews_the_principal_over_the_months(self):
        page = self.new_page(self.user)
        self.open(page, reverse('loan-create'))
        self.fill_form(page, principal='120000', rate='0', months='12')
        shown = page.inner_text('#emi-preview-value').replace(',', '')
        self.assertAlmostEqual(float(''.join(ch for ch in shown if ch.isdigit() or ch == '.')), 10000.0, delta=0.01)

    def test_the_form_refuses_what_makes_no_sense_and_saves_nothing(self):
        from expenses.models import Loan
        page = self.new_page(self.user)
        cases = (
            (dict(principal='0'), 'Principal must be greater than zero.'),
            (dict(months='601'), 'Duration must be between 1 and 600 months.'),
            (dict(months='0'), 'Duration must be between 1 and 600 months.'),
            (dict(rate='101'), 'Interest rate must be between 0 and 100 percent.'),
            (dict(rate='-1'), 'Interest rate must be between 0 and 100 percent.'),
        )
        for fields, message in cases:
            with self.subTest(fields=fields):
                self.open(page, reverse('loan-create'))
                self.fill_form(page, **fields)
                page.click('button[type="submit"]:visible')
                page.wait_for_selector('form div.text-danger.small')
                self.assertIn(message, page.inner_text('body'))
                self.assertEqual(Loan.objects.filter(user=self.user).count(), 0)

    def test_a_blank_form_is_not_submitted(self):
        from expenses.models import Loan
        page = self.new_page(self.user)
        self.open(page, reverse('loan-create'))
        page.click('button[type="submit"]:visible')
        page.wait_for_timeout(500)
        self.assertEqual(Loan.objects.filter(user=self.user).count(), 0)

    def test_a_loan_added_shows_in_the_list_with_its_figures(self):
        page = self.new_page(self.user)
        self.open(page, reverse('loan-create'))
        self.fill_form(page, name='Car Loan', principal='500000', rate='9', months='60')
        page.click('button[type="submit"]:visible')
        page.wait_for_url(lambda url: '/loans/add' not in url)
        text = page.inner_text('body')
        self.assertIn('Car Loan', text)
        self.assertIn('Loan created successfully', text)
        self.assertIn('5,00,000', text.replace('₹', ''))


class EditAndDeleteTests(Base):
    def test_editing_a_loan_changes_it_and_its_first_rate(self):
        loan = make_loan(self.user, 'Old name', 600000, 60, '8')
        page = self.new_page(self.user)
        self.open(page, reverse('loan-edit', kwargs={'pk': loan.uuid}))
        self.assertEqual(page.input_value('input[name="interest_rate"]'), '8.00')
        page.fill('input[name="name"]', 'New name')
        page.fill('input[name="initial_principal"]', '700000')
        page.fill('input[name="interest_rate"]', '9.5')
        page.fill('input[name="duration_months"]', '72')
        page.click('button[type="submit"]:visible')
        page.wait_for_url(lambda url: '/edit' not in url)
        loan.refresh_from_db()
        self.assertEqual((loan.name, loan.initial_principal, loan.duration_months), ('New name', D('700000.00'), 72))
        self.assertEqual([str(r.interest_rate) for r in loan.interest_rates.all()], ['9.50'])
        self.open(page, reverse('loan-list'))
        self.assertIn('New name', page.inner_text('body'))

    def test_a_rate_change_recorded_later_is_kept_when_the_loan_is_edited(self):
        from expenses.models import LoanInterestRate
        loan = make_loan(self.user, 'Two rates', 600000, 60, '8')
        LoanInterestRate.objects.create(loan=loan, interest_rate=D('10'), effective_date=self.today)
        page = self.new_page(self.user)
        self.open(page, reverse('loan-edit', kwargs={'pk': loan.uuid}))
        page.fill('input[name="name"]', 'Two rates, renamed')
        page.click('button[type="submit"]:visible')
        page.wait_for_url(lambda url: '/edit' not in url)
        self.assertEqual(sorted(str(r.interest_rate) for r in loan.interest_rates.all()), ['10.00', '8.00'])

    def test_editing_the_principal_down_to_what_is_paid_closes_the_loan(self):
        loan = make_loan(self.user, 'Small', 100000, 12, '10')
        make_repayment(loan, self.bank, 60000, 1000)
        page = self.new_page(self.user)
        self.open(page, reverse('loan-edit', kwargs={'pk': loan.uuid}))
        page.fill('input[name="initial_principal"]', '60000')
        page.click('button[type="submit"]:visible')
        page.wait_for_url(lambda url: '/edit' not in url)
        loan.refresh_from_db()
        self.assertFalse(loan.is_active)

    def test_deleting_a_loan_from_the_list_gives_the_repaid_money_back(self):
        from expenses.models import Loan, LoanRepayment
        loan = make_loan(self.user, 'To delete', 600000, 60, '8')
        make_repayment(loan, self.bank, 8000, 4000)
        self.assertEqual(self.balance(), D('488000.00'))
        page = self.new_page(self.user)
        self.open(page, reverse('loan-list'))
        confirm_delete(self, page, f'button[data-url="{reverse("loan-delete", kwargs={"pk": loan.uuid})}"]')
        self.wait_until(lambda: not Loan.objects.filter(pk=loan.pk).exists())
        self.assertFalse(LoanRepayment.objects.filter(loan_id=loan.pk).exists())
        self.assertEqual(self.balance(), D('500000.00'))
        self.assertNotIn('To delete', page.inner_text('body'))

    def test_cancelling_the_delete_keeps_the_loan(self):
        from expenses.models import Loan
        loan = make_loan(self.user, 'Keep me')
        page = self.new_page(self.user)
        self.open(page, reverse('loan-list'))
        page.click(f'button[data-url="{reverse("loan-delete", kwargs={"pk": loan.uuid})}"]')
        page.wait_for_selector('#deleteModal.show')
        page.click('#deleteModal button[data-bs-dismiss="modal"]')
        page.wait_for_timeout(500)
        self.assertTrue(Loan.objects.filter(pk=loan.pk).exists())

    def test_another_persons_loan_cannot_be_opened_edited_or_deleted(self):
        other = make_user('someone-else')
        theirs = make_loan(other, 'Theirs')
        page = self.new_page(self.user)
        for name in ('loan-detail', 'loan-edit'):
            response = page.goto(self.live_server_url + reverse(name, kwargs={'pk': theirs.uuid}))
            self.assertEqual(response.status, 404, name)


class ListTests(Base):
    def test_the_list_filters_by_active_and_paid_off(self):
        active = make_loan(self.user, 'Still owing', 500000, 60)
        done = make_loan(self.user, 'All paid', 50000, 12)
        make_repayment(done, self.bank, 50000, 0)
        page = self.new_page(self.user)
        self.open(page, reverse('loan-list'))
        text = page.inner_text('body')
        self.assertIn('Still owing', text)
        self.assertNotIn('All paid', text)
        page.click('button:has-text("Paid Off")')
        page.wait_for_function("document.body.innerText.includes('All paid')")
        self.assertNotIn('Still owing', page.inner_text('body'))
        page.click('button:has-text("All Loans")')
        page.wait_for_function("document.body.innerText.includes('Still owing') && document.body.innerText.includes('All paid')")
        self.assertIsNotNone(active)

    def test_the_filter_counts_match_the_loans(self):
        make_loan(self.user, 'One', 500000, 60)
        make_loan(self.user, 'Two', 300000, 36)
        done = make_loan(self.user, 'Three', 50000, 12)
        make_repayment(done, self.bank, 50000, 0)
        page = self.new_page(self.user)
        self.open(page, reverse('loan-list'))
        pills = page.inner_text('#loans-shell')
        self.assertRegex(pills, r'Active\s*2')
        self.assertRegex(pills, r'Paid Off\s*1')
        self.assertRegex(pills, r'All Loans\s*3')

    def test_a_person_with_no_loans_is_invited_to_add_one(self):
        page = self.new_page(self.user)
        self.open(page, reverse('loan-list'))
        self.assertIn('Add Your First Loan', page.inner_text('body'))
        page.click('a:has-text("Add Your First Loan")')
        page.wait_for_url(lambda url: '/loans/add' in url)

    def test_a_card_shows_what_is_left_and_what_is_paid(self):
        loan = make_loan(self.user, 'Card check', 1000000, 120, '9')
        make_repayment(loan, self.bank, 100000, 5000)
        page = self.new_page(self.user)
        self.open(page, reverse('loan-list'))
        text = page.inner_text('body')
        self.assertIn('Card check', text)
        self.assertRegex(text.replace(',', ''), r'9,?00,?000|900000|9\s?L|9\.0\s?L')

    def test_the_charts_are_drawn_and_the_comparison_needs_two_loans(self):
        make_loan(self.user, 'One', 500000, 60)
        page = self.new_page(self.user)
        self.open(page, reverse('loan-list'))
        page.wait_for_timeout(800)
        self.assertGreater(page.eval_on_selector('#portfolioBreakdownChart', 'c => c.width * c.height'), 0)
        self.assertEqual(page.locator('#loanComparisonChart').count(), 0)
        make_loan(self.user, 'Two', 300000, 36)
        self.open(page, reverse('loan-list'))
        page.wait_for_timeout(800)
        self.assertGreater(page.eval_on_selector('#loanComparisonChart', 'c => c.width * c.height'), 0)

    def test_the_list_opens_a_loan(self):
        loan = make_loan(self.user, 'Open me')
        page = self.new_page(self.user)
        self.open(page, reverse('loan-list'))
        page.click(f'a[href="{reverse("loan-detail", kwargs={"pk": loan.uuid})}"]')
        page.wait_for_url(lambda url: f'/loans/{loan.uuid}' in url)
        self.assertIn('Open me', page.inner_text('h1'))


class PlanTests(Base):
    def set_tier(self, tier):
        from expenses.models import UserProfile
        UserProfile.objects.filter(user=self.user).update(tier=tier)

    def test_a_free_plan_person_is_sent_to_the_plans(self):
        self.set_tier('FREE')
        page = self.new_page(self.user)
        page.goto(self.live_server_url + reverse('loan-list'))
        page.wait_for_url(lambda url: '/pricing' in url)
        self.assertIn('Choose the plan that fits', page.inner_text('body'))

    def test_a_plus_plan_person_can_keep_one_loan_until_it_is_paid_off(self):
        from expenses.models import Loan
        self.set_tier('PLUS')
        first = make_loan(self.user, 'First', 100000, 12)
        page = self.new_page(self.user)
        self.open(page, reverse('loan-create'))
        self.fill_form(page, name='Second')
        page.click('button[type="submit"]:visible')
        page.wait_for_url(lambda url: '/pricing' in url)
        self.assertFalse(Loan.objects.filter(name='Second').exists())
        make_repayment(first, self.bank, 100000, 0)           # paying the first off frees its place
        self.open(page, reverse('loan-create'))
        self.fill_form(page, name='Second')
        page.click('button[type="submit"]:visible')
        page.wait_for_url(lambda url: '/loans/add' not in url and '/pricing' not in url)
        self.assertTrue(Loan.objects.filter(name='Second').exists())
