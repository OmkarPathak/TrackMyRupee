"""Pay EMI: the one-confirm page for a loan payment, its entries in the Add menu, and the Move Money hint."""

from decimal import Decimal

from django.test import override_settings
from django.urls import reverse

from expenses.models import Account, LoanRepayment
from expenses.tests.test_loans_module import LoanBase

D = Decimal


class PayEmiPageTests(LoanBase):
    def url(self, **query):
        base = reverse('pay-emi')
        return base + ('?' + '&'.join(f'{k}={v}' for k, v in query.items()) if query else '')

    def test_login_is_required(self):
        self.client.logout()
        self.assertEqual(self.client.get(self.url()).status_code, 302)

    def test_with_no_loan_it_points_at_the_guided_setup(self):
        response = self.client.get(self.url())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'You have not added a loan yet')
        self.assertContains(response, reverse('flow-detail', kwargs={'key': 'loan'}))
        self.assertContains(response, reverse('loan-create'))
        self.assertNotContains(response, 'name="from_pay_emi"')

    def test_with_a_loan_the_amount_is_filled_in_and_the_split_is_left_empty(self):
        loan = self.loan()
        response = self.client.get(self.url())
        self.assertEqual(response.status_code, 200)
        form = response.context['form']
        self.assertTrue(form['amount'].value())
        self.assertIsNone(form['principal_portion'].value())
        self.assertIsNone(form['interest_portion'].value())
        self.assertNotIn('recalculatePortions', str(form['amount']))
        self.assertContains(response, reverse('loan-repayment-create', kwargs={'pk': loan.uuid}))
        self.assertContains(response, 'Split into principal and interest (optional)')

    def test_paid_from_starts_on_the_account_used_for_the_last_emi(self):
        loan = self.loan()
        other = Account.objects.create(user=self.user, name='Salary', account_type='SALARY_ACCOUNT',
                                       balance=D('50000.00'), currency='₹')
        self.pay(loan, 5000, 1000, account=other)
        form = self.client.get(self.url()).context['form']
        self.assertEqual(form['from_account'].value(), other.pk)

    def test_with_no_history_paid_from_is_a_salary_or_savings_account_never_a_card(self):
        self.loan()
        Account.objects.create(user=self.user, name='Card', account_type='CREDIT_CARD', balance=D('0.00'), currency='₹')
        bank = Account.objects.create(user=self.user, name='Salary', account_type='SALARY_ACCOUNT',
                                      balance=D('50000.00'), currency='₹')
        form = self.client.get(self.url()).context['form']
        self.assertEqual(form['from_account'].value(), bank.pk)

    def test_with_several_loans_the_person_can_pick_which(self):
        first = self.loan(name='Home')
        second = self.loan(name='Car', started=first.start_date)
        response = self.client.get(self.url())
        self.assertContains(response, 'Which loan?')
        self.assertEqual(response.context['loan'], first)
        chosen = self.client.get(self.url(loan=second.uuid))
        self.assertEqual(chosen.context['loan'], second)

    def test_a_loan_that_is_not_theirs_is_never_offered(self):
        other = self.make_user('someone-else')
        theirs = self.loan(user=other, name='Theirs')
        response = self.client.get(self.url(loan=theirs.uuid))
        self.assertNotEqual(response.context['loan'], theirs)
        self.assertNotContains(response, 'Theirs')

    def test_the_free_plan_is_sent_to_pricing(self):
        free = self.make_user('free-user', 'FREE')
        self.client.force_login(free)
        response = self.client.get(self.url())
        self.assertRedirects(response, reverse('pricing'), fetch_redirect_response=False)


