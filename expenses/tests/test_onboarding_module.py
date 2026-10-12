"""Onboarding: the three steps, what they write, the checklist, events and the view's guards."""

import datetime
import json
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from expenses.ledger_read_service import LedgerReadService
from expenses.models import (
    Account,
    Category,
    Expense,
    Income,
    OnboardingEvent,
    OnboardingState,
    RecurringTransaction,
    SavingsGoal,
    UserProfile,
)
from expenses.onboarding_v2 import compute_cycle_range, get_checklist_items

D = Decimal


class OnbBase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('onb-user', password='pass', email='onb@example.com')
        profile = self.user.profile
        profile.currency = '₹'
        profile.has_seen_tutorial = False
        profile.consent_granted = True
        profile.save()
        self.client.force_login(self.user)
        self.url = reverse('onboarding')
        cache.clear()

    def post(self, **data):
        response = self.client.post(self.url, json.dumps(data), content_type='application/json')
        return response

    def ok(self, **data):
        response = self.post(**data)
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()

    def step2(self, **kw):
        values = dict(action='step2', payday=25, salary_amount='50000', balance_now='35000', bank_choice='SBI',
                      auto_log_salary=True)
        values.update(kw)
        return self.post(**values)

    def state(self):
        return OnboardingState.objects.get(user=self.user)

    def profile(self):
        return UserProfile.objects.get(user=self.user)


class TestCycleRange(TestCase):
    def test_a_payday_in_the_future_this_month_starts_last_month(self):
        start, end, days = compute_cycle_range(25, datetime.date(2026, 3, 10))
        self.assertEqual((start, end, days), (datetime.date(2026, 2, 25), datetime.date(2026, 3, 24), 15))

    def test_the_payday_itself_starts_a_new_cycle(self):
        start, end, days = compute_cycle_range(25, datetime.date(2026, 3, 25))
        self.assertEqual((start, end, days), (datetime.date(2026, 3, 25), datetime.date(2026, 4, 24), 31))

    def test_the_first_of_the_month(self):
        start, end, _ = compute_cycle_range(1, datetime.date(2026, 3, 17))
        self.assertEqual((start, end), (datetime.date(2026, 3, 1), datetime.date(2026, 3, 31)))

    def test_a_31st_payday_in_a_short_month_lands_on_its_last_day(self):
        start, end, days = compute_cycle_range(31, datetime.date(2026, 2, 28))
        self.assertEqual(start, datetime.date(2026, 2, 28))
        self.assertEqual(end, datetime.date(2026, 3, 30))

    def test_last_day_of_month_across_the_year_end(self):
        start, end, days = compute_cycle_range(32, datetime.date(2026, 12, 31))
        self.assertEqual((start, end), (datetime.date(2026, 12, 31), datetime.date(2027, 1, 30)))
        self.assertEqual(days, 31)

    def test_january_before_payday_starts_in_december(self):
        start, end, _ = compute_cycle_range(20, datetime.date(2026, 1, 5))
        self.assertEqual((start, end), (datetime.date(2025, 12, 20), datetime.date(2026, 1, 19)))

    def test_a_leap_february_is_handled(self):
        start, end, _ = compute_cycle_range(30, datetime.date(2028, 2, 29))
        self.assertEqual(start, datetime.date(2028, 2, 29))


class TestViewGuards(OnbBase):
    def test_a_new_user_sees_the_page(self):
        self.assertEqual(self.client.get(self.url).status_code, 200)

    def test_someone_who_finished_is_sent_home(self):
        UserProfile.objects.filter(user=self.user).update(has_seen_tutorial=True)
        self.assertRedirects(self.client.get(self.url), reverse('home'), fetch_redirect_response=False)

    def test_force_and_preview_open_it_again(self):
        UserProfile.objects.filter(user=self.user).update(has_seen_tutorial=True)
        self.assertEqual(self.client.get(self.url, {'force': 'true'}).status_code, 200)
        self.assertEqual(self.client.get(self.url, {'preview': 'true'}).status_code, 200)

    def test_login_is_required(self):
        self.client.logout()
        self.assertEqual(self.client.get(self.url).status_code, 302)
        self.assertEqual(self.post(action='finish').status_code, 302)

    def test_bad_json_and_unknown_actions_are_a_400(self):
        self.assertEqual(self.client.post(self.url, 'not json', content_type='application/json').status_code, 400)
        self.assertEqual(self.post(action='nope').status_code, 400)

    def test_opening_it_records_the_started_event_without_amounts(self):
        self.client.get(self.url)
        events = OnboardingEvent.objects.filter(user=self.user)
        self.assertTrue(events.filter(event='onboarding_started').exists())


