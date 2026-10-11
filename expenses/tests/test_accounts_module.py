"""Accounts: create/edit/delete/restore/pin, plan locks, transfers, the list and the detail ledger."""

import datetime
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.contrib.messages import get_messages
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from expenses.forms import AccountForm, TransferForm
from expenses.models import (
    Account,
    Category,
    Expense,
    FXRate,
    GoalContribution,
    Income,
    SavingsGoal,
    Transfer,
    UserProfile,
)

D = Decimal


def today():
    return timezone.localdate()


class AccountBase(TestCase):
    tier = 'PRO'

    def setUp(self):
        self.user = self.make_user('acct-user', self.tier)
        self.client.force_login(self.user)
        Account.objects.filter(user=self.user).delete()
        Category.objects.get_or_create(user=self.user, name='Food')
        cache.clear()

    def make_user(self, name, tier='PRO'):
        user = User.objects.create_user(username=name, password='pass')
        profile, _ = UserProfile.objects.get_or_create(user=user, defaults={'currency': '₹'})
        profile.tier = tier
        profile.currency = '₹'
        profile.has_seen_tutorial = True
        profile.save()
        user.refresh_from_db()
        return user

    def acct(self, name='Bank', balance='1000', account_type='SAVINGS_ACCOUNT', currency='₹', **kw):
        return Account.objects.create(user=self.user, name=name, account_type=account_type,
                                      balance=D(str(balance)), currency=currency, **kw)

    def bal(self, account):
        account.refresh_from_db()
        return account.balance

    def seed_fx(self, usd_inr='80'):
        for (src, dst), rate in {('USD', 'INR'): usd_inr, ('INR', 'USD'): str(D('1') / D(usd_inr)),
                                 ('$', '₹'): usd_inr, ('₹', '$'): str(D('1') / D(usd_inr))}.items():
            FXRate.objects.update_or_create(from_currency=src, to_currency=dst, as_of_date=today(),
                                            defaults={'rate': D(rate), 'source': 'test'})
        cache.clear()

    def form_data(self, **kw):
        data = {'name': 'HDFC', 'account_type': 'SAVINGS_ACCOUNT', 'balance': '500', 'currency': '₹'}
        data.update(kw)
        return data

    def messages(self, response):
        return [str(m) for m in get_messages(response.wsgi_request)]


class TestAccountForm(AccountBase):
    def form(self, **kw):
        return AccountForm(self.form_data(**kw), user=self.user)

    def test_a_plain_account_is_valid(self):
        self.assertTrue(self.form().is_valid())

    def test_name_is_unique_ignoring_case_among_active_accounts(self):
        self.acct('HDFC')
        form = self.form(name='hdfc')
        self.assertFalse(form.is_valid())
        self.assertIn('name', form.errors)

    def test_a_deleted_accounts_name_can_be_reused(self):
        self.acct('HDFC', is_active=False)
        self.assertTrue(self.form(name='HDFC').is_valid())

    def test_editing_keeps_its_own_name(self):
        account = self.acct('HDFC')
        form = AccountForm(self.form_data(name='HDFC'), instance=account, user=self.user)
        self.assertTrue(form.is_valid())

    def test_a_credit_limit_cannot_be_negative(self):
        form = self.form(account_type='CREDIT_CARD', credit_limit='-5000')
        self.assertFalse(form.is_valid())
        self.assertIn('credit_limit', form.errors)

    def test_a_deposit_needs_its_terms(self):
        form = self.form(account_type='FD')
        self.assertFalse(form.is_valid())
        for field in ('deposit_principal', 'deposit_rate', 'deposit_start_date'):
            self.assertIn(field, form.errors)

    def test_a_deposit_rate_must_be_a_sane_percentage(self):
        form = self.form(account_type='FD', deposit_principal='1000', deposit_rate='900',
                         deposit_start_date='2025-01-01')
        self.assertFalse(form.is_valid())
        self.assertIn('deposit_rate', form.errors)

    def test_a_negative_deposit_rate_is_refused(self):
        form = self.form(account_type='FD', deposit_principal='1000', deposit_rate='-3',
                         deposit_start_date='2025-01-01')
        self.assertFalse(form.is_valid())
        self.assertIn('deposit_rate', form.errors)

    def test_a_deposit_principal_cannot_be_negative(self):
        form = self.form(account_type='FD', deposit_principal='-1000', deposit_rate='7',
                         deposit_start_date='2025-01-01')
        self.assertFalse(form.is_valid())
        self.assertIn('deposit_principal', form.errors)

    def test_maturity_must_be_after_start(self):
        form = self.form(account_type='FD', deposit_principal='1000', deposit_rate='7',
                         deposit_start_date='2025-06-01', deposit_maturity_date='2025-05-01')
        self.assertFalse(form.is_valid())
        self.assertIn('deposit_maturity_date', form.errors)

    def test_a_recurring_deposit_needs_an_installment_and_a_day(self):
        form = self.form(account_type='RD', deposit_principal='0', deposit_rate='7',
                         deposit_start_date='2025-01-01')
        self.assertFalse(form.is_valid())
        self.assertIn('rd_installment_amount', form.errors)
        self.assertIn('rd_installment_day', form.errors)

    def test_a_loan_account_needs_a_linked_loan(self):
        form = self.form(account_type='HOME_LOAN')
        self.assertFalse(form.is_valid())
        self.assertIn('linked_loan', form.errors)

    def test_fields_of_another_type_are_dropped(self):
        form = self.form(credit_limit='5000')
        self.assertTrue(form.is_valid())
        self.assertIsNone(form.cleaned_data['credit_limit'])


