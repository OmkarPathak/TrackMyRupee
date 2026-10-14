"""Audit of settings, categories, onboarding, sign-in and account deletion: the destructive actions do
exactly what they say, the account email cannot be changed without proof, and the shared demo account
cannot be altered."""
import json
from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock, patch

import razorpay
from allauth.account.models import EmailAddress
from django.contrib.auth.models import User
from django.core import mail
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from expenses.models import (
    Account,
    CapitalEvent,
    Category,
    DeletionRequestAuditLog,
    EmailLog,
    Expense,
    ExpenseKeywordHint,
    Income,
    PaymentHistory,
    RecurringTransaction,
    SavingsGoal,
    UserProfile,
)
from expenses.views.settings import delete_user_account, dispatch_account_deletion

D = Decimal
TODAY = date.today()
LOCMEM = 'django.core.mail.backends.locmem.EmailBackend'


def make_user(name, tier='PLUS'):
    user = User.objects.create_user(username=name, email=f'{name}@example.com', password='pw')
    user.profile.tier = tier
    user.profile.has_seen_tutorial = True
    user.profile.save()
    EmailAddress.objects.update_or_create(user=user, email=user.email, defaults={'primary': True, 'verified': True})
    return user


@override_settings(EMAIL_BACKEND=LOCMEM, ACCOUNT_DELETION_ASYNC=False, RAZORPAY_KEY_ID='k', RAZORPAY_KEY_SECRET='s')
class AccountDeletionTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = make_user('gone')
        self.bank = Account.objects.create(user=self.user, name='Bank', balance=D('100'), currency='₹')
        Expense.objects.create(user=self.user, account=self.bank, amount=D('5'), date=TODAY, category='Food', currency='₹')
        Income.objects.create(user=self.user, account=self.bank, amount=D('50'), date=TODAY, source='Salary', currency='₹')
        SavingsGoal.objects.create(user=self.user, name='G', target_amount=D('9'), currency='₹')
        self.keeper = make_user('stays')
        Account.objects.create(user=self.keeper, name='Keep', balance=D('1'), currency='₹')
        mail.outbox.clear()

    def subscribe(self, latest_is_subscription=True):
        profile = self.user.profile
        profile.razorpay_subscription_id = 'sub_9'
        profile.save()
        PaymentHistory.objects.create(user=self.user, order_id='sub_9' if latest_is_subscription else 'order_1',
                                      payment_id='p', amount=49, tier='PLUS', duration='MONTHLY', status='SUCCESS')

    def test_the_users_data_goes_and_other_users_data_stays(self):
        uid = self.user.id
        self.assertTrue(delete_user_account(uid))
        self.assertFalse(User.objects.filter(pk=uid).exists())
        for model in (Account, Expense, Income, SavingsGoal, UserProfile):
            field = 'user_id' if model is not SavingsGoal else 'user_id'
            self.assertEqual(model.objects.filter(**{field: uid}).count(), 0, model.__name__)
        self.assertTrue(Account.objects.filter(user=self.keeper).exists())
        self.assertTrue(User.objects.filter(pk=self.keeper.pk).exists())

    def test_the_sent_email_history_is_removed_too_not_left_with_a_null_user(self):
        EmailLog.objects.create(user=self.user, to_email=self.user.email, subject='Hi', body='b', status='SENT')
        EmailLog.objects.create(user=None, to_email=self.user.email.upper(), subject='Old', body='b', status='SENT')
        EmailLog.objects.create(user=self.keeper, to_email=self.keeper.email, subject='Mine', body='b', status='SENT')
        delete_user_account(self.user.id)
        self.assertEqual(list(EmailLog.objects.values_list('to_email', flat=True)), [self.keeper.email])

    def test_it_records_that_a_deletion_happened_and_confirms_by_email(self):
        email = self.user.email
        delete_user_account(self.user.id)
        entry = DeletionRequestAuditLog.objects.get()
        self.assertEqual((entry.email, entry.username), (email, 'gone'))
        self.assertEqual([m.to for m in mail.outbox if 'Deleted' in m.subject], [[email]])

    def test_an_auto_renewing_subscription_is_cancelled_so_the_card_is_not_charged_again(self):
        self.subscribe()
        with patch('expenses.views.settings.razorpay.Client') as client:
            delete_user_account(self.user.id)
        client.return_value.subscription.cancel.assert_called_once_with('sub_9')
        self.assertFalse(User.objects.filter(pk=self.user.pk).exists())

    def test_payment_records_are_kept_when_the_account_goes_unlinked_from_the_person(self):
        PaymentHistory.objects.create(user=self.user, order_id='order_7', payment_id='pay_7', amount=499, tier='PLUS',
                                      duration='YEARLY', status='SUCCESS')
        email = self.user.email
        delete_user_account(self.user.id)
        record = PaymentHistory.objects.get(order_id='order_7')
        self.assertIsNone(record.user_id)
        self.assertEqual((record.user_email, record.user_username, record.amount, record.payment_id),
                         (email, 'gone', D('499.00'), 'pay_7'))
        self.assertIn('gone', str(record))

    def test_another_users_payment_records_are_untouched(self):
        PaymentHistory.objects.create(user=self.keeper, order_id='order_8', amount=49, tier='PLUS', status='SUCCESS')
        delete_user_account(self.user.id)
        kept = PaymentHistory.objects.get(order_id='order_8')
        self.assertEqual((kept.user_id, kept.user_email), (self.keeper.id, ''))

    def test_a_late_charge_for_a_deleted_accounts_subscription_is_acknowledged_not_a_server_error(self):
        from datetime import timedelta
        from django.utils import timezone
        PaymentHistory.objects.create(user=self.user, order_id='sub_late', payment_id='pay_a', amount=49, tier='PLUS',
                                      duration='MONTHLY', status='SUCCESS')
        delete_user_account(self.user.id)
        end = int((timezone.now() + timedelta(days=30)).timestamp())
        payload = {'event': 'subscription.charged', 'payload': {
            'subscription': {'entity': {'id': 'sub_late', 'current_end': end}},
            'payment': {'entity': {'id': 'pay_b', 'amount': 4900}}}}
        with patch('expenses.views_payment.razorpay.Client'), self.settings(RAZORPAY_WEBHOOK_SECRET='w'):
            r = self.client.post(reverse('razorpay-webhook'), json.dumps(payload), content_type='application/json',
                                 HTTP_X_RAZORPAY_SIGNATURE='sig')
        self.assertEqual(r.status_code, 200)

    def test_the_admin_list_still_opens_with_a_record_that_has_no_user(self):
        PaymentHistory.objects.create(user=self.user, order_id='order_9', amount=49, tier='PLUS', status='SUCCESS')
        delete_user_account(self.user.id)
        admin = User.objects.create_superuser(username='boss', email='boss@example.com', password='pw')
        self.client.force_login(admin)
        r = self.client.get('/tmr_admin/expenses/paymenthistory/')
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'gone@example.com')

    def test_a_one_time_payer_has_nothing_to_cancel(self):
        self.subscribe(latest_is_subscription=False)
        with patch('expenses.views.settings.razorpay.Client') as client:
            delete_user_account(self.user.id)
        client.return_value.subscription.cancel.assert_not_called()

    def test_a_free_user_never_contacts_the_payment_gateway(self):
        with patch('expenses.views.settings.razorpay.Client') as client:
            delete_user_account(self.user.id)
        client.assert_not_called()

    def test_an_already_cancelled_subscription_does_not_stop_the_deletion(self):
        self.subscribe()
        with patch('expenses.views.settings.razorpay.Client') as client:
            client.return_value.subscription.cancel.side_effect = razorpay.errors.BadRequestError('already cancelled')
            self.assertTrue(delete_user_account(self.user.id))

    def test_an_unreachable_gateway_is_logged_loudly_but_the_account_is_still_deleted(self):
        self.subscribe()
        with patch('expenses.views.settings.razorpay.Client') as client, self.assertLogs('expenses.views.settings', 'ERROR') as logs:
            client.return_value.subscription.cancel.side_effect = razorpay.errors.GatewayError('down')
            self.assertTrue(delete_user_account(self.user.id))
        self.assertIn('sub_9', ''.join(logs.output))
        self.assertFalse(User.objects.filter(pk=self.user.pk).exists())

    def test_a_failure_leaves_the_account_intact_and_sends_nothing(self):
        with patch('django.contrib.auth.models.User.delete', side_effect=RuntimeError('boom')):
            self.assertFalse(delete_user_account(self.user.id))
        self.assertTrue(User.objects.filter(pk=self.user.pk).exists())
        self.assertEqual(DeletionRequestAuditLog.objects.count(), 0)
        self.assertEqual([m for m in mail.outbox if 'Deleted' in m.subject], [])

    def test_a_missing_user_is_reported_not_raised(self):
        self.assertFalse(delete_user_account(999999))

    def test_two_deletions_cannot_run_at_once(self):
        cache.set(f'account_deletion_lock_{self.user.id}', 1, 600)
        self.assertFalse(dispatch_account_deletion(self.user.id, run_async=False))
        self.assertTrue(User.objects.filter(pk=self.user.pk).exists())

    def test_the_confirm_page_shows_first_and_only_a_post_deletes(self):
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(reverse('user-delete')).status_code, 200)
        self.assertTrue(User.objects.filter(pk=self.user.pk).exists())
        r = self.client.post(reverse('user-delete'))
        self.assertEqual(r.status_code, 302)
        self.assertFalse(User.objects.filter(pk=self.user.pk).exists())
        self.assertEqual(self.client.get(reverse('home')).status_code, 302)  # logged out

    def test_withdrawing_consent_deletes_the_account_the_same_way(self):
        self.client.force_login(self.user)
        self.client.post(reverse('withdraw-consent'))
        self.assertFalse(User.objects.filter(pk=self.user.pk).exists())

    def test_anonymous_visitors_are_sent_to_sign_in(self):
        self.assertEqual(self.client.post(reverse('user-delete')).status_code, 302)
        self.assertTrue(User.objects.filter(pk=self.user.pk).exists())

    def test_the_shared_demo_account_cannot_be_deleted(self):
        demo = User.objects.create_user(username='demo', email='demo@example.com', password='pw')
        self.client.force_login(demo)
        self.client.post(reverse('user-delete'))
        self.client.post(reverse('withdraw-consent'))
        self.assertTrue(User.objects.filter(pk=demo.pk).exists())


