from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from expenses import reconciliation
from expenses.models import (
    Account,
    AccountReconciliation,
    Expense,
    FinancialAuditLog,
    Income,
    JournalEntry,
    LedgerAccount,
    LedgerReconciliationReport,
)

D = Decimal


def _make_user(username, tier='PLUS'):
    user = User.objects.create_user(username=username, password='password')
    profile = user.profile
    profile.has_seen_tutorial = True
    profile.tier = tier
    profile.currency = '₹'
    profile.save()
    return user


class ReconciliationTestBase(TestCase):
    def setUp(self):
        self.user = _make_user('recon_user')
        self.client.force_login(self.user)
        self.today = date.today()
        self.bank = Account.objects.create(
            user=self.user, name='Bank', account_type='BANK', balance=D('1000.00'), currency='₹',
        )
        # Opening 1000, then +500 income ten days ago and -200 expense two days ago: 1300 today,
        # 1500 as of five days ago.
        Income.objects.create(
            user=self.user, date=self.today - timedelta(days=10), amount=D('500.00'),
            source='Salary', description='Pay', account=self.bank, currency='₹',
        )
        Expense.objects.create(
            user=self.user, date=self.today - timedelta(days=2), amount=D('200.00'),
            description='Groceries', category='Food', account=self.bank, currency='₹',
        )
        self.bank.refresh_from_db()
        self.five_days_ago = self.today - timedelta(days=5)

    def ledger_balance(self):
        from expenses.ledger_read_service import LedgerReadService

        self.bank.refresh_from_db()
        return LedgerReadService.get_account_balance(self.bank)


class ReconciliationServiceTest(ReconciliationTestBase):
    def test_balance_as_of_ignores_later_dated_transactions(self):
        self.assertEqual(reconciliation.current_balance(self.bank), D('1300.00'))
        self.assertEqual(reconciliation.balance_as_of(self.bank, self.five_days_ago), D('1500.00'))
        self.assertEqual(reconciliation.balance_as_of(self.bank, self.today), D('1300.00'))

    def test_matching_statement_has_no_difference(self):
        result = reconciliation.compare(self.bank, self.five_days_ago, D('1500.004'))
        self.assertTrue(result.matched)
        self.assertEqual(result.later_count, 1)

    def test_difference_is_statement_minus_app(self):
        result = reconciliation.compare(self.bank, self.five_days_ago, D('1450.00'))
        self.assertFalse(result.matched)
        self.assertEqual(result.difference, D('-50.00'))

    def test_suspect_is_a_transaction_the_size_of_the_gap(self):
        Expense.objects.create(
            user=self.user, date=self.today - timedelta(days=6), amount=D('75.00'),
            description='Dinner', category='Food', account=self.bank, currency='₹',
        )
        result = reconciliation.compare(self.bank, self.five_days_ago, D('1575.00'))
        # App shows 1425 on that date; the statement is 150 higher, twice the dinner.
        self.assertEqual(result.difference, D('150.00'))
        reasons = {flow.description: reason for flow, reason in result.suspects}
        self.assertEqual(reasons.get('Dinner'), 'wrong_direction')

    def test_adjust_makes_the_app_match_and_is_recorded_once(self):
        rec = reconciliation.record(self.bank, self.five_days_ago, D('1450.00'), action='adjust', note='Mar')
        self.assertEqual(rec.status, 'ADJUSTED')
        self.assertEqual(rec.difference, D('-50.00'))
        self.assertEqual(rec.app_balance, D('1500.00'))
        self.assertIsNotNone(rec.adjustment_entry)
        self.assertEqual(self.ledger_balance(), D('1250.00'))
        self.assertEqual(self.bank.balance, D('1250.00'))
        self.assertEqual(reconciliation.balance_as_of(self.bank, self.five_days_ago), D('1450.00'))

        # A second submit of the same statement finds nothing to fix and posts nothing.
        again = reconciliation.record(self.bank, self.five_days_ago, D('1450.00'), action='adjust')
        self.assertEqual(again.status, 'MATCHED')
        self.assertIsNone(again.adjustment_entry)
        self.assertEqual(self.ledger_balance(), D('1250.00'))
        self.assertEqual(JournalEntry.objects.filter(idempotency_key__contains='RECON').count(), 1)
        self.assertEqual(FinancialAuditLog.objects.filter(model_name='AccountReconciliation').count(), 2)

    def test_adjust_updates_the_cached_ledger_balance(self):
        # Backfilled accounts read their balance from the cached ledger balance.
        LedgerAccount.objects.filter(
            code=f"USR:{self.user.id}:ASSET:ACCOUNT:{self.bank.id}",
        ).update(cached_balance=D('1300.00'))
        self.assertEqual(reconciliation.current_balance(self.bank), D('1300.00'))

        reconciliation.record(self.bank, self.five_days_ago, D('1450.00'), action='adjust')

        cached = LedgerAccount.objects.get(code=f"USR:{self.user.id}:ASSET:ACCOUNT:{self.bank.id}").cached_balance
        self.assertEqual(cached, D('1250.00'))
        self.assertEqual(reconciliation.current_balance(self.bank), D('1250.00'))

    def test_leaving_it_open_changes_no_balance(self):
        rec = reconciliation.record(self.bank, self.five_days_ago, D('1400.00'), action='open')
        self.assertEqual(rec.status, 'OPEN')
        self.assertIsNone(rec.adjustment_entry)
        self.assertEqual(self.ledger_balance(), D('1300.00'))
        self.assertEqual(self.bank.balance, D('1300.00'))

    def test_account_types_without_a_statement_are_refused(self):
        fd = Account.objects.create(user=self.user, name='FD', account_type='FD', balance=D('5000.00'))
        self.assertFalse(reconciliation.is_reconcilable(fd))
        with self.assertRaises(reconciliation.ReconciliationError):
            reconciliation.record(fd, self.today, D('5000.00'), action='adjust')
        self.assertEqual(AccountReconciliation.objects.count(), 0)