class TestAccountCreate(AccountBase):
    def test_create_saves_the_account_for_the_user(self):
        response = self.client.post(reverse('account-create'), self.form_data(balance='1234.50'))
        self.assertRedirects(response, reverse('account-list'))
        account = Account.objects.get(name='HDFC')
        self.assertEqual((account.user, account.balance), (self.user, D('1234.50')))

    def test_create_refuses_a_duplicate_name_with_a_field_error(self):
        self.acct('HDFC')
        response = self.client.post(reverse('account-create'), self.form_data())
        self.assertEqual(response.status_code, 200)
        self.assertIn('name', response.context['form'].errors)
        self.assertEqual(Account.objects.filter(user=self.user).count(), 1)

    def test_the_plan_limit_stops_creation_and_points_to_pricing(self):
        user = self.make_user('free-user', 'FREE')
        self.client.force_login(user)
        for i in range(2):
            Account.objects.create(user=user, name=f'A{i}', balance=0)
        response = self.client.post(reverse('account-create'), self.form_data(name='Third'))
        self.assertRedirects(response, reverse('pricing'), fetch_redirect_response=False)
        self.assertFalse(Account.objects.filter(name='Third').exists())

    def test_deleted_accounts_do_not_use_up_the_limit(self):
        user = self.make_user('free-user', 'FREE')
        self.client.force_login(user)
        Account.objects.create(user=user, name='Gone', balance=0, is_active=False)
        Account.objects.create(user=user, name='Live', balance=0)
        response = self.client.post(reverse('account-create'), self.form_data(name='Second'))
        self.assertRedirects(response, reverse('account-list'))

    def test_a_credit_card_keeps_its_limit_and_billing_day(self):
        self.client.post(reverse('account-create'), self.form_data(
            name='Card', account_type='CREDIT_CARD', balance='0', credit_limit='50000',
            credit_card_billing_day='15'))
        card = Account.objects.get(name='Card')
        self.assertEqual((card.credit_limit, card.credit_card_billing_day), (D('50000.00'), 15))

    def test_login_is_required(self):
        self.client.logout()
        response = self.client.post(reverse('account-create'), self.form_data())
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Account.objects.filter(name='HDFC').exists())


class TestAccountQuickCreate(AccountBase):
    def test_returns_the_new_account_as_json(self):
        response = self.client.post(reverse('account-quick-create'), self.form_data())
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data['success'])
        self.assertTrue(Account.objects.filter(pk=data['id'], user=self.user).exists())

    def test_errors_come_back_as_json(self):
        self.acct('HDFC')
        response = self.client.post(reverse('account-quick-create'), self.form_data())
        self.assertEqual(response.status_code, 400)
        self.assertIn('name', response.json()['errors'])

    def test_the_plan_limit_is_enforced(self):
        user = self.make_user('free-user', 'FREE')
        self.client.force_login(user)
        for i in range(2):
            Account.objects.create(user=user, name=f'A{i}', balance=0)
        response = self.client.post(reverse('account-quick-create'), self.form_data(name='Third'))
        self.assertEqual(response.status_code, 403)
        self.assertFalse(Account.objects.filter(name='Third').exists())


class TestAccountEdit(AccountBase):
    def test_edit_changes_the_fields(self):
        account = self.acct('HDFC', 500)
        response = self.client.post(reverse('account-edit', args=[account.pk]),
                                    self.form_data(name='HDFC Salary', balance='900'))
        self.assertRedirects(response, reverse('account-list'))
        account.refresh_from_db()
        self.assertEqual((account.name, account.balance), ('HDFC Salary', D('900.00')))

    def test_another_users_account_is_not_found(self):
        other = self.make_user('someone-else')
        account = Account.objects.create(user=other, name='Theirs', balance=5)
        response = self.client.post(reverse('account-edit', args=[account.pk]), self.form_data())
        self.assertEqual(response.status_code, 404)

    def test_a_locked_account_cannot_be_edited(self):
        user = self.make_user('free-user', 'FREE')
        self.client.force_login(user)
        for i in range(3):
            Account.objects.create(user=user, name=f'A{i}', balance=0)
        locked = Account.objects.get(user=user, name='A2')
        response = self.client.post(reverse('account-edit', args=[locked.pk]), self.form_data(name='New'))
        self.assertRedirects(response, reverse('pricing'), fetch_redirect_response=False)
        locked.refresh_from_db()
        self.assertEqual(locked.name, 'A2')

    def test_the_currency_is_locked_once_the_account_has_transactions(self):
        account = self.acct('HDFC', 500)
        Expense.objects.create(user=self.user, account=account, amount=D('100'), category='Food',
                               date=today(), currency='₹')
        response = self.client.post(reverse('account-edit', args=[account.pk]),
                                    self.form_data(balance='400', currency='$'))
        self.assertRedirects(response, reverse('account-list'))
        account.refresh_from_db()
        self.assertEqual((account.currency, account.balance), ('₹', D('400.00')))

    def test_any_kind_of_activity_locks_the_currency(self):
        other = self.acct('Other', 0)
        for make in (
            lambda a: Income.objects.create(user=self.user, account=a, amount=D('1'), source_type='Salary',
                                            date=today(), currency='₹'),
            lambda a: Transfer.objects.create(user=self.user, from_account=a, to_account=other,
                                              amount=D('1'), date=today()),
            lambda a: Transfer.objects.create(user=self.user, from_account=other, to_account=a,
                                              amount=D('1'), date=today()),
        ):
            account = self.acct(f'Acc{Account.objects.count()}', 100)
            make(account)
            self.assertTrue(AccountForm(instance=account, user=self.user).fields['currency'].disabled)

    def test_the_currency_can_change_while_the_account_is_unused(self):
        account = self.acct('HDFC', 500)
        response = self.client.post(reverse('account-edit', args=[account.pk]),
                                    self.form_data(currency='$'))
        self.assertRedirects(response, reverse('account-list'))
        account.refresh_from_db()
        self.assertEqual(account.currency, '$')