class DemoReadOnlyTests(TestCase):
    def setUp(self):
        self.demo = User.objects.create_user(username='demo', email='demo@example.com', password='pw')
        self.demo.profile.has_seen_tutorial = True
        self.demo.profile.save()
        self.client.force_login(self.demo)

    def post(self, **data):
        return self.client.post(reverse('onboarding'), json.dumps(data), content_type='application/json')

    def test_the_tour_cannot_create_accounts_or_income_for_the_demo_user(self):
        for step in ('step1', 'step2', 'step3_confirm', 'finish', 'skip_all', 'dismiss_checklist'):
            r = self.post(step=step, persona='SALARIED', payday=1, salary_amount='50000', balance_now='1000',
                          bank_choice='SBI', amount='100', description='x')
            self.assertEqual(r.status_code, 200)
        self.assertEqual(Account.objects.filter(user=self.demo).count(), 0)
        self.assertEqual(Income.objects.filter(user=self.demo).count(), 0)
        profile = UserProfile.objects.get(user=self.demo)
        self.assertEqual(profile.salary_date, 1)
        self.assertFalse(profile.dismissed_onboarding_checklist)

    def test_the_tours_harmless_calls_still_work(self):
        self.assertEqual(self.post(step='cycle_range', payday=5).json()['success'], True)
        self.assertEqual(self.post(step='checklist').json()['success'], True)

    def test_other_changes_are_refused(self):
        self.client.post(reverse('category-create'), {'name': 'Hacked'})
        self.client.post(reverse('currency-settings'), {'currency': '$'})
        self.assertFalse(Category.objects.filter(user=self.demo, name='Hacked').exists())
        self.assertNotEqual(UserProfile.objects.get(user=self.demo).currency, '$')