class ReconciliationViewTest(ReconciliationTestBase):
    def payload(self, **overrides):
        data = {
            'statement_date': self.five_days_ago.isoformat(),
            'statement_balance': '1450.00',
            'note': 'March',
        }
        data.update(overrides)
        return data

    def test_compare_shows_the_difference_and_saves_nothing(self):
        response = self.client.post(reverse('account-reconcile', args=[self.bank.uuid]), self.payload())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['comparison'].difference, D('-50.00'))
        self.assertEqual(AccountReconciliation.objects.count(), 0)

    def test_future_statement_date_is_rejected(self):
        future = (self.today + timedelta(days=1)).isoformat()
        response = self.client.post(reverse('account-reconcile', args=[self.bank.uuid]), self.payload(statement_date=future))
        self.assertIsNone(response.context['comparison'])
        self.assertIn('statement_date', response.context['form'].errors)

    def test_confirm_adjusts_and_lands_on_history(self):
        response = self.client.post(
            reverse('account-reconcile-confirm', args=[self.bank.uuid]), self.payload(action='adjust'),
        )
        self.assertRedirects(response, reverse('account-reconciliation-history', args=[self.bank.uuid]))
        rec = AccountReconciliation.objects.get()
        self.assertEqual((rec.status, rec.note), ('ADJUSTED', 'March'))
        self.assertEqual(self.ledger_balance(), D('1250.00'))
        history = self.client.get(reverse('account-reconciliation-history', args=[self.bank.uuid]))
        self.assertContains(history, 'Adjusted')

    def test_confirm_needs_a_known_action(self):
        response = self.client.post(
            reverse('account-reconcile-confirm', args=[self.bank.uuid]), self.payload(action='delete'),
        )
        self.assertRedirects(response, reverse('account-reconcile', args=[self.bank.uuid]))
        self.assertEqual(AccountReconciliation.objects.count(), 0)

    def test_confirm_only_accepts_post(self):
        response = self.client.get(reverse('account-reconcile-confirm', args=[self.bank.uuid]))
        self.assertEqual(response.status_code, 405)

    def test_other_users_account_is_not_found(self):
        other = _make_user('someone_else')
        theirs = Account.objects.create(user=other, name='Theirs', account_type='BANK', balance=D('10.00'))
        for name in ('account-reconcile', 'account-reconcile-confirm', 'account-reconciliation-history'):
            response = self.client.post(reverse(name, args=[theirs.uuid]), self.payload(action='adjust'))
            self.assertEqual(response.status_code, 404, name)
        self.assertEqual(AccountReconciliation.objects.count(), 0)

    def test_anonymous_user_is_sent_to_login(self):
        self.client.logout()
        response = self.client.get(reverse('account-reconcile', args=[self.bank.uuid]))
        self.assertEqual(response.status_code, 302)
        self.assertIn('login', response.url)

    def test_deposit_account_redirects_to_its_page(self):
        fd = Account.objects.create(user=self.user, name='FD', account_type='FD', balance=D('5000.00'))
        response = self.client.get(reverse('account-reconcile', args=[fd.uuid]))
        self.assertRedirects(response, reverse('account-detail', args=[fd.uuid]), fetch_redirect_response=False)

    def test_demo_user_cannot_save(self):
        demo = _make_user('demo')
        account = Account.objects.create(user=demo, name='Demo bank', account_type='BANK', balance=D('100.00'))
        self.client.force_login(demo)
        self.client.post(reverse('account-reconcile-confirm', args=[account.uuid]), self.payload(action='adjust'))
        self.assertEqual(AccountReconciliation.objects.count(), 0)