class TestAccountDeleteRestorePin(AccountBase):
    def test_delete_deactivates_and_keeps_the_history(self):
        account = self.acct('HDFC', 500)
        expense = Expense.objects.create(user=self.user, account=account, amount=D('100'),
                                         category='Food', date=today(), currency='₹')
        response = self.client.post(reverse('account-delete', args=[account.pk]))
        self.assertRedirects(response, reverse('account-list'))
        account.refresh_from_db()
        expense.refresh_from_db()
        self.assertFalse(account.is_active)
        self.assertEqual(expense.account_id, account.pk)
        self.assertIn('Account deleted successfully.', self.messages(response))

    def test_a_deleted_account_leaves_the_active_list_and_shows_under_inactive(self):
        account = self.acct('HDFC', 500)
        self.client.post(reverse('account-delete', args=[account.pk]))
        active = self.client.get(reverse('account-list'))
        self.assertNotContains(active, 'HDFC')
        inactive = self.client.get(reverse('account-list'), {'status': 'inactive'})
        self.assertContains(inactive, 'HDFC')

    def test_a_deleted_account_cannot_be_deleted_again(self):
        account = self.acct('HDFC', is_active=False)
        response = self.client.post(reverse('account-delete', args=[account.pk]))
        self.assertEqual(response.status_code, 404)

    def test_restore_brings_it_back(self):
        account = self.acct('HDFC', is_active=False)
        response = self.client.get(reverse('account-restore', args=[account.pk]))
        self.assertRedirects(response, reverse('account-list'))
        account.refresh_from_db()
        self.assertTrue(account.is_active)

    def test_restore_is_refused_at_the_plan_limit(self):
        user = self.make_user('free-user', 'FREE')
        self.client.force_login(user)
        for i in range(2):
            Account.objects.create(user=user, name=f'A{i}', balance=0)
        old = Account.objects.create(user=user, name='Old', balance=0, is_active=False)
        response = self.client.get(reverse('account-restore', args=[old.pk]))
        self.assertRedirects(response, reverse('pricing'), fetch_redirect_response=False)
        old.refresh_from_db()
        self.assertFalse(old.is_active)

    def test_restore_does_not_crash_when_the_name_is_in_use_again(self):
        old = self.acct('HDFC', is_active=False)
        self.acct('HDFC')
        response = self.client.get(reverse('account-restore', args=[old.pk]))
        self.assertEqual(response.status_code, 302)
        old.refresh_from_db()
        self.assertFalse(old.is_active)
        self.assertTrue(any('already' in m for m in self.messages(response)))

    def test_restore_of_another_users_account_is_not_found(self):
        other = self.make_user('someone-else')
        account = Account.objects.create(user=other, name='Theirs', balance=0, is_active=False)
        response = self.client.get(reverse('account-restore', args=[account.pk]))
        self.assertEqual(response.status_code, 404)

    def test_pin_toggles_both_ways(self):
        account = self.acct('HDFC')
        self.client.post(reverse('account-toggle-pin', args=[account.pk]))
        account.refresh_from_db()
        self.assertTrue(account.is_pinned)
        self.client.post(reverse('account-toggle-pin', args=[account.pk]))
        account.refresh_from_db()
        self.assertFalse(account.is_pinned)

    def test_pin_over_htmx_returns_the_list(self):
        account = self.acct('HDFC')
        response = self.client.post(reverse('account-toggle-pin', args=[account.pk]), HTTP_HX_REQUEST='true')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'HDFC')

    def test_pin_of_another_users_account_is_not_found(self):
        other = self.make_user('someone-else')
        account = Account.objects.create(user=other, name='Theirs', balance=0)
        response = self.client.post(reverse('account-toggle-pin', args=[account.pk]))
        self.assertEqual(response.status_code, 404)
        account.refresh_from_db()
        self.assertFalse(account.is_pinned)


class TestPlanLocks(AccountBase):
    tier = 'FREE'

    def setUp(self):
        super().setUp()
        self.oldest = self.acct('Oldest')
        self.second = self.acct('Second')
        self.third = self.acct('Third')

    def test_the_oldest_accounts_within_the_limit_stay_open(self):
        profile = self.user.profile
        self.assertFalse(profile.is_account_locked(self.oldest))
        self.assertFalse(profile.is_account_locked(self.second))
        self.assertTrue(profile.is_account_locked(self.third))

    def test_the_list_marks_the_same_account_as_locked(self):
        response = self.client.get(reverse('account-list'))
        locked = {a.name for a in response.context['accounts'] if a.is_locked}
        self.assertEqual(locked, {'Third'})

    def test_pinning_a_newer_account_does_not_lock_an_older_one(self):
        Account.objects.filter(pk=self.third.pk).update(is_pinned=True)
        response = self.client.get(reverse('account-list'))
        locked = {a.name for a in response.context['accounts'] if a.is_locked}
        self.assertEqual(locked, {'Third'})

    def test_searching_does_not_move_the_lock(self):
        response = self.client.get(reverse('account-list'), {'search': 'Third'})
        flags = {a.name: a.is_locked for a in response.context['accounts']}
        self.assertEqual(flags, {'Third': True})
        response = self.client.get(reverse('account-list'), {'search': 'Second'})
        flags = {a.name: a.is_locked for a in response.context['accounts']}
        self.assertEqual(flags, {'Second': False})

    def test_deleted_accounts_are_never_shown_as_locked(self):
        Account.objects.filter(pk=self.oldest.pk).update(is_active=False)
        response = self.client.get(reverse('account-list'), {'status': 'inactive'})
        self.assertFalse(any(a.is_locked for a in response.context['accounts']))

    def test_a_locked_account_cannot_be_deleted_or_opened(self):
        response = self.client.post(reverse('account-delete', args=[self.third.pk]))
        self.assertRedirects(response, reverse('pricing'), fetch_redirect_response=False)
        response = self.client.get(reverse('account-detail', args=[self.third.pk]))
        self.assertRedirects(response, reverse('pricing'), fetch_redirect_response=False)

    def test_transfers_only_offer_the_open_accounts(self):
        form = TransferForm(user=self.user)
        offered = set(form.fields['from_account'].queryset.values_list('name', flat=True))
        self.assertEqual(offered, {'Oldest', 'Second'})


