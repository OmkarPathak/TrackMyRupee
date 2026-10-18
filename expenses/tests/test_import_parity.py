"""An imported expense must leave the same traces as one typed in by hand: the account balance, the ledger
entry, the audit log and the cleared caches. Imports use bulk_create, which skips all of that unless done by hand."""
from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from expenses.ledger_read_service import LedgerReadService
from expenses.ledger_service import LedgerPostingService
from expenses.models import Account, Expense, FinancialAuditLog, JournalEntry

D = Decimal


@override_settings(LEDGER_WRITE_ENABLED=True)
class ImportParityTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(username='parity', password='pw')
        profile = self.user.profile
        profile.tier, profile.has_seen_tutorial, profile.currency = 'PRO', True, '₹'
        profile.save()
        self.client.force_login(self.user)
        self.typed = Account.objects.create(user=self.user, name='Typed', account_type='BANK', balance=D('10000'), currency='₹')
        self.imported = Account.objects.create(user=self.user, name='Imported', account_type='BANK', balance=D('10000'), currency='₹')
        for account in (self.typed, self.imported):
            LedgerPostingService.post_opening_balance(account=account)

    def type_in(self):
        Expense.objects.create(user=self.user, date=date(2026, 9, 1), amount=D('250.50'), description='Lunch',
                               category='Food', currency='₹', account=self.typed)

    def import_(self):
        csv = 'Date,Description,Amount\n2026-09-01,Lunch,250.50\n'
        return self.client.post(reverse('upload'), {
            'account': self.imported.id, 'currency': '₹', 'file': SimpleUploadedFile('s.csv', csv.encode())})

    def test_balance_ledger_and_audit_log_match_a_typed_expense(self):
        self.type_in()
        self.import_()
        typed, imported = Expense.objects.get(account=self.typed), Expense.objects.get(account=self.imported)
        self.typed.refresh_from_db()
        self.imported.refresh_from_db()
        self.assertEqual(self.typed.balance, self.imported.balance)
        self.assertEqual(LedgerReadService.get_account_balance(self.typed), LedgerReadService.get_account_balance(self.imported))
        entries = lambda e: JournalEntry.objects.filter(source_type='EXPENSE', source_id=e.id).count()
        self.assertEqual((entries(typed), entries(imported)), (1, 1))
        logs = lambda e: list(FinancialAuditLog.objects.filter(model_name='Expense', object_id=e.id).values_list('action', 'diff'))
        self.assertEqual(logs(typed), logs(imported))
        self.assertEqual(logs(imported)[0][0], 'CREATE')

    def test_the_audit_log_row_belongs_to_the_user_and_the_expense(self):
        self.import_()
        expense = Expense.objects.get()
        log = FinancialAuditLog.objects.get(model_name='Expense', object_id=expense.id)
        self.assertEqual((log.user_id, log.object_uuid, log.action), (self.user.id, expense.uuid, 'CREATE'))

    def test_the_same_caches_are_cleared_as_for_a_typed_expense(self):
        keys = [f'filter_merchants:{self.user.id}', f'filter_categories:{self.user.id}',
                f'interest_summary_{self.user.id}_None_None',
                f'home_default_data_{self.user.id}', f'account_net_worth_{self.user.id}',
                f'budget_dashboard_{self.user.id}']

        def warm():
            for key in keys:
                cache.set(key, 'stale')

        warm()
        self.type_in()
        cleared_by_typing = {k for k in keys if cache.get(k) is None}
        warm()
        self.import_()
        cleared_by_import = {k for k in keys if cache.get(k) is None}
        self.assertEqual(cleared_by_import, cleared_by_typing)
        self.assertTrue(cleared_by_import)

    def test_a_foreign_currency_import_converts_like_a_typed_one(self):
        from expenses.models import FXRate
        FXRate.objects.update_or_create(from_currency='USD', to_currency='INR', as_of_date=date.today(),
                                        defaults={'rate': D('80'), 'source': 't'})
        cache.clear()
        csv = 'Date,Description,Amount\n2026-09-01,Hosting,10\n'
        self.client.post(reverse('upload'), {'account': self.imported.id, 'currency': '$',
                                             'file': SimpleUploadedFile('s.csv', csv.encode())})
        imported = Expense.objects.get()
        typed = Expense.objects.create(user=self.user, date=date(2026, 9, 1), amount=D('10'), description='Hosting 2',
                                       category='Other', currency='$', account=self.typed)
        self.assertEqual((imported.base_amount, imported.exchange_rate), (typed.base_amount, typed.exchange_rate))
        self.imported.refresh_from_db()
        self.typed.refresh_from_db()
        self.assertEqual(self.imported.balance, self.typed.balance)