@override_settings(EMAIL_BACKEND=LOCMEM)
class ProfileEmailChangeTests(TestCase):
    def setUp(self):
        self.user = make_user('mail')
        self.client.force_login(self.user)
        mail.outbox.clear()

    def post(self, email):
        return self.client.post(reverse('profile-settings'), {
            'auth_email': email, 'first_name': 'A', 'last_name': 'B', 'salary_date': '5', 'daily_reminder': 'on'})

    def test_a_new_address_is_pending_until_it_is_confirmed(self):
        self.assertEqual(self.post('new@example.com').status_code, 302)
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, 'mail@example.com')
        pending = EmailAddress.objects.get(user=self.user, email='new@example.com')
        self.assertFalse(pending.verified)
        self.assertEqual([m.to for m in mail.outbox], [['new@example.com']])

    def test_confirming_the_link_switches_the_account_email(self):
        import re
        self.post('new@example.com')
        key = re.search(r'/confirm-email/([^/\s"<]+)/', mail.outbox[-1].body).group(1)
        self.client.post(reverse('account_confirm_email', args=[key]))
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, 'new@example.com')
        self.assertTrue(EmailAddress.objects.get(email='new@example.com').verified)

    def test_the_other_profile_fields_still_save_and_an_unchanged_email_sends_nothing(self):
        self.post('mail@example.com')
        self.user.refresh_from_db()
        self.user.profile.refresh_from_db()
        self.assertEqual((self.user.first_name, self.user.profile.salary_date), ('A', 5))
        self.assertEqual(mail.outbox, [])

    def test_another_accounts_address_cannot_be_claimed_in_any_letter_case(self):
        other = make_user('other')
        for typed in (other.email, other.email.upper()):
            r = self.post(typed)
            self.assertEqual(r.status_code, 200)
            self.assertContains(r, 'already assigned')
        self.assertFalse(EmailAddress.objects.filter(user=self.user, email__iexact=other.email).exists())

    def test_an_address_pending_on_another_account_cannot_be_claimed_either(self):
        other = make_user('other2')
        EmailAddress.objects.create(user=other, email='pending@example.com', verified=False)
        r = self.post('pending@example.com')
        self.assertContains(r, 'already assigned')

    def test_a_malformed_address_is_refused(self):
        self.assertEqual(self.post('not-an-email').status_code, 200)
        self.assertEqual(EmailAddress.objects.filter(user=self.user).count(), 1)