class ReconciliationPlanTierTest(TestCase):
    def test_accounts_beyond_the_free_limit_are_locked_but_the_rest_work(self):
        user = _make_user('free_user', tier='FREE')
        self.client.force_login(user)
        accounts = [
            Account.objects.create(user=user, name=f'Acc {i}', account_type='BANK', balance=D('100.00'))
            for i in range(3)
        ]
        payload = {'statement_date': date.today().isoformat(), 'statement_balance': '90.00', 'action': 'adjust'}

        allowed = self.client.post(reverse('account-reconcile-confirm', args=[accounts[0].uuid]), payload)
        self.assertRedirects(allowed, reverse('account-reconciliation-history', args=[accounts[0].uuid]))
        self.assertEqual(AccountReconciliation.objects.filter(account=accounts[0], status='ADJUSTED').count(), 1)

        locked = self.client.post(reverse('account-reconcile-confirm', args=[accounts[2].uuid]), payload)
        self.assertRedirects(locked, reverse('pricing'), fetch_redirect_response=False)
        self.assertFalse(AccountReconciliation.objects.filter(account=accounts[2]).exists())

    def test_list_offers_reconcile_only_where_it_applies(self):
        user = _make_user('list_user')
        self.client.force_login(user)
        bank = Account.objects.create(user=user, name='Bank', account_type='BANK', balance=D('100.00'))
        fd = Account.objects.create(user=user, name='Deposit', account_type='FD', balance=D('100.00'))
        AccountReconciliation.objects.create(
            user=user, account=bank, statement_date=date.today(), statement_balance=D('100'),
            app_balance=D('100'), difference=D('0'), status='MATCHED',
        )
        response = self.client.get(reverse('account-list'))
        self.assertContains(response, reverse('account-reconcile', args=[bank.uuid]))
        self.assertNotContains(response, reverse('account-reconcile', args=[fd.uuid]))
        self.assertContains(response, 'Reconciled')

    def test_detail_page_links_to_reconcile(self):
        user = _make_user('detail_user')
        self.client.force_login(user)
        bank = Account.objects.create(user=user, name='Bank', account_type='BANK', balance=D('100.00'))
        response = self.client.get(reverse('account-detail', args=[bank.uuid]))
        self.assertContains(response, reverse('account-reconcile', args=[bank.uuid]))


class ReconcileLedgersCommandTest(ReconciliationTestBase):
    def test_rerunning_the_same_day_keeps_one_report_per_account(self):
        call_command('reconcile_ledgers', user_id=self.user.id)
        call_command('reconcile_ledgers', user_id=self.user.id)
        self.assertEqual(LedgerReconciliationReport.objects.filter(account=self.bank).count(), 1)

    def test_old_matches_are_pruned_but_old_drift_is_kept(self):
        old = self.today - timedelta(days=120)
        for status in ('MATCH', 'DRIFT'):
            LedgerReconciliationReport.objects.create(
                user=self.user, account=self.bank, as_of_date=old - timedelta(days=(status == 'DRIFT')),
                account_balance=D('1'), ledger_balance=D('1'), drift_amount=D('0'), status=status,
            )
        call_command('reconcile_ledgers', user_id=self.user.id)
        remaining = set(LedgerReconciliationReport.objects.filter(account=self.bank, as_of_date__lt=self.today).values_list('status', flat=True))
        self.assertEqual(remaining, {'DRIFT'})


class MoneyExactFilterTest(TestCase):
    def test_sign_goes_before_the_symbol_and_paise_are_kept(self):
        from expenses.templatetags.digit_filters import money_exact, money_exact_signed

        self.assertEqual(money_exact(D('-450.00'), '₹'), '-₹450')
        self.assertEqual(money_exact(D('1234567.50'), '₹'), '₹12,34,567.50')
        self.assertEqual(money_exact(D('0.01'), '₹'), '₹0.01')
        self.assertEqual(money_exact(D('-1200'), '$'), '-$1,200')
        self.assertEqual(money_exact_signed(D('249'), '₹'), '+₹249')
        self.assertEqual(money_exact_signed(D('-249.50'), '₹'), '-₹249.50')
        self.assertEqual(money_exact_signed(D('0'), '₹'), '₹0')