class TestStep1(OnbBase):
    def test_a_persona_is_saved_and_unknown_ones_become_salaried(self):
        self.ok(action='step1', persona='freelancer')
        self.assertEqual((self.state().persona, self.profile().persona), ('FREELANCER', 'FREELANCER'))
        self.ok(action='step1', persona='wizard')
        self.assertEqual(self.state().persona, 'SALARIED')
        self.assertEqual(self.state().current_step, 2)

    def test_skipping_clears_the_persona(self):
        self.ok(action='step1', persona='STUDENT')
        self.ok(action='step1', skip=True)
        self.assertIsNone(self.state().persona)
        self.assertTrue(self.state().step1_skipped)


class TestStep2(OnbBase):
    def test_the_typed_balance_is_what_every_screen_shows(self):
        self.assertEqual(self.step2().status_code, 200)
        account = self.state().account
        account.refresh_from_db()
        self.assertEqual(account.balance, D('35000.00'))
        self.assertEqual(LedgerReadService.get_account_balance(account), D('35000.00'))
        response = self.client.get(reverse('account-list'))
        shown = {a.name: a.display_balance for a in response.context['accounts']}
        self.assertEqual(shown['SBI Savings Account'], D('35000.00'))

    def test_it_creates_the_account_salary_and_a_monthly_rule(self):
        self.step2()
        state = self.state()
        self.assertEqual(state.account.name, 'SBI Savings Account')
        self.assertEqual(state.income.amount, D('50000.00'))
        self.assertEqual(state.income.account, state.account)
        rule = state.recurring_transaction
        self.assertEqual((rule.amount, rule.frequency, rule.transaction_type), (D('50000.00'), 'MONTHLY', 'INCOME'))
        self.assertEqual(rule.account, state.account)

    def test_the_salary_is_dated_at_the_start_of_the_cycle(self):
        self.step2(payday=25)
        start, _, _ = compute_cycle_range(25)
        self.assertEqual(self.state().income.date, start)

    def test_the_persona_names_the_income_source(self):
        self.ok(action='step1', persona='FREELANCER')
        self.step2()
        self.assertEqual(self.state().income.source, 'Client Income')

    def test_cash_and_custom_banks(self):
        self.step2(bank_choice='CASH')
        self.assertEqual((self.state().account.name, self.state().account.account_type), ('Cash Wallet', 'CASH_WALLET'))
        self.step2(bank_choice='OTHER', custom_bank_name='  Local Co-op ')
        self.assertEqual(self.state().account.name, 'Local Co-op')

    def test_going_back_and_resubmitting_updates_instead_of_duplicating(self):
        self.step2()
        self.step2(salary_amount='60000', bank_choice='HDFC', balance_now='1000')
        self.assertEqual(Account.objects.filter(user=self.user).count(), 1)
        self.assertEqual(Income.objects.filter(user=self.user).count(), 1)
        self.assertEqual(RecurringTransaction.objects.filter(user=self.user).count(), 1)
        self.assertEqual(self.state().account.name, 'HDFC Savings Account')
        self.assertEqual(self.state().account.balance, D('1000.00'))

    def test_turning_off_auto_log_removes_the_rule(self):
        self.step2()
        self.step2(auto_log_salary=False)
        self.assertEqual(RecurringTransaction.objects.filter(user=self.user).count(), 0)
        self.assertIsNone(self.state().recurring_transaction)

    def test_no_salary_means_no_income_or_rule(self):
        self.step2(salary_amount='')
        self.assertEqual(Income.objects.filter(user=self.user).count(), 0)
        self.assertEqual(RecurringTransaction.objects.filter(user=self.user).count(), 0)

    def test_the_payday_is_saved_on_the_profile(self):
        self.step2(payday=7)
        self.assertEqual(self.profile().salary_date, 7)

    def test_last_day_of_month_is_stored_as_the_31st_so_it_follows_every_month(self):
        self.step2(payday=32)
        self.assertEqual(self.profile().salary_date, 31)
        self.assertTrue(self.state().recurring_transaction.is_last_day_of_month)

    def test_an_existing_account_with_the_same_name_keeps_its_money_when_no_balance_is_typed(self):
        existing = Account.objects.create(user=self.user, name='SBI Savings Account',
                                          account_type='SAVINGS_ACCOUNT', balance=D('80000'), currency='₹')
        self.step2(balance_now='', salary_amount='')
        existing.refresh_from_db()
        self.assertEqual(existing.balance, D('80000.00'))

    def test_skipping_moves_on_and_leaves_nothing_behind(self):
        self.ok(action='step2', skip=True)
        self.assertEqual(self.state().current_step, 3)
        self.assertTrue(self.state().step2_skipped)
        self.assertEqual(Account.objects.filter(user=self.user).count(), 0)

    def test_garbage_input_is_a_clean_400(self):
        self.assertEqual(self.step2(payday='abc').status_code, 400)
        self.assertEqual(Account.objects.filter(user=self.user).count(), 0)