class TestTransferMaths(AccountBase):
    def setUp(self):
        super().setUp()
        self.a = self.acct('A', 1000)
        self.b = self.acct('B', 200)

    def transfer(self, amount, **kw):
        values = dict(user=self.user, from_account=self.a, to_account=self.b, amount=D(str(amount)), date=today())
        values.update(kw)
        return Transfer.objects.create(**values)

    def test_a_transfer_moves_the_money(self):
        self.transfer(300)
        self.assertEqual((self.bal(self.a), self.bal(self.b)), (D('700.00'), D('500.00')))

    def test_editing_the_amount_moves_only_the_difference(self):
        t = self.transfer(300)
        t.amount = D('500')
        t.save()
        self.assertEqual((self.bal(self.a), self.bal(self.b)), (D('500.00'), D('700.00')))

    def test_editing_the_accounts_moves_the_money_back_and_forth(self):
        c = self.acct('C', 0)
        t = self.transfer(300)
        t.to_account = c
        t.save()
        self.assertEqual((self.bal(self.a), self.bal(self.b), self.bal(c)),
                         (D('700.00'), D('200.00'), D('300.00')))

    def test_delete_gives_it_back(self):
        t = self.transfer(300)
        t.delete()
        self.assertEqual((self.bal(self.a), self.bal(self.b)), (D('1000.00'), D('200.00')))
        self.assertFalse(Transfer.objects.filter(pk=t.pk).exists())

    def test_a_card_can_go_further_into_debt(self):
        card = self.acct('Card', -500, account_type='CREDIT_CARD')
        self.transfer(1500, from_account=card, to_account=self.b)
        self.assertEqual(self.bal(card), D('-2000.00'))

    def test_same_account_is_refused(self):
        from django.core.exceptions import ValidationError
        with self.assertRaises(ValidationError):
            self.transfer(10, to_account=self.a)

    def test_another_users_account_is_refused(self):
        from django.core.exceptions import ValidationError
        other = Account.objects.create(user=self.make_user('someone-else'), name='Theirs', balance=0)
        with self.assertRaises(ValidationError):
            self.transfer(10, to_account=other)

    def test_a_cross_currency_transfer_converts_for_the_receiver(self):
        self.seed_fx('80')
        usd = self.acct('Dollars', 100, currency='$')
        self.transfer(8000, to_account=usd, from_account=self.acct('Rupees', 20000))
        self.assertEqual(self.bal(usd), D('200.00'))

    def test_the_base_amount_is_in_the_users_currency(self):
        self.seed_fx('80')
        usd = self.acct('Dollars', 100, currency='$')
        t = self.transfer(10, from_account=usd, to_account=self.a)
        self.assertEqual(t.converted_amount, D('800.00'))

    def test_a_transfer_between_currencies_nets_to_nothing_when_deleted(self):
        self.seed_fx('80')
        usd = self.acct('Dollars', 100, currency='$')
        rupees = self.acct('Rupees', 20000)
        t = self.transfer(8000, to_account=usd, from_account=rupees)
        t.delete()
        self.assertEqual((self.bal(usd), self.bal(rupees)), (D('100.00'), D('20000.00')))


class TestTransferViews(AccountBase):
    def setUp(self):
        super().setUp()
        self.a = self.acct('A', 1000)
        self.b = self.acct('B', 200)

    def post_data(self, **kw):
        data = {'date': today().isoformat(), 'amount': '300', 'from_account': self.a.pk,
                'to_account': self.b.pk, 'description': 'rent'}
        data.update(kw)
        return data

    def test_create_moves_the_money_and_redirects_to_the_list(self):
        response = self.client.post(reverse('transfer-create'), self.post_data())
        self.assertRedirects(response, reverse('transfer-list'))
        self.assertEqual((self.bal(self.a), self.bal(self.b)), (D('700.00'), D('500.00')))
        self.assertIn('Transfer completed successfully!', self.messages(response))

    def test_zero_and_negative_amounts_are_refused(self):
        for amount in ('0', '-50'):
            response = self.client.post(reverse('transfer-create'), self.post_data(amount=amount))
            self.assertEqual(response.status_code, 200)
        self.assertEqual(Transfer.objects.count(), 0)
        self.assertEqual(self.bal(self.a), D('1000.00'))

    def test_the_same_account_on_both_sides_is_refused(self):
        response = self.client.post(reverse('transfer-create'), self.post_data(to_account=self.a.pk))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Transfer.objects.count(), 0)

    def test_another_users_account_cannot_be_used(self):
        other = Account.objects.create(user=self.make_user('someone-else'), name='Theirs', balance=0)
        response = self.client.post(reverse('transfer-create'), self.post_data(to_account=other.pk))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Transfer.objects.count(), 0)

    def test_a_deleted_account_cannot_be_used(self):
        Account.objects.filter(pk=self.b.pk).update(is_active=False)
        response = self.client.post(reverse('transfer-create'), self.post_data())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Transfer.objects.count(), 0)

    def test_a_double_submit_creates_one_transfer(self):
        data = self.post_data(client_dedup_key='abc-123')
        self.client.post(reverse('transfer-create'), data)
        self.client.post(reverse('transfer-create'), data)
        self.assertEqual(Transfer.objects.count(), 1)
        self.assertEqual(self.bal(self.a), D('700.00'))

    def test_next_must_stay_on_this_site_after_create(self):
        response = self.client.post(reverse('transfer-create') + '?next=https://evil.example/x', self.post_data())
        self.assertEqual(response.status_code, 302)
        self.assertNotIn('evil.example', response['Location'])

    def test_next_must_stay_on_this_site_after_edit(self):
        t = Transfer.objects.create(user=self.user, from_account=self.a, to_account=self.b,
                                    amount=D('10'), date=today())
        response = self.client.post(reverse('transfer-edit', args=[t.pk]) + '?next=//evil.example/x',
                                    self.post_data(amount='20'))
        self.assertEqual(response.status_code, 302)
        self.assertNotIn('evil.example', response['Location'])

    def test_a_local_next_is_followed(self):
        response = self.client.post(reverse('transfer-create') + '?next=/accounts/list/', self.post_data())
        self.assertRedirects(response, '/accounts/list/')

    def test_delete_restores_the_balances_and_reports(self):
        t = Transfer.objects.create(user=self.user, from_account=self.a, to_account=self.b,
                                    amount=D('300'), date=today())
        response = self.client.post(reverse('transfer-delete', args=[t.pk]))
        self.assertRedirects(response, reverse('transfer-list'))
        self.assertEqual((self.bal(self.a), self.bal(self.b)), (D('1000.00'), D('200.00')))
        self.assertIn('Transfer deleted successfully!', self.messages(response))

    def test_delete_ignores_an_outside_next(self):
        t = Transfer.objects.create(user=self.user, from_account=self.a, to_account=self.b,
                                    amount=D('300'), date=today())
        response = self.client.post(reverse('transfer-delete', args=[t.pk]), {'next': 'https://evil.example'})
        self.assertNotIn('evil.example', response['Location'])

    def test_another_users_transfer_cannot_be_edited_or_deleted(self):
        other = self.make_user('someone-else')
        x = Account.objects.create(user=other, name='X', balance=100)
        y = Account.objects.create(user=other, name='Y', balance=100)
        t = Transfer.objects.create(user=other, from_account=x, to_account=y, amount=D('5'), date=today())
        self.assertEqual(self.client.post(reverse('transfer-edit', args=[t.pk]), self.post_data()).status_code, 404)
        self.assertEqual(self.client.post(reverse('transfer-delete', args=[t.pk])).status_code, 404)

    def test_the_list_shows_only_my_transfers_newest_first(self):
        old = Transfer.objects.create(user=self.user, from_account=self.a, to_account=self.b,
                                      amount=D('1'), date=today() - datetime.timedelta(days=5), description='older')
        new = Transfer.objects.create(user=self.user, from_account=self.a, to_account=self.b,
                                      amount=D('2'), date=today(), description='newer')
        response = self.client.get(reverse('transfer-list'))
        self.assertEqual([t.pk for t in response.context['transfers']], [new.pk, old.pk])

    def test_the_demo_user_is_sent_home(self):
        demo = self.make_user('demo')
        self.client.force_login(demo)
        response = self.client.get(reverse('transfer-create'))
        self.assertRedirects(response, reverse('home'), fetch_redirect_response=False)


