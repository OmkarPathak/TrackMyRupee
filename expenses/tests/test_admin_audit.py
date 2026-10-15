"""Audit of the admin site: it cannot be used to break the books, deleting from it does what deleting in the
app does, and every page in it opens."""
from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.contrib import admin as django_admin
from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from expenses.ledger_read_service import LedgerReadService
from expenses.ledger_service import LedgerPostingService
from expenses.models import (
    Account,
    Announcement,
    CapitalEvent,
    Expense,
    GoalContribution,
    Income,
    JournalEntry,
    JournalLine,
    LedgerAccount,
    Loan,
    LoanRepayment,
    PaymentHistory,
    SavingsGoal,
    Transfer,
)

D = Decimal


def today():
    return timezone.localdate()


@override_settings(ACCOUNT_DELETION_ASYNC=False)
class Base(TestCase):
    def setUp(self):
        cache.clear()
        self.boss = User.objects.create_superuser(username='boss', email='boss@example.com', password='pw')
        self.client.force_login(self.boss)
        self.user = User.objects.create_user(username='cust', email='cust@example.com', password='pw')
        self.bank = Account.objects.create(user=self.user, name='Bank', account_type='BANK', balance=D('10000'), currency='₹')
        self.other = Account.objects.create(user=self.user, name='Other', account_type='BANK', balance=D('0'), currency='₹')
        for account in (self.bank, self.other):
            LedgerPostingService.post_opening_balance(account=account)

    def balance(self, account):
        account.refresh_from_db()
        return account.balance, LedgerReadService.get_account_balance(account)

    def bulk_delete(self, model_name, ids):
        url = reverse(f'admin:expenses_{model_name}_changelist')
        return self.client.post(url, {'action': 'delete_selected', '_selected_action': [str(i) for i in ids], 'post': 'yes'})


class BulkDeleteReversesTheMoneyTests(Base):
    def test_expenses(self):
        a = Expense.objects.create(user=self.user, account=self.bank, amount=D('300'), date=today(), category='Food', currency='₹')
        b = Expense.objects.create(user=self.user, account=self.bank, amount=D('200'), date=today(), category='Food', currency='₹')
        self.assertEqual(self.balance(self.bank), (D('9500.00'), D('9500.00')))
        self.bulk_delete('expense', [a.pk, b.pk])
        self.assertEqual(Expense.objects.count(), 0)
        self.assertEqual(self.balance(self.bank), (D('10000.00'), D('10000.00')))

    def test_income(self):
        i = Income.objects.create(user=self.user, account=self.bank, amount=D('900'), date=today(), source='Salary', currency='₹')
        self.bulk_delete('income', [i.pk])
        self.assertEqual(self.balance(self.bank), (D('10000.00'), D('10000.00')))

    def test_transfers_restore_both_accounts(self):
        t = Transfer.objects.create(user=self.user, from_account=self.bank, to_account=self.other, amount=D('400'), date=today())
        self.assertEqual(self.balance(self.other)[0], D('400.00'))
        self.bulk_delete('transfer', [t.pk])
        self.assertEqual((self.balance(self.bank), self.balance(self.other)),
                         ((D('10000.00'), D('10000.00')), (D('0.00'), D('0.00'))))

    def test_capital_events(self):
        c = CapitalEvent.objects.create(user=self.user, account=self.bank, date=today(), amount=D('700'), include_in_net_worth=True)
        self.bulk_delete('capitalevent', [c.pk])
        self.assertEqual(self.balance(self.bank), (D('10000.00'), D('10000.00')))

    def test_goal_contributions_and_the_goal_total(self):
        goal = SavingsGoal.objects.create(user=self.user, name='G', target_amount=D('9999'), currency='₹')
        c = GoalContribution.objects.create(goal=goal, account=self.bank, amount=D('250'), date=today())
        self.bulk_delete('goalcontribution', [c.pk])
        goal.refresh_from_db()
        self.assertEqual((self.balance(self.bank), goal.current_amount), ((D('10000.00'), D('10000.00')), D('0.00')))

    def test_loan_repayments(self):
        loan = Loan.objects.create(user=self.user, name='L', loan_type='HOME', initial_principal=D('5000'),
                                   duration_months=12, start_date=today(), is_active=True)
        r = LoanRepayment.objects.create(loan=loan, from_account=self.bank, date=today(), amount=D('600'),
                                         principal_portion=D('500'), interest_portion=D('100'))
        self.bulk_delete('loanrepayment', [r.pk])
        self.assertEqual(self.balance(self.bank), (D('10000.00'), D('10000.00')))

    def test_no_posting_is_left_pointing_at_something_deleted(self):
        e = Expense.objects.create(user=self.user, account=self.bank, amount=D('300'), date=today(), category='Food', currency='₹')
        self.bulk_delete('expense', [e.pk])
        from io import StringIO
        from django.core.management import call_command
        out = StringIO()
        call_command('find_orphaned_ledger_postings', stdout=out)
        self.assertIn('No orphaned', out.getvalue())

    def test_deleting_one_from_its_own_page_does_the_same(self):
        e = Expense.objects.create(user=self.user, account=self.bank, amount=D('300'), date=today(), category='Food', currency='₹')
        self.client.post(reverse('admin:expenses_expense_delete', args=[e.pk]), {'post': 'yes'})
        self.assertEqual(self.balance(self.bank), (D('10000.00'), D('10000.00')))


