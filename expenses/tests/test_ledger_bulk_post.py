"""Posting many expenses or income rows to the ledger at once must write exactly what posting them one at a time
writes: the import relies on it to stay fast (about nine queries a row became a handful a batch)."""
from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.cache import cache
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext

from expenses.ledger_service import LedgerPostingService
from expenses.models import (Account, Expense, FXRate, Income, JournalEntry, JournalLine, LedgerAccount,
                             LedgerPostingFailure)

D = Decimal


def shape(user, account):
    """Everything the ledger recorded, with ids and timestamps taken out so two runs can be compared."""
    entries = []
    for entry in JournalEntry.objects.filter(user=user).order_by('source_type', 'description', 'id'):
        lines = sorted(
            (l.ledger_account.code.split(':', 2)[2].replace(f':ACCOUNT:{account.id}', ':ACCOUNT:X'), l.direction,
             str(l.amount), l.currency, str(l.fx_rate_to_base), str(l.base_amount), l.account_ref_id is not None)
            for l in JournalLine.objects.filter(journal_entry=entry).select_related('ledger_account'))
        entries.append((entry.source_type, entry.description, sorted(entry.metadata), entry.metadata['shadow_action'],
                        entry.status, lines))
    balances = sorted(
        (a.code.split(':', 2)[2].replace(f':ACCOUNT:{account.id}', ':ACCOUNT:X'), str(a.cached_balance))
        for a in LedgerAccount.objects.filter(user=user))
    return entries, balances