class PayEmiRecordTests(LoanBase):
    def post(self, loan, **data):
        payload = {'from_pay_emi': '1', 'from_account': self.cash.pk, 'amount': '9000',
                   'date': today_str()}
        payload.update(data)
        return self.client.post(reverse('loan-repayment-create', kwargs={'pk': loan.pk}), payload)

    def test_recording_with_no_split_works_it_out_and_debits_the_account(self):
        loan = self.loan()
        before = self.bal(self.cash)
        response = self.post(loan)
        repayment = LoanRepayment.objects.get(loan=loan)
        self.assertEqual(repayment.amount, D('9000.00'))
        self.assertEqual(repayment.principal_portion + repayment.interest_portion, D('9000.00'))
        self.assertGreater(repayment.interest_portion, 0)
        self.assertEqual(self.bal(self.cash), before - D('9000.00'))
        self.assertRedirects(response, reverse('loan-detail', kwargs={'pk': loan.uuid}), fetch_redirect_response=False)

    def test_the_same_payment_sent_twice_in_a_row_is_recorded_once(self):
        loan = self.loan()
        before = self.bal(self.cash)
        self.post(loan)
        response = self.post(loan)
        self.assertEqual(LoanRepayment.objects.filter(loan=loan).count(), 1)
        self.assertEqual(self.bal(self.cash), before - D('9000.00'))
        self.assertRedirects(response, reverse('loan-detail', kwargs={'pk': loan.uuid}), fetch_redirect_response=False)

    def test_a_different_amount_straight_after_is_a_new_payment(self):
        loan = self.loan()
        self.post(loan)
        self.post(loan, amount='9500')
        self.assertEqual(LoanRepayment.objects.filter(loan=loan).count(), 2)

    def test_the_button_is_switched_off_after_the_first_click(self):
        self.loan()
        self.assertContains(self.client.get(reverse('pay-emi')), "b.disabled = true")

    def test_the_page_is_counted_as_opened_only_on_a_fresh_visit(self):
        from unittest import mock
        loan = self.loan()
        with mock.patch('expenses.views.loans.ph_capture') as capture:
            self.client.get(reverse('pay-emi'))
            self.client.get(reverse('pay-emi') + f'?loan={loan.uuid}')
        events = [c.args[1] for c in capture.call_args_list]
        self.assertEqual(events.count('pay_emi_opened'), 1)

    def test_the_person_goes_back_to_where_they_came_from(self):
        loan = self.loan()
        response = self.post(loan, next=reverse('account-list'))
        self.assertRedirects(response, reverse('account-list'), fetch_redirect_response=False)

    @override_settings(ALLOWED_HOSTS=['testserver'])
    def test_an_outside_address_is_never_followed(self):
        loan = self.loan()
        response = self.post(loan, next='https://evil.example/steal')
        self.assertRedirects(response, reverse('loan-detail', kwargs={'pk': loan.uuid}), fetch_redirect_response=False)

    def test_a_typed_split_is_respected(self):
        loan = self.loan()
        self.post(loan, principal_portion='6000', interest_portion='3000')
        repayment = LoanRepayment.objects.get(loan=loan)
        self.assertEqual((repayment.principal_portion, repayment.interest_portion), (D('6000.00'), D('3000.00')))

    def test_a_bad_amount_comes_back_to_the_page_and_records_nothing(self):
        loan = self.loan()
        response = self.post(loan, amount='0')
        self.assertRedirects(response, reverse('pay-emi') + f'?loan={loan.uuid}', fetch_redirect_response=False)
        self.assertFalse(LoanRepayment.objects.filter(loan=loan).exists())
        self.assertTrue(any('Error recording repayment' in m for m in self.messages(response)))

    def test_the_loan_page_form_still_goes_back_to_the_loan_page(self):
        loan = self.loan()
        response = self.client.post(reverse('loan-repayment-create', kwargs={'pk': loan.pk}),
                                    {'from_account': self.cash.pk, 'amount': '9000', 'date': today_str()})
        self.assertRedirects(response, reverse('loan-detail', kwargs={'pk': loan.uuid}), fetch_redirect_response=False)


class AddMenuTests(LoanBase):
    def page(self):
        response = self.client.get(reverse('settings-home'))
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def test_pay_emi_is_in_the_add_menu_and_the_phone_sheet(self):
        html = self.page()
        self.assertGreaterEqual(html.count(reverse('pay-emi')), 2)
        self.assertIn('Pay EMI', html)

    def test_move_money_is_still_there_and_expense_is_still_first(self):
        html = self.page()
        self.assertIn(reverse('transfer-create'), html)
        self.assertLess(html.index(reverse('expense-create')), html.index(reverse('pay-emi')))

    def test_a_free_user_sees_pay_emi_with_a_plus_badge(self):
        free = self.make_user('free-menu', 'FREE')
        self.client.force_login(free)
        html = self.page()
        start = html.index(reverse('pay-emi'))
        self.assertIn('Plus', html[start:start + 600])


class MoveMoneyHintTests(LoanBase):
    def test_with_one_account_the_form_says_it_needs_two(self):
        response = self.client.get(reverse('transfer-create'))
        self.assertContains(response, 'Moving money needs two accounts')

    def test_with_two_accounts_there_is_no_hint(self):
        Account.objects.create(user=self.user, name='Savings', account_type='SAVINGS_ACCOUNT',
                               balance=D('500.00'), currency='₹')
        response = self.client.get(reverse('transfer-create'))
        self.assertNotContains(response, 'Moving money needs two accounts')


class MoveMoneySwapTests(LoanBase):
    def test_with_two_accounts_there_is_a_swap_link_and_no_decorative_arrow(self):
        Account.objects.create(user=self.user, name='Savings', account_type='SAVINGS_ACCOUNT',
                               balance=D('500.00'), currency='₹')
        response = self.client.get(reverse('transfer-create'))
        self.assertContains(response, 'id="swapAccounts"')
        self.assertNotContains(response, 'bi-arrow-down"')

    def test_with_one_account_there_is_nothing_to_swap(self):
        self.assertNotContains(self.client.get(reverse('transfer-create')), 'id="swapAccounts"')


class MoveMoneyQueryTests(LoanBase):
    def count_of_account_counts(self, url):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        with CaptureQueriesContext(connection) as queries:
            self.client.get(url)
        return sum(1 for q in queries if 'COUNT(*)' in q['sql'] and 'expenses_account' in q['sql'] and 'is_active' in q['sql'])

    def test_a_new_transfer_counts_the_accounts_at_most_once(self):
        Account.objects.create(user=self.user, name='Savings', account_type='SAVINGS_ACCOUNT',
                               balance=D('500.00'), currency='₹')
        self.assertLessEqual(self.count_of_account_counts(reverse('transfer-create')), 1)


def today_str():
    from django.utils import timezone
    return timezone.localdate().isoformat()