class CurrencyAndLanguageSettingsTests(TestCase):
    def setUp(self):
        self.user = make_user('cur')
        self.client.force_login(self.user)

    @patch('expenses.views.settings.dispatch_currency_recalculation')
    def test_saving_the_same_currency_recalculates_nothing(self, dispatch):
        self.client.post(reverse('currency-settings'), {'currency': self.user.profile.currency})
        dispatch.assert_not_called()

    def test_an_unknown_currency_is_refused(self):
        self.client.post(reverse('currency-settings'), {'currency': 'ZZZ'})
        self.user.profile.refresh_from_db()
        self.assertNotEqual(self.user.profile.currency, 'ZZZ')

    def test_an_unknown_language_is_refused(self):
        self.client.post(reverse('language-settings'), {'language': 'xx'})
        self.user.profile.refresh_from_db()
        self.assertNotEqual(self.user.profile.language, 'xx')

    def test_the_settings_pages_need_sign_in(self):
        self.client.logout()
        for name in ('currency-settings', 'language-settings', 'profile-settings', 'user-delete', 'export-data'):
            self.assertEqual(self.client.get(reverse(name)).status_code, 302, name)


class CategoryRenameTests(TestCase):
    def setUp(self):
        self.user = make_user('cat')
        self.client.force_login(self.user)
        self.food = Category.objects.get_or_create(user=self.user, name='Food')[0]
        self.bank = Account.objects.create(user=self.user, name='Bank', balance=D('1000'), currency='₹')

    def rename(self, new):
        return self.client.post(reverse('category-edit', args=[self.food.uuid]), {'name': new, 'icon': 'bi-tag', 'limit': ''})

    def test_everything_filed_under_the_old_name_follows_the_rename_in_any_letter_case(self):
        Expense.objects.create(user=self.user, account=self.bank, amount=D('1'), date=TODAY, category='Food', currency='₹')
        legacy = Expense.objects.create(user=self.user, account=self.bank, amount=D('1'), date=TODAY, category='Food', currency='₹')
        Expense.objects.filter(pk=legacy.pk).update(category='food')
        RecurringTransaction.objects.create(user=self.user, description='Meal', amount=D('9'), frequency='MONTHLY',
                                            transaction_type='EXPENSE', start_date=TODAY, next_due_date=TODAY,
                                            account=self.bank, currency='₹', category='Food')
        CapitalEvent.objects.create(user=self.user, account=self.bank, date=TODAY, amount=D('5'), category='Food')
        ExpenseKeywordHint.objects.create(user=self.user, keyword='swiggy', category='Food')
        self.rename('Meals')
        self.assertEqual(set(Expense.objects.filter(user=self.user).values_list('category', flat=True)), {'Meals'})
        self.assertEqual(RecurringTransaction.objects.get().category, 'Meals')
        self.assertEqual(CapitalEvent.objects.get().category, 'Meals')
        self.assertEqual(ExpenseKeywordHint.objects.get().category, 'Meals')

    def test_another_users_expenses_are_never_touched(self):
        other = make_user('cat2')
        acc = Account.objects.create(user=other, name='B', balance=D('1'), currency='₹')
        Expense.objects.create(user=other, account=acc, amount=D('1'), date=TODAY, category='Food', currency='₹')
        self.rename('Meals')
        self.assertEqual(Expense.objects.get(user=other).category, 'Food')

    def test_a_rename_onto_an_existing_name_is_refused_in_any_letter_case(self):
        Category.objects.create(user=self.user, name='Travel')
        r = self.rename('TRAVEL')
        self.assertEqual(r.status_code, 200)
        self.food.refresh_from_db()
        self.assertEqual(self.food.name, 'Food')

    def test_a_negative_budget_is_refused_and_zero_means_none(self):
        r = self.client.post(reverse('category-edit', args=[self.food.uuid]), {'name': 'Food', 'icon': 'bi-tag', 'limit': '-5'})
        self.assertEqual(r.status_code, 200)
        self.client.post(reverse('category-edit', args=[self.food.uuid]), {'name': 'Food', 'icon': 'bi-tag', 'limit': '0'})
        self.food.refresh_from_db()
        self.assertIsNone(self.food.limit)

    def test_deleting_a_category_keeps_the_expenses_filed_under_it(self):
        Expense.objects.create(user=self.user, account=self.bank, amount=D('1'), date=TODAY, category='Food', currency='₹')
        self.client.post(reverse('category-delete', args=[self.food.uuid]))
        self.assertFalse(Category.objects.filter(pk=self.food.pk).exists())
        self.assertEqual(Expense.objects.filter(user=self.user, category='Food').count(), 1)

    def test_bulk_delete_only_removes_my_categories(self):
        other = make_user('cat3')
        theirs = Category.objects.create(user=other, name='Theirs')
        mine = Category.objects.create(user=self.user, name='Mine')
        self.client.post(reverse('category-bulk-delete'), {'category_ids': [theirs.id, mine.id, 'x', '']})
        self.assertTrue(Category.objects.filter(pk=theirs.pk).exists())
        self.assertFalse(Category.objects.filter(pk=mine.pk).exists())

    def test_another_users_category_cannot_be_opened_edited_or_deleted(self):
        other = make_user('cat4')
        theirs = Category.objects.create(user=other, name='Theirs')
        for name in ('category-edit', 'category-delete'):
            self.assertEqual(self.client.get(reverse(name, args=[theirs.uuid])).status_code, 404, name)

    def test_a_free_account_cannot_edit_a_category_beyond_its_limit(self):
        free = make_user('freecat', tier='FREE')
        self.client.force_login(free)
        from finance_tracker.plans import get_limit
        for i in range(get_limit('FREE', 'budget_categories') + 1):
            Category.objects.get_or_create(user=free, name=f'C{i}')
        last = Category.objects.filter(user=free).order_by('id').last()
        r = self.client.get(reverse('category-edit', args=[last.uuid]))
        self.assertRedirects(r, reverse('category-list'), fetch_redirect_response=False)