@override_settings(LEDGER_WRITE_ENABLED=False)
class BulkPostTests(TestCase):
    def setUp(self):
        cache.clear()
        self.rows = [('Food', '120.50', 'Lunch'), ('Food', '80', 'Tea'), ('Travel', '300', 'Cab'), ('Food', '45.25', 'Snack'),
                     ('Rent', '15000', 'Rent'), ('Travel', '75.10', 'Bus')]

    def world(self, name, currency='₹'):
        user = User.objects.create_user(username=name, password='pw')
        user.profile.currency = '₹'
        user.profile.save()
        account = Account.objects.create(user=user, name='Bank', account_type='SAVINGS_ACCOUNT', balance=D('1000'), currency=currency)
        return user, account

    def expenses(self, user, account, currency='₹'):
        return [Expense.objects.create(user=user, date=date(2026, 9, 1 + i % 28), amount=D(a), description=d, category=c,
                                       currency=currency, account=account) for i, (c, a, d) in enumerate(self.rows)]

    def incomes(self, user, account):
        return [Income.objects.create(user=user, date=date(2026, 9, 1 + i), amount=D(a), description=d, source=c,
                                      source_type='Other', currency='₹', account=account)
                for i, (c, a, d) in enumerate([('Salary', '50000', 'Pay'), ('Other', '120', 'Gift'), ('Salary', '900', 'Bonus')])]

    def test_expenses_bulk_posted_equal_expenses_posted_one_by_one(self):
        one_user, one_account = self.world('one')
        bulk_user, bulk_account = self.world('bulk')
        singles, bulk = self.expenses(one_user, one_account), self.expenses(bulk_user, bulk_account)
        for e in singles:
            LedgerPostingService.shadow_post_expense_create(expense=e, version_token=f'CREATE-{e.id}')
        self.assertEqual(LedgerPostingService.bulk_post(bulk, kind='EXPENSE'), 6)
        self.assertEqual(shape(one_user, one_account), shape(bulk_user, bulk_account))
        self.assertEqual(JournalEntry.objects.filter(user=bulk_user).count(), 6)
        self.assertEqual(JournalLine.objects.filter(journal_entry__user=bulk_user).count(), 12)

    def test_income_bulk_posted_equals_income_posted_one_by_one(self):
        one_user, one_account = self.world('one')
        bulk_user, bulk_account = self.world('bulk')
        singles, bulk = self.incomes(one_user, one_account), self.incomes(bulk_user, bulk_account)
        for i in singles:
            LedgerPostingService.shadow_post_income_create(income=i, version_token=f'CREATE-{i.id}')
        self.assertEqual(LedgerPostingService.bulk_post(bulk, kind='INCOME'), 3)
        self.assertEqual(shape(one_user, one_account), shape(bulk_user, bulk_account))

    def test_a_foreign_currency_row_posts_the_same_either_way(self):
        FXRate.objects.update_or_create(from_currency='USD', to_currency='INR', as_of_date=date.today(),
                                        defaults={'rate': D('80'), 'source': 't'})
        FXRate.objects.update_or_create(from_currency='INR', to_currency='USD', as_of_date=date.today(),
                                        defaults={'rate': D('0.0125'), 'source': 't'})
        cache.clear()
        one_user, one_account = self.world('one')
        bulk_user, bulk_account = self.world('bulk')
        singles, bulk = self.expenses(one_user, one_account, '$'), self.expenses(bulk_user, bulk_account, '$')
        for e in singles:
            LedgerPostingService.shadow_post_expense_create(expense=e, version_token=f'CREATE-{e.id}')
        LedgerPostingService.bulk_post(bulk, kind='EXPENSE')
        self.assertEqual(shape(one_user, one_account), shape(bulk_user, bulk_account))

    def test_running_it_twice_posts_nothing_the_second_time(self):
        user, account = self.world('twice')
        expenses = self.expenses(user, account)
        self.assertEqual(LedgerPostingService.bulk_post(expenses, kind='EXPENSE'), 6)
        before = shape(user, account)
        self.assertEqual(LedgerPostingService.bulk_post(expenses, kind='EXPENSE'), 0)
        self.assertEqual(shape(user, account), before)

    def test_nothing_to_post(self):
        self.assertEqual(LedgerPostingService.bulk_post([], kind='EXPENSE'), 0)

    def test_a_row_with_no_account_is_refused_and_nothing_is_written(self):
        user, account = self.world('noacct')
        expenses = self.expenses(user, account)
        expenses[2].account = None
        from django.core.exceptions import ValidationError
        with self.assertRaises(ValidationError):
            LedgerPostingService.bulk_post(expenses, kind='EXPENSE')
        self.assertEqual(JournalEntry.objects.filter(user=user).count(), 0)

    def test_the_number_of_queries_does_not_grow_with_the_rows(self):
        counts = {}
        for n in (10, 60):
            user, account = self.world(f'queries{n}')
            self.rows = [('Food', str(10 + i), f'row {i}') for i in range(n)]
            expenses = self.expenses(user, account)
            with CaptureQueriesContext(connection) as ctx:
                LedgerPostingService.bulk_post(expenses, kind='EXPENSE')
            counts[n] = len(ctx)
        self.assertLessEqual(abs(counts[60] - counts[10]), 2, 'bulk posting must not cost queries per row')
        self.assertLess(counts[60], 25)


@override_settings(LEDGER_WRITE_ENABLED=True)
class ImportFallsBackTests(TestCase):
    def test_if_bulk_posting_fails_the_import_posts_row_by_row_and_still_succeeds(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        from django.urls import reverse
        user = User.objects.create_user(username='fallback', password='pw')
        user.profile.tier, user.profile.has_seen_tutorial, user.profile.currency = 'PRO', True, '₹'
        user.profile.save()
        self.client.force_login(user)
        account = Account.objects.create(user=user, name='Bank', account_type='SAVINGS_ACCOUNT', balance=D('1000'), currency='₹')
        LedgerPostingService.post_opening_balance(account=account)
        csv = 'Date,Description,Amount\n2026-09-01,Tea,50\n2026-09-02,Bus,30\n'
        with patch.object(LedgerPostingService, 'bulk_post', side_effect=RuntimeError('boom')):
            cache.clear()
            res = self.client.post(reverse('upload'), {'account': account.id, 'currency': '₹',
                                                        'file': SimpleUploadedFile('s.csv', csv.encode())}).context['results']
        self.assertEqual(res['created_count'], 2)
        self.assertEqual(JournalEntry.objects.filter(user=user, source_type='EXPENSE').count(), 2)
        self.assertEqual(LedgerPostingFailure.objects.count(), 0)