class TestAccountList(AccountBase):
    def test_the_total_adds_the_accounts_and_subtracts_cards(self):
        self.acct('Bank', 10000)
        self.acct('Card', -2500, account_type='CREDIT_CARD')
        response = self.client.get(reverse('account-list'))
        self.assertEqual(response.context['total_balance'], D('7500.00'))

    def test_search_narrows_by_name(self):
        self.acct('Alpha')
        self.acct('Beta')
        response = self.client.get(reverse('account-list'), {'search': 'alp'})
        self.assertEqual([a.name for a in response.context['accounts']], ['Alpha'])

    def test_the_type_filter_matches_a_whole_group(self):
        self.acct('Bank', account_type='SAVINGS_ACCOUNT')
        self.acct('Wallet', account_type='CASH_WALLET')
        self.acct('Funds', account_type='MUTUAL_FUND')
        response = self.client.get(reverse('account-list'), {'type': 'CASH___BANK'})
        self.assertEqual({a.name for a in response.context['accounts']}, {'Bank', 'Wallet'})

    def test_pinned_accounts_come_first(self):
        self.acct('Aaa')
        self.acct('Zzz', is_pinned=True)
        response = self.client.get(reverse('account-list'))
        self.assertEqual([a.name for a in response.context['accounts']][0], 'Zzz')

    def test_sorting_by_name(self):
        self.acct('Beta')
        self.acct('Alpha')
        response = self.client.get(reverse('account-list'), {'sort': 'name_asc'})
        names = [a.name for g in response.context['grouped_accounts'] for a in g['accounts']]
        self.assertEqual(names, sorted(names))

    def test_other_users_accounts_are_never_shown(self):
        Account.objects.create(user=self.make_user('someone-else'), name='Secret', balance=999)
        response = self.client.get(reverse('account-list'))
        self.assertNotContains(response, 'Secret')

    def test_foreign_accounts_are_converted_once(self):
        self.seed_fx('80')
        self.acct('Dollars', 100, currency='$')
        response = self.client.get(reverse('account-list'))
        self.assertEqual(response.context['total_balance'], D('8000.00'))

    def test_the_list_works_without_any_accounts(self):
        response = self.client.get(reverse('account-list'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['total_balance'], D('0.00'))


class TestAccountDetail(AccountBase):
    def setUp(self):
        super().setUp()
        self.bank = self.acct('Bank', 10000)
        self.other = self.acct('Other', 0)

    def detail(self, **params):
        return self.client.get(reverse('account-detail', args=[self.bank.pk]), params)

    def populate(self):
        """Every movement type; returns the balance the account should end up with."""
        Income.objects.create(user=self.user, account=self.bank, amount=D('5000'), source_type='Salary',
                              date=today(), currency='₹')
        Expense.objects.create(user=self.user, account=self.bank, amount=D('1200'), category='Food',
                               date=today(), currency='₹')
        Transfer.objects.create(user=self.user, from_account=self.bank, to_account=self.other,
                                amount=D('700'), date=today())
        Transfer.objects.create(user=self.user, from_account=self.other, to_account=self.bank,
                                amount=D('100'), date=today())
        goal = SavingsGoal.objects.create(user=self.user, name='Trip', target_amount=D('9999'), currency='₹')
        GoalContribution.objects.create(goal=goal, account=self.bank, amount=D('300'), date=today())

    def test_the_detail_page_opens(self):
        self.assertEqual(self.detail().status_code, 200)

    def test_another_users_account_is_not_found(self):
        other = Account.objects.create(user=self.make_user('someone-else'), name='Theirs', balance=1)
        response = self.client.get(reverse('account-detail', args=[other.pk]))
        self.assertEqual(response.status_code, 404)

    def test_the_net_of_every_row_equals_how_far_the_balance_moved(self):
        self.populate()
        response = self.detail()
        self.assertEqual(response.context['filtered_net_total'], self.bal(self.bank) - D('10000.00'))
        self.assertEqual(response.context['page_obj'].paginator.count, 5)

    def test_the_trend_ends_at_the_current_balance_and_starts_at_the_opening_one(self):
        self.populate()
        trend = self.detail().context['trend_data']
        self.assertEqual(trend['type'], 'daily')
        self.assertEqual(trend['values'][-1], float(self.bal(self.bank)))
        self.assertEqual(trend['values'][0], 10000.0)
        self.assertEqual(len(trend['labels']), 31)

    def test_a_long_history_uses_monthly_points(self):
        Income.objects.create(user=self.user, account=self.bank, amount=D('1000'), source_type='Salary',
                              date=today() - datetime.timedelta(days=200), currency='₹')
        trend = self.detail().context['trend_data']
        self.assertEqual(trend['type'], 'monthly')
        self.assertEqual(len(trend['values']), 13)
        self.assertEqual(trend['values'][-1], float(self.bal(self.bank)))
        self.assertEqual(trend['values'][0], 10000.0)

    def test_filtering_by_type_keeps_only_those_rows(self):
        self.populate()
        response = self.detail(tx_type='EXPENSE')
        self.assertEqual(response.context['page_obj'].paginator.count, 1)
        self.assertEqual(response.context['filtered_net_total'], D('-1200.00'))

    def test_search_matches_descriptions(self):
        Expense.objects.create(user=self.user, account=self.bank, amount=D('50'), category='Food',
                               date=today(), currency='₹', description='zebra lunch')
        Expense.objects.create(user=self.user, account=self.bank, amount=D('60'), category='Food',
                               date=today(), currency='₹', description='plain')
        response = self.detail(search='zebra')
        self.assertEqual(response.context['page_obj'].paginator.count, 1)

    def test_dates_filter_the_rows(self):
        Expense.objects.create(user=self.user, account=self.bank, amount=D('50'), category='Food',
                               date=today() - datetime.timedelta(days=60), currency='₹')
        Expense.objects.create(user=self.user, account=self.bank, amount=D('60'), category='Food',
                               date=today(), currency='₹')
        start = (today() - datetime.timedelta(days=5)).isoformat()
        response = self.detail(time_period='custom', start_date=start, end_date=today().isoformat())
        self.assertEqual(response.context['page_obj'].paginator.count, 1)

    def test_amount_sort_orders_the_rows(self):
        for amount in (30, 10, 20):
            Expense.objects.create(user=self.user, account=self.bank, amount=D(str(amount)), category='Food',
                                   date=today(), currency='₹')
        response = self.detail(sort='amount_asc')
        self.assertEqual([r.amount for r in response.context['page_obj']], [D('10.00'), D('20.00'), D('30.00')])

    def test_it_pages_at_twenty_rows(self):
        for _ in range(25):
            Expense.objects.create(user=self.user, account=self.bank, amount=D('1'), category='Food',
                                   date=today(), currency='₹')
        response = self.detail()
        self.assertEqual(len(response.context['page_obj']), 20)
        self.assertTrue(response.context['is_paginated'])
        self.assertEqual(len(self.detail(page=2).context['page_obj']), 5)

    def test_a_deleted_account_still_opens_with_its_history(self):
        self.populate()
        Account.objects.filter(pk=self.bank.pk).update(is_active=False)
        response = self.detail()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['page_obj'].paginator.count, 5)

    def test_htmx_returns_the_partial_only(self):
        response = self.client.get(reverse('account-detail', args=[self.bank.pk]), HTTP_HX_REQUEST='true')
        self.assertTemplateUsed(response, 'expenses/partials/_account_detail.html')
        self.assertTemplateNotUsed(response, 'base.html')

    def test_a_foreign_currency_expense_is_shown_in_the_accounts_currency(self):
        self.seed_fx('80')
        Expense.objects.create(user=self.user, account=self.bank, amount=D('10'), category='Food',
                               date=today(), currency='$')
        response = self.detail()
        self.assertEqual(response.context['filtered_net_total'], D('-800.00'))
        self.assertEqual(self.bal(self.bank), D('9200.00'))