class OnboardingInputTests(TestCase):
    def setUp(self):
        self.user = make_user('onb')
        self.user.profile.has_seen_tutorial = False
        self.user.profile.save()
        self.client.force_login(self.user)
        self.stranger = make_user('onb2')
        self.theirs = Account.objects.create(user=self.stranger, name='Theirs', balance=D('5'), currency='₹')

    def post(self, **data):
        return self.client.post(reverse('onboarding'), json.dumps(data), content_type='application/json')

    def test_the_typed_balance_is_the_balance_shown_even_with_salary_logged(self):
        from expenses.ledger_read_service import LedgerReadService
        self.post(step='step1', persona='SALARIED')
        r = self.post(step='step2', payday=1, salary_amount='50000', balance_now='120000', bank_choice='SBI',
                      auto_log_salary=True)
        self.assertTrue(r.json()['success'])
        account = Account.objects.get(user=self.user)
        self.assertEqual(LedgerReadService.get_account_balance(account), D('120000.00'))

    def test_going_back_and_resubmitting_updates_instead_of_duplicating(self):
        for amount in ('50000', '60000'):
            self.post(step='step2', payday=1, salary_amount=amount, balance_now='1000', bank_choice='SBI',
                      auto_log_salary=True)
        self.assertEqual(Account.objects.filter(user=self.user).count(), 1)
        self.assertEqual(Income.objects.filter(user=self.user).count(), 1)
        self.assertEqual(RecurringTransaction.objects.filter(user=self.user).count(), 1)
        self.assertEqual(Income.objects.get(user=self.user).amount, D('60000.00'))

    def test_nonsense_numbers_never_create_data_or_crash(self):
        for bad in ('NaN', 'Infinity', '-5', '1e400', 'abc', '<script>'):
            r = self.post(step='step3_confirm', amount=bad, description='x')
            self.assertIn(r.status_code, (200, 400), bad)
        self.assertEqual(Expense.objects.filter(user=self.user).count(), 0)

    def test_an_expense_cannot_be_put_on_someone_elses_account(self):
        self.post(step='step3_confirm', amount='50', description='x', account_id=self.theirs.id)
        self.assertEqual(Expense.objects.filter(account=self.theirs).count(), 0)
        self.assertEqual(Account.objects.get(pk=self.theirs.pk).balance, D('5.00'))

    def test_a_very_long_bank_name_is_cut_to_fit(self):
        self.post(step='step2', payday=1, bank_choice='X' * 400, balance_now='1')
        self.assertLessEqual(len(Account.objects.get(user=self.user).name), 100)
        self.post(step='step2', payday=1, bank_choice='OTHER', custom_bank_name='Y' * 400, balance_now='1')
        self.assertTrue(all(len(n) <= 100 for n in Account.objects.filter(user=self.user).values_list('name', flat=True)))

    def test_an_unknown_action_is_refused_and_a_broken_body_is_not_a_crash(self):
        self.assertEqual(self.post(step='nonsense').status_code, 400)
        r = self.client.post(reverse('onboarding'), 'not json', content_type='application/json')
        self.assertEqual(r.status_code, 400)

    def test_finishing_marks_the_tutorial_seen_and_the_page_then_redirects(self):
        self.post(step='finish')
        self.user.profile.refresh_from_db()
        self.assertTrue(self.user.profile.has_seen_tutorial)
        self.assertEqual(self.client.get(reverse('onboarding')).status_code, 302)

    def test_it_needs_sign_in(self):
        self.client.logout()
        self.assertEqual(self.client.post(reverse('onboarding'), '{}', content_type='application/json').status_code, 302)