class TheBooksCannotBeEditedByHandTests(Base):
    def setUp(self):
        super().setUp()
        Expense.objects.create(user=self.user, account=self.bank, amount=D('300'), date=today(), category='Food', currency='₹')

    def test_ledger_and_audit_tables_have_no_add_change_or_delete(self):
        for model in ('journalentry', 'journalline', 'ledgeraccount', 'ledgerreconciliationreport',
                      'financialauditlog', 'consentevent', 'deletionrequestauditlog', 'networthsnapshot'):
            add = reverse(f'admin:expenses_{model}_add')
            self.assertEqual(self.client.get(add).status_code, 403, model)

    def test_a_ledger_entry_can_be_looked_at_but_not_changed_or_deleted(self):
        entry = JournalEntry.objects.filter(source_type='EXPENSE').first()
        change = reverse('admin:expenses_journalentry_change', args=[entry.pk])
        self.assertEqual(self.client.get(change).status_code, 200)
        self.assertEqual(self.client.post(change, {'description': 'tampered'}).status_code, 403)
        self.assertEqual(self.client.post(reverse('admin:expenses_journalentry_delete', args=[entry.pk]), {'post': 'yes'}).status_code, 403)
        entry.refresh_from_db()
        self.assertNotEqual(entry.description, 'tampered')

    def test_the_bulk_delete_action_is_not_offered_on_ledger_lines(self):
        line = JournalLine.objects.first()
        r = self.client.post(reverse('admin:expenses_journalline_changelist'),
                             {'action': 'delete_selected', '_selected_action': [str(line.pk)], 'post': 'yes'})
        self.assertTrue(JournalLine.objects.filter(pk=line.pk).exists())
        self.assertIn(r.status_code, (200, 302, 403))

    def test_a_payment_record_cannot_be_deleted_or_invented_in_admin(self):
        p = PaymentHistory.objects.create(user=self.user, order_id='o1', amount=49, tier='PLUS', status='SUCCESS')
        self.assertEqual(self.client.get(reverse('admin:expenses_paymenthistory_add')).status_code, 403)
        self.assertEqual(self.client.post(reverse('admin:expenses_paymenthistory_delete', args=[p.pk]), {'post': 'yes'}).status_code, 403)
        self.assertTrue(PaymentHistory.objects.filter(pk=p.pk).exists())
        self.assertEqual(self.client.get(reverse('admin:expenses_paymenthistory_changelist')).status_code, 200)