class TestMaturityIncome(AccountBase):
    def fd(self, **kw):
        values = dict(account_type='FD', deposit_principal=D('10000'), deposit_rate=D('10'),
                      deposit_start_date=today() - datetime.timedelta(days=365),
                      deposit_compounding='SIMPLE', deposit_maturity_date=today() - datetime.timedelta(days=1))
        values.update(kw)
        return self.acct('FD', 10000, **values)

    def record(self, account):
        return self.client.post(reverse('account-record-maturity-income', args=[account.pk]),
                                HTTP_REFERER='/accounts/list/')

    def test_it_records_the_interest_once(self):
        fd = self.fd()
        self.record(fd)
        income = Income.objects.filter(account=fd, source_type='Investment Returns')
        self.assertEqual(income.count(), 1)
        self.assertGreater(income.first().amount, D('900'))
        self.record(fd)
        self.assertEqual(income.count(), 1)

    def test_no_interest_means_no_income(self):
        fd = self.fd(deposit_rate=D('0'))
        response = self.record(fd)
        self.assertEqual(Income.objects.filter(account=fd).count(), 0)
        self.assertTrue(any('No accrued interest' in m for m in self.messages(response)))

    def test_another_users_account_is_not_found(self):
        other = Account.objects.create(user=self.make_user('someone-else'), name='Theirs', balance=0)
        self.assertEqual(self.record(other).status_code, 404)