class TestStep3(OnbBase):
    def setUp(self):
        super().setUp()
        self.step2()

    def confirm(self, **kw):
        values = dict(action='step3_confirm', amount='250', description='Chai', category='Food & Dining')
        values.update(kw)
        return self.post(**values)

    def test_the_first_expense_is_recorded_and_moves_the_balance(self):
        response = self.confirm()
        self.assertEqual(response.status_code, 200)
        expense = self.state().expense
        self.assertEqual((expense.amount, expense.description), (D('250.00'), 'Chai'))
        self.assertEqual(expense.account, self.state().account)
        self.state().account.refresh_from_db()
        self.assertEqual(self.state().account.balance, D('34750.00'))

    def test_confirming_again_edits_the_same_expense(self):
        self.confirm()
        self.confirm(amount='300')
        self.assertEqual(Expense.objects.filter(user=self.user).count(), 1)
        self.assertEqual(self.state().expense.amount, D('300.00'))

    def test_zero_negative_and_missing_amounts_are_refused(self):
        for amount in ('0', '-50', '', 'abc'):
            response = self.confirm(amount=amount)
            self.assertFalse(response.json()['success'], amount)
        self.assertEqual(Expense.objects.filter(user=self.user).count(), 0)

    def test_the_category_exists_afterwards(self):
        self.confirm(category='Food & Dining')
        self.assertTrue(Category.objects.filter(user=self.user, name__iexact='Food & Dining').exists())

    def test_an_existing_category_is_reused_whatever_its_case(self):
        Category.objects.create(user=self.user, name='Groceries')
        self.confirm(category='groceries')
        self.assertEqual(Category.objects.filter(user=self.user, name__iexact='groceries').count(), 1)
        self.assertEqual(self.state().expense.category, 'Groceries')

    def test_another_users_account_cannot_be_used(self):
        other = User.objects.create_user('someone-else', password='pass')
        theirs = Account.objects.create(user=other, name='Theirs', balance=D('100'), currency='₹')
        self.confirm(account_id=theirs.pk)
        self.assertEqual(self.state().expense.account, self.state().account)
        theirs.refresh_from_db()
        self.assertEqual(theirs.balance, D('100.00'))

    def test_a_bad_date_falls_back_to_today(self):
        self.confirm(date='not-a-date')
        self.assertEqual(self.state().expense.date, datetime.date.today())

    def test_no_account_gives_a_clear_error(self):
        Account.objects.filter(user=self.user).update(is_active=False)
        response = self.confirm()
        self.assertFalse(response.json()['success'])

    def test_skipping(self):
        self.ok(action='step3_confirm', skip=True)
        self.assertTrue(self.state().step3_skipped)
        self.assertEqual(self.state().current_step, 4)


class TestFinishing(OnbBase):
    def test_finishing_marks_the_tutorial_seen_and_stamps_the_time(self):
        self.ok(action='finish')
        profile = self.profile()
        self.assertTrue(profile.has_seen_tutorial)
        self.assertIsNotNone(profile.onboarding_completed_at)
        self.assertIsNotNone(self.state().completed_at)
        self.assertTrue(OnboardingEvent.objects.filter(user=self.user, event='onboarding_completed').exists())

    def test_skip_all_finishes_too(self):
        self.ok(action='skip_all')
        self.assertTrue(self.profile().has_seen_tutorial)

    def test_dismissing_the_checklist_is_remembered(self):
        self.ok(action='dismiss_checklist')
        self.assertTrue(self.profile().dismissed_onboarding_checklist)

    def test_the_cycle_preview_for_a_payday(self):
        data = self.ok(action='cycle_range', payday=1)
        self.assertEqual(set(data), {'success', 'start_str', 'end_str', 'days_left'})