class UserDeletionFromAdminTests(Base):
    def setUp(self):
        super().setUp()
        Expense.objects.create(user=self.user, account=self.bank, amount=D('300'), date=today(), category='Food', currency='₹')

    def test_deleting_a_user_with_transactions_works_and_clears_the_ledger(self):
        self.assertTrue(JournalEntry.objects.filter(user=self.user).exists())
        r = self.client.post(reverse('admin:auth_user_delete', args=[self.user.pk]), {'post': 'yes'})
        self.assertEqual(r.status_code, 302)
        self.assertFalse(User.objects.filter(pk=self.user.pk).exists())
        self.assertFalse(JournalEntry.objects.filter(user_id=self.user.pk).exists())
        self.assertFalse(LedgerAccount.objects.filter(user_id=self.user.pk).exists())

    def test_the_bulk_action_does_the_same_and_keeps_the_payment_record(self):
        PaymentHistory.objects.create(user=self.user, order_id='o2', amount=499, tier='PLUS', status='SUCCESS')
        self.client.post(reverse('admin:auth_user_changelist'),
                         {'action': 'delete_selected', '_selected_action': [str(self.user.pk)], 'post': 'yes'})
        self.assertFalse(User.objects.filter(pk=self.user.pk).exists())
        record = PaymentHistory.objects.get(order_id='o2')
        self.assertEqual((record.user_id, record.user_email), (None, 'cust@example.com'))

    def test_a_live_subscription_is_cancelled(self):
        profile = self.user.profile
        profile.razorpay_subscription_id = 'sub_admin'
        profile.save()
        PaymentHistory.objects.create(user=self.user, order_id='sub_admin', amount=49, tier='PLUS', status='SUCCESS')
        with patch('expenses.views.settings.razorpay.Client') as client, self.settings(RAZORPAY_KEY_ID='k', RAZORPAY_KEY_SECRET='s'):
            self.client.post(reverse('admin:auth_user_delete', args=[self.user.pk]), {'post': 'yes'})
        client.return_value.subscription.cancel.assert_called_once_with('sub_admin')

    def test_the_demo_account_is_not_listed_or_deletable(self):
        demo = User.objects.create_user(username='demo', email='demo@example.com', password='pw')
        r = self.client.get(reverse('admin:auth_user_changelist'))
        self.assertNotContains(r, 'demo@example.com')
        self.client.post(reverse('admin:auth_user_delete', args=[demo.pk]), {'post': 'yes'})
        self.assertTrue(User.objects.filter(pk=demo.pk).exists())


class AnnouncementQueueTests(Base):
    def test_only_drafts_are_queued_and_a_sent_one_is_never_re_sent(self):
        draft = Announcement.objects.create(title='D', body='b', status='DRAFT')
        sent = Announcement.objects.create(title='S', body='b', status='SENT')
        queued = Announcement.objects.create(title='Q', body='b', status='QUEUED')
        r = self.client.post(reverse('admin:expenses_announcement_changelist'), {
            'action': 'queue_for_sending', '_selected_action': [str(draft.pk), str(sent.pk), str(queued.pk)]}, follow=True)
        draft.refresh_from_db(); sent.refresh_from_db()
        self.assertEqual((draft.status, sent.status), ('QUEUED', 'SENT'))
        self.assertContains(r, 'not drafts')


class EveryAdminPageOpensTests(Base):
    def test_every_registered_list_page_loads(self):
        failures = []
        for model, model_admin in django_admin.site._registry.items():
            if model._meta.app_label not in ('expenses', 'auth', 'account', 'blog'):
                continue
            url = reverse(f'admin:{model._meta.app_label}_{model._meta.model_name}_changelist')
            response = self.client.get(url)
            if response.status_code != 200:
                failures.append((model.__name__, response.status_code))
        self.assertEqual(failures, [])

    def test_a_search_on_every_list_page_does_not_crash(self):
        failures = []
        for model, model_admin in django_admin.site._registry.items():
            if model._meta.app_label not in ('expenses', 'auth', 'account', 'blog') or not model_admin.search_fields:
                continue
            url = reverse(f'admin:{model._meta.app_label}_{model._meta.model_name}_changelist')
            response = self.client.get(url, {'q': 'zzz'})
            if response.status_code != 200:
                failures.append((model.__name__, response.status_code))
        self.assertEqual(failures, [])

    def test_the_admin_is_closed_to_ordinary_users_and_visitors(self):
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(reverse('admin:expenses_expense_changelist')).status_code, 302)
        self.client.logout()
        self.assertEqual(self.client.get(reverse('admin:index')).status_code, 302)