class TestHoldings(AccountBase):
    def setUp(self):
        super().setUp()
        self.funds = self.acct('Funds', 0, account_type='MUTUAL_FUND')

    def add(self, **kw):
        data = {'instrument_name': 'Index Fund', 'scheme_code': '12345', 'units': '10', 'avg_cost': '50',
                'account_id': self.funds.pk, 'next': '/holdings/'}
        data.update(kw)
        with patch('expenses.nav_provider.NAVFetchService.fetch_scheme', return_value=(None, True)):
            return self.client.post(reverse('holding-create-global'), data)

    def test_adding_a_holding(self):
        response = self.add()
        self.assertRedirects(response, '/holdings/', fetch_redirect_response=False)
        from expenses.models import Holding
        holding = Holding.objects.get(account=self.funds)
        self.assertEqual((holding.units, holding.avg_cost, holding.currency, holding.instrument_type),
                         (D('10'), D('50'), '₹', 'MF'))

    def test_bad_input_is_refused(self):
        from expenses.models import Holding
        for kw in ({'instrument_name': ''}, {'units': '0'}, {'avg_cost': '-1'}, {'units': 'abc'}):
            self.add(**kw)
        self.assertEqual(Holding.objects.count(), 0)

    def test_a_missing_account_is_reported(self):
        response = self.add(account_id='')
        self.assertEqual(response.status_code, 302)

    def test_another_users_account_is_not_found(self):
        other = Account.objects.create(user=self.make_user('someone-else'), name='Theirs', balance=0,
                                       account_type='MUTUAL_FUND')
        self.assertEqual(self.add(account_id=other.pk).status_code, 404)

    def test_next_must_stay_on_this_site(self):
        response = self.add(next='https://evil.example/x')
        self.assertNotIn('evil.example', response['Location'])

    def test_a_blank_scheme_code_is_looked_up_by_name(self):
        from expenses.models import AMFIScheme, Holding
        AMFIScheme.objects.create(scheme_code='999', scheme_name='Index Fund Direct Growth')
        self.add(scheme_code='')
        self.assertEqual(Holding.objects.get(account=self.funds).scheme_code, '999')

    def make_holding(self, account=None, **kw):
        from expenses.models import Holding
        values = dict(account=account or self.funds, instrument_name='Fund', units=D('10'), avg_cost=D('50'),
                      currency='₹', scheme_code='1')
        values.update(kw)
        return Holding.objects.create(**values)

    def test_delete_needs_post_and_deactivates(self):
        holding = self.make_holding()
        self.assertEqual(self.client.get(reverse('holding-delete', args=[holding.pk])).status_code, 405)
        holding.refresh_from_db()
        self.assertTrue(holding.is_active)
        response = self.client.post(reverse('holding-delete', args=[holding.pk]), {'next': 'https://evil.example'})
        self.assertNotIn('evil.example', response['Location'])
        holding.refresh_from_db()
        self.assertFalse(holding.is_active)

    def test_another_users_holding_cannot_be_deleted_or_refreshed(self):
        other = Account.objects.create(user=self.make_user('someone-else'), name='Theirs', balance=0,
                                       account_type='MUTUAL_FUND')
        holding = self.make_holding(other)
        self.assertEqual(self.client.post(reverse('holding-delete', args=[holding.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse('refresh-holding-nav', args=[holding.pk])).status_code, 404)

    def test_refresh_reports_success_and_failure(self):
        holding = self.make_holding()

        class Cache:
            latest_nav = D('12.5')

        with patch('expenses.nav_provider.NAVFetchService.fetch_scheme', return_value=(Cache(), True)):
            response = self.client.get(reverse('refresh-holding-nav', args=[holding.pk]))
        self.assertRedirects(response, reverse('account-detail', args=[self.funds.pk]), fetch_redirect_response=False)
        self.assertTrue(any('12.5' in m for m in self.messages(response)))
        with patch('expenses.nav_provider.NAVFetchService.fetch_scheme', return_value=(None, False)):
            response = self.client.get(reverse('refresh-holding-nav', args=[holding.pk]))
        self.assertTrue(any('Failed to fetch' in m for m in self.messages(response)))

    def test_refresh_without_a_scheme_code_warns(self):
        holding = self.make_holding(scheme_code=None)
        response = self.client.get(reverse('refresh-holding-nav', args=[holding.pk]))
        self.assertTrue(any('No scheme code' in m for m in self.messages(response)))

    def test_the_list_totals_use_the_latest_value_or_cost(self):
        from expenses.models import Valuation
        valued = self.make_holding(instrument_name='Valued')
        Valuation.objects.create(holding=valued, value=D('700'), as_of_date=today() - datetime.timedelta(days=3))
        Valuation.objects.create(holding=valued, value=D('800'), as_of_date=today())
        self.make_holding(instrument_name='Unvalued', units=D('2'), avg_cost=D('100'))
        response = self.client.get(reverse('holding-list'))
        self.assertEqual(response.context['total_valuation'], D('1000.00'))
        self.assertEqual(response.context['total_cost'], D('700.00'))
        self.assertEqual(response.context['unrealized_gain'], D('300.00'))
        self.assertEqual(response.context['gain_pct'], D('42.9'))

    def test_the_list_converts_other_currencies(self):
        self.seed_fx('80')
        dollars = self.acct('Dollar funds', 0, account_type='MUTUAL_FUND', currency='$')
        self.make_holding(dollars, currency='$', units=D('1'), avg_cost=D('10'))
        response = self.client.get(reverse('holding-list'))
        self.assertEqual(response.context['total_cost'], D('800.00'))

    def test_the_list_hides_removed_holdings_and_other_users(self):
        self.make_holding(instrument_name='Gone', is_active=False)
        other = Account.objects.create(user=self.make_user('someone-else'), name='Theirs', balance=0,
                                       account_type='MUTUAL_FUND')
        self.make_holding(other, instrument_name='Secret')
        response = self.client.get(reverse('holding-list'))
        self.assertEqual(list(response.context['holdings']), [])

    def test_search_prefers_direct_growth_and_exact_matches(self):
        from expenses.models import AMFIScheme
        AMFIScheme.objects.create(scheme_code='1', scheme_name='Nifty Fund Regular IDCW')
        AMFIScheme.objects.create(scheme_code='2', scheme_name='Nifty Fund Direct Growth')
        AMFIScheme.objects.create(scheme_code='3', scheme_name='Other')
        results = self.client.get(reverse('search-amfi-schemes'), {'q': 'nifty fund'}).json()['results']
        self.assertEqual([r['scheme_code'] for r in results], ['2', '1'])

    def test_search_needs_two_characters_and_login(self):
        self.assertEqual(self.client.get(reverse('search-amfi-schemes'), {'q': 'n'}).json(), {'results': []})
        self.client.logout()
        self.assertEqual(self.client.get(reverse('search-amfi-schemes'), {'q': 'nifty'}).status_code, 401)

    def test_search_falls_back_to_the_live_api(self):
        class Resp:
            status_code = 200

            def json(self):
                return [{'schemeCode': 77, 'schemeName': 'Live Fund'}]

        with patch('requests.get', return_value=Resp()):
            results = self.client.get(reverse('search-amfi-schemes'), {'q': 'live'}).json()['results']
        self.assertEqual(results, [{'scheme_code': '77', 'scheme_name': 'Live Fund', 'isin': None}])
        with patch('requests.get', side_effect=OSError):
            results = self.client.get(reverse('search-amfi-schemes'), {'q': 'live'}).json()['results']
        self.assertEqual(results, [])


class TestForeignCurrencyDetail(AccountBase):
    def setUp(self):
        super().setUp()
        self.seed_fx('80')
        self.usd = self.acct('Dollars', 1000, currency='$')
        self.inr = self.acct('Rupees', 100000)

    def test_every_kind_of_row_is_added_up_in_the_accounts_currency(self):
        Income.objects.create(user=self.user, account=self.usd, amount=D('8000'), source_type='Salary',
                              date=today(), currency='₹')
        Expense.objects.create(user=self.user, account=self.usd, amount=D('1600'), category='Food',
                               date=today(), currency='₹')
        Transfer.objects.create(user=self.user, from_account=self.inr, to_account=self.usd,
                                amount=D('800'), date=today())
        goal = SavingsGoal.objects.create(user=self.user, name='Trip', target_amount=D('9999'), currency='₹')
        GoalContribution.objects.create(goal=goal, account=self.usd, amount=D('400'), date=today())
        response = self.client.get(reverse('account-detail', args=[self.usd.pk]))
        # +100 income, -20 expense, +10 transfer in, -5 saved
        self.assertEqual(response.context['filtered_net_total'], D('85.00'))
        self.assertEqual(self.bal(self.usd), D('1085.00'))

    def test_the_trend_follows_the_same_conversion(self):
        Expense.objects.create(user=self.user, account=self.usd, amount=D('1600'), category='Food',
                               date=today(), currency='₹')
        trend = self.client.get(reverse('account-detail', args=[self.usd.pk])).context['trend_data']
        self.assertEqual((trend['values'][0], trend['values'][-1]), (1000.0, 980.0))

    def test_transfers_out_show_in_the_accounts_currency(self):
        Transfer.objects.create(user=self.user, from_account=self.usd, to_account=self.inr,
                                amount=D('10'), date=today())
        response = self.client.get(reverse('account-detail', args=[self.usd.pk]))
        self.assertEqual(response.context['filtered_net_total'], D('-10.00'))
        self.assertEqual(self.bal(self.inr), D('100800.00'))


class TestLedgerPresentation(AccountBase):
    """The account ledger uses the same table vocabulary as All Transactions."""

    def setUp(self):
        super().setUp()
        self.bank = self.acct('Bank', 10000)
        self.other = self.acct('Savings', 0)

    def detail(self, **params):
        return self.client.get(reverse('account-detail', args=[self.bank.pk]), params)

    def rows(self, **params):
        return {r.transaction_type: r for r in self.detail(**params).context['page_obj']}

    def populate(self):
        Income.objects.create(user=self.user, account=self.bank, amount=D('5000'), source_type='Salary',
                              date=today(), currency='₹', description='March salary')
        Expense.objects.create(user=self.user, account=self.bank, amount=D('1200'), category='Food',
                               date=today(), currency='₹', description='Dinner')
        Transfer.objects.create(user=self.user, from_account=self.bank, to_account=self.other,
                                amount=D('700'), date=today())
        Transfer.objects.create(user=self.user, from_account=self.other, to_account=self.bank,
                                amount=D('100'), date=today())
        goal = SavingsGoal.objects.create(user=self.user, name='Trip', target_amount=D('9999'), currency='₹')
        GoalContribution.objects.create(goal=goal, account=self.bank, amount=D('300'), date=today())

    def test_each_row_has_a_kind_a_label_and_a_direction(self):
        self.populate()
        rows = self.rows()
        self.assertEqual((rows['EXPENSE'].row_kind, rows['EXPENSE'].row_label, rows['EXPENSE'].row_is_inflow),
                         ('expense', 'Expense', False))
        self.assertEqual((rows['INCOME'].row_kind, rows['INCOME'].row_is_inflow), ('income', True))
        self.assertEqual((rows['TRANSFER_OUT'].row_kind, rows['TRANSFER_OUT'].row_is_inflow), ('transfer', False))
        self.assertEqual((rows['TRANSFER_IN'].row_kind, rows['TRANSFER_IN'].row_is_inflow), ('transfer', True))
        self.assertEqual((rows['SAVINGS'].row_kind, rows['SAVINGS'].row_is_inflow), ('savings', False))

    def test_the_category_or_source_badge_names_what_the_row_is(self):
        self.populate()
        rows = self.rows()
        self.assertEqual(rows['EXPENSE'].row_badge, 'Food')
        self.assertEqual(rows['INCOME'].row_badge, 'Salary')
        self.assertEqual(rows['TRANSFER_OUT'].row_badge, 'To Savings')
        self.assertEqual(rows['TRANSFER_IN'].row_badge, 'From Savings')
        self.assertEqual(rows['SAVINGS'].row_badge, 'Trip')

    def test_a_capital_event_left_out_of_net_worth_is_neutral(self):
        from expenses.models import CapitalEvent
        CapitalEvent.objects.create(user=self.user, account=self.bank, amount=D('900'), date=today(),
                                    subtype='ASSET_PURCHASE', currency='₹', include_in_net_worth=False)
        row = self.rows()['CAPITAL_EVENT']
        self.assertTrue(row.row_is_neutral)
        self.assertEqual(self.bal(self.bank), D('10000.00'))

    def test_the_summary_adds_up_what_came_in_and_went_out(self):
        self.populate()
        context = self.detail().context
        self.assertEqual(context['money_in'], D('5100.00'))
        self.assertEqual(context['money_out'], D('2200.00'))
        self.assertEqual(context['money_in'] - context['money_out'], context['filtered_net_total'])
        self.assertEqual(context['row_count'], 5)

    def test_the_summary_follows_the_filters_but_the_header_count_does_not(self):
        self.populate()
        context = self.detail(tx_type='EXPENSE').context
        self.assertEqual((context['row_count'], context['money_in'], context['money_out']),
                         (1, D('0.00'), D('1200.00')))
        self.assertEqual(context['total_transactions'], 5)
        self.assertEqual(self.detail().context['total_transactions'], 5)

    def test_it_uses_the_shared_table_and_type_pills(self):
        self.populate()
        html = self.detail().content.decode()
        self.assertIn('tmr-table', html)
        self.assertIn('type-badge type-expense', html)
        self.assertIn('type-badge type-income', html)
        self.assertIn('type-badge type-transfer', html)
        self.assertIn('type-badge type-savings', html)
        self.assertIn('amount-income', html)
        self.assertIn('amount-expense', html)

    def test_the_shared_colours_live_in_the_global_stylesheet(self):
        from pathlib import Path
        css = (Path(__file__).resolve().parents[2] / 'static' / 'style.css').read_text()
        for name in ('type-badge', 'type-expense', 'type-savings', 'amount-income', 'amount-expense', 'amount-neutral'):
            self.assertIn(f'.{name}', css)

    def test_edit_links_come_back_to_this_page(self):
        self.populate()
        html = self.detail(tx_type='EXPENSE').content.decode()
        self.assertIn('/edit/?next=', html)

    def test_the_amount_header_sorts_and_cycles(self):
        self.populate()
        html = self.detail().content.decode()
        self.assertIn('amount-sort-link', html)
        self.assertIn('sort=amount_desc', html)
        self.assertIn('sort=amount_asc', self.detail(sort='amount_desc').content.decode())
        self.assertIn('sort=date_desc', self.detail(sort='amount_asc').content.decode())

    def test_the_toolbar_offers_the_ledger_filters(self):
        config = self.detail().context['filter_config']
        self.assertEqual([f.key for f in config.filters], ['tx_type', 'category', 'amount_range'])
        self.assertTrue(config.supports_search)

    def test_an_empty_ledger_says_so_and_a_filtered_one_offers_to_clear(self):
        empty = self.detail().content.decode()
        self.assertIn('no activities recorded for this account yet', empty)
        self.assertNotIn('Clear Filters', empty)
        self.populate()
        filtered = self.detail(search='zzz-no-match').content.decode()
        self.assertIn('Clear Filters', filtered)

    def test_one_transaction_reads_in_the_singular(self):
        Expense.objects.create(user=self.user, account=self.bank, amount=D('10'), category='Food',
                               date=today(), currency='₹')
        html = self.detail().content.decode()
        self.assertIn('transaction</span>', html.replace('\n', ''))