class TestEvents(OnbBase):
    def test_a_known_event_is_stored_without_private_properties(self):
        self.ok(action='event', event='quick_add_parsed', properties={'amount': 500, 'description': 'x', 'lang': 'en'})
        event = OnboardingEvent.objects.get(user=self.user, event='quick_add_parsed')
        self.assertEqual(event.properties, {'lang': 'en'})

    def test_made_up_events_are_not_stored(self):
        self.ok(action='event', event='anything_i_like', properties={})
        self.assertFalse(OnboardingEvent.objects.filter(user=self.user, event='anything_i_like').exists())

    def test_property_values_must_be_small_scalars(self):
        self.ok(action='event', event='checklist_item_opened',
                properties={'item': 'x' * 5000, 'nested': {'a': 1}, 'ok': 'fine', 'n': 3})
        event = OnboardingEvent.objects.get(user=self.user, event='checklist_item_opened')
        self.assertEqual(event.properties.get('ok'), 'fine')
        self.assertEqual(event.properties.get('n'), 3)
        self.assertNotIn('nested', event.properties)
        self.assertLessEqual(len(str(event.properties.get('item', ''))), 200)


class TestChecklist(OnbBase):
    def items(self):
        fresh = User.objects.get(pk=self.user.pk)  # a request always sees the current profile
        items, done, total = get_checklist_items(fresh)
        return {i['id']: i for i in items}, done, total

    def test_a_brand_new_user_has_everything_to_do(self):
        items, done, total = self.items()
        self.assertFalse(items['add_account']['done'])
        self.assertFalse(items['add_salary']['done'])
        self.assertTrue(items['setup_basics']['done'])
        self.assertEqual(done, 1)

    def test_a_payday_on_the_first_counts_as_set(self):
        self.assertIn('set_payday', self.items()[0])
        self.step2(payday=1)
        self.assertNotIn('set_payday', self.items()[0])

    def test_finishing_the_steps_clears_the_basics_from_the_list(self):
        self.step2()
        self.post(action='step3_confirm', amount='10', description='x', category='Food')
        items, _, _ = self.items()
        for key in ('set_payday', 'add_salary', 'add_account', 'log_first_expense'):
            self.assertNotIn(key, items, key)
        self.assertTrue(items['setup_basics']['done'])

    def test_the_persona_changes_the_suggestions(self):
        self.ok(action='step1', persona='FREELANCER')
        self.assertIn('log_client_payment', self.items()[0])
        UserProfile.objects.filter(user=self.user).update(persona='FAMILY')
        self.assertIn('add_loan_emi', self.items()[0])
        UserProfile.objects.filter(user=self.user).update(persona='STUDENT')
        self.assertNotIn('add_loan_emi', self.items()[0])

    def test_any_investment_account_ticks_the_investments_item(self):
        UserProfile.objects.filter(user=self.user).update(persona='STUDENT')
        for code in ('MUTUAL_FUND', 'PPF', 'DEMAT', 'RD'):
            Account.objects.all().delete()
            Account.objects.create(user=self.user, name=code, account_type=code, balance=0, currency='₹')
            self.assertTrue(self.items()[0]['add_sips_investments']['done'], code)

    def test_a_goal_ticks_the_goal_item(self):
        SavingsGoal.objects.create(user=self.user, name='Trip', target_amount=D('100'), currency='₹')
        self.assertTrue(self.items()[0]['set_savings_goal']['done'])

    def test_a_non_english_language_ticks_the_language_item(self):
        UserProfile.objects.filter(user=self.user).update(language='hi')
        self.assertTrue(self.items()[0]['switch_language']['done'])

    def test_the_checklist_action_returns_the_same_numbers(self):
        data = self.ok(action='checklist')
        _, done, total = self.items()
        self.assertEqual((data['done_count'], data['total_count']), (done, total))
