"""Phase 4 of the import: money coming in can be imported as income, if the person asks for it."""
import csv as csvlib
import io
from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from expenses.ledger_read_service import LedgerReadService
from expenses.ledger_service import LedgerPostingService
from expenses.models import Account, Expense, FinancialAuditLog, ImportBatch, Income, JournalEntry, Transfer

D = Decimal
HEADER = ['Date', 'Narration', 'Amount']
BANK_HEADER = ['Date', 'Narration', 'Withdrawal Amt.', 'Deposit Amt.']       # money in is in its own column
STATEMENT = [
    HEADER,
    ['2026-09-01', 'UPI-ZOMATO-ORDER', -350],
    ['2026-09-03', 'NEFT-ACME CORP-SALARY SEPTEMBER', 85000],
    ['2026-09-09', 'INT.PD SAVINGS 01-07 TO 30-09', 312.40],
    ['2026-09-10', 'UPI-AMAZON-REFUND ORDER 4412', 799],
    ['2026-09-12', 'UPI-RAHUL SHARMA-9876@ybl', 1500],
]


def csv_file(rows, name='statement.csv'):
    out = io.StringIO()
    csvlib.writer(out).writerows(rows)
    return SimpleUploadedFile(name, out.getvalue().encode('utf-8'), content_type='text/csv')


@override_settings(LEDGER_WRITE_ENABLED=True)
class Base(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(username='p4', password='pw')
        profile = self.user.profile
        profile.tier, profile.has_seen_tutorial, profile.currency = 'PRO', True, '₹'
        profile.save()
        self.client.force_login(self.user)
        self.bank = Account.objects.create(user=self.user, name='HDFC Savings', account_type='SAVINGS_ACCOUNT',
                                           balance=D('1000'), currency='₹')
        self.card = Account.objects.create(user=self.user, name='ICICI Amazon Card', account_type='CREDIT_CARD',
                                           balance=D('0'), currency='₹')
        for account in (self.bank, self.card):
            LedgerPostingService.post_opening_balance(account=account)

    def data(self, rows, **extra):
        data = {'account': self.bank.id, 'currency': '₹', 'file': csv_file(rows)}
        data.update(extra)
        return data

    def preview(self, rows, income=True, **extra):
        cache.clear()
        if income:
            extra['import_income'] = '1'
        return self.client.post(reverse('upload-preview'), self.data(rows, **extra))

    def direct(self, rows, income=True, **extra):
        cache.clear()
        if income:
            extra['import_income'] = '1'
        return self.client.post(reverse('upload'), self.data(rows, **extra)).context['results']

    def confirm(self, rows, untick=(), types=None, income=True):
        preview = self.preview(rows, income=income)
        decisions = preview.context['rows']
        ticks = [d.row_id for d in decisions if d.status in ('new', 'transfer', 'income') and d.row_id not in untick]
        extra = {f'itype_{k}': v for k, v in (types or {}).items()}
        if income:
            extra['import_income'] = '1'
        cache.clear()
        response = self.client.post(reverse('upload'), self.data(
            rows, shown=preview.context['shown_ids'], include=ticks, file_hash=preview.context['analysis'].file_hash,
            **extra))
        return preview, response.context['results']

    def statuses(self, preview):
        return {d.row.description: d.status for d in preview.context['rows']}


class OffByDefaultTests(Base):
    def test_money_in_is_left_out_unless_asked_for(self):
        preview = self.preview(STATEMENT, income=False)
        self.assertEqual(preview.context['counts']['credit'], 4)
        self.assertEqual(preview.context['counts']['income'], 0)
        self.assertTrue(preview.context['has_money_in'])                   # so the switch is offered
        self.assertIn('id="import_income"', preview.content.decode())
        self.assertNotIn('id="import_income" name="import_income" value="1"\n                   checked', preview.content.decode())

    def test_an_import_without_the_switch_creates_no_income(self):
        res = self.direct(STATEMENT, income=False)
        self.assertEqual((res['created_count'], res['credit_skipped'], res['income_count']), (1, 4, 0))
        self.assertEqual(Income.objects.count(), 0)

    def test_no_switch_is_offered_when_nothing_came_in(self):
        preview = self.preview([HEADER, ['2026-09-01', 'Zomato', 350]], income=False)
        self.assertFalse(preview.context['has_money_in'])
        self.assertNotIn('id="import_income"', preview.content.decode())


class ImportingIncomeTests(Base):
    def test_money_in_becomes_income_rows_with_a_guessed_source(self):
        preview = self.preview(STATEMENT)
        types = {d.row.description: d.income_type for d in preview.context['rows'] if d.status == 'income'}
        self.assertEqual(types, {
            'NEFT-ACME CORP-SALARY SEPTEMBER': 'Salary',
            'INT.PD SAVINGS 01-07 TO 30-09': 'Investment Returns',
            'UPI-AMAZON-REFUND ORDER 4412': 'Refund / Reimbursement',
            'UPI-RAHUL SHARMA-9876@ybl': 'Other',
        })
        self.assertEqual(preview.context['counts']['income'], 4)
        self.assertEqual((Income.objects.count(), ImportBatch.objects.count()), (0, 0))        # nothing saved yet
        self.assertIn('checked', preview.content.decode().split('id="import_income"')[1].split('>')[0])

    def test_confirming_saves_them_like_income_typed_in(self):
        _preview, res = self.confirm(STATEMENT)
        self.assertEqual((res['created_count'], res['income_count']), (1, 4))
        self.assertEqual(res['income_total'], 85000 + 312.40 + 799 + 1500)
        salary = Income.objects.get(description__contains='SALARY')
        self.assertEqual((salary.source_type, salary.source, salary.amount, salary.date, salary.account, salary.currency),
                         ('Salary', 'Salary', D('85000'), date(2026, 9, 3), self.bank, '₹'))
        self.assertEqual(salary.base_amount, D('85000'))
        batch = ImportBatch.objects.get()
        self.assertEqual((batch.income_count, batch.created_count), (4, 1))
        self.assertEqual(Income.objects.filter(import_batch=batch).count(), 4)

    def test_the_account_is_credited_and_the_ledger_agrees(self):
        self.confirm(STATEMENT)
        self.bank.refresh_from_db()
        self.assertEqual(self.bank.balance, D('1000') - D('350') + D('85000') + D('312.40') + D('799') + D('1500'))
        self.assertEqual(LedgerReadService.get_account_balance(self.bank), self.bank.balance)
        self.assertEqual(JournalEntry.objects.filter(source_type='INCOME').count(), 4)

    def test_the_source_can_be_chosen_per_row(self):
        preview = self.preview(STATEMENT)
        other = next(d for d in preview.context['rows'] if d.row.description.startswith('UPI-RAHUL'))
        again = self.preview(STATEMENT, **{f'itype_{other.row_id}': 'Freelance / Consulting'})       # "Check again" keeps it
        self.assertIn('<option value="Freelance / Consulting" selected>', again.content.decode())
        self.confirm(STATEMENT, types={other.row_id: 'Freelance / Consulting'})
        income = Income.objects.get(description__startswith='UPI-RAHUL')
        self.assertEqual((income.source_type, income.source), ('Freelance / Consulting', 'Freelance / Consulting'))

    def test_a_made_up_source_is_ignored(self):
        preview = self.preview(STATEMENT)
        salary = next(d for d in preview.context['rows'] if 'SALARY' in d.row.description)
        self.confirm(STATEMENT, types={salary.row_id: 'Lottery Winnings'})
        self.assertEqual(Income.objects.get(description__contains='SALARY').source_type, 'Salary')

    def test_unticked_money_in_is_not_imported(self):
        preview = self.preview(STATEMENT)
        refund = next(d for d in preview.context['rows'] if 'REFUND' in d.row.description)
        _p, res = self.confirm(STATEMENT, untick={refund.row_id})
        self.assertEqual((res['income_count'], res['excluded_count']), (3, 1))
        self.assertFalse(Income.objects.filter(description__contains='REFUND').exists())

    def test_an_import_with_no_preview_can_bring_income_in_too(self):
        res = self.direct(STATEMENT)
        self.assertEqual((res['created_count'], res['income_count']), (1, 4))

    def test_income_is_not_held_to_the_plans_monthly_expense_limit(self):
        from django.utils import timezone
        today = timezone.localdate().isoformat()
        rows = [HEADER, [today, 'Zomato', -100], [today, 'NEFT-ACME-SALARY', 5000]]
        with patch('finance_tracker.plans.get_limit', return_value=0):
            res = self.direct(rows)
        self.assertEqual((res['created_count'], res['limit_skipped'], res['income_count']), (0, 1, 1))


class AlreadyThereTests(Base):
    def test_importing_the_same_statement_again_adds_nothing(self):
        self.confirm(STATEMENT)
        res = self.direct(STATEMENT)
        self.assertEqual((res['created_count'], res['income_count'], res['duplicate_count']), (0, 0, 5))
        self.assertEqual(Income.objects.count(), 4)

    def test_income_typed_in_by_hand_is_not_imported_again(self):
        Income.objects.create(user=self.user, date=date(2026, 9, 3), amount=D('85000'), description='neft-acme corp-salary  september',
                              source='Salary', source_type='Salary', currency='₹', account=self.bank)
        statuses = self.statuses(self.preview(STATEMENT))
        self.assertEqual(statuses['NEFT-ACME CORP-SALARY SEPTEMBER'], 'duplicate')

    def test_income_that_looks_like_one_already_typed_is_a_possible_duplicate(self):
        Income.objects.create(user=self.user, date=date(2026, 9, 11), amount=D('799'), description='Amazon refund',
                              source='Refund / Reimbursement', source_type='Refund / Reimbursement', currency='₹',
                              account=self.bank)
        preview = self.preview(STATEMENT)
        decision = next(d for d in preview.context['rows'] if 'REFUND' in d.row.description)
        self.assertEqual(decision.status, 'possible')
        self.assertIn('Amazon refund', decision.reason)
        _p, res = self.confirm(STATEMENT)
        self.assertEqual(res['possible_skipped'], 1)
        self.assertFalse(Income.objects.filter(description__contains='REFUND ORDER').exists())

    def test_ticking_the_possible_duplicate_imports_it(self):
        Income.objects.create(user=self.user, date=date(2026, 9, 11), amount=D('799'), description='Amazon refund',
                              source='Refund / Reimbursement', source_type='Refund / Reimbursement', currency='₹',
                              account=self.bank)
        preview = self.preview(STATEMENT)
        refund = next(d for d in preview.context['rows'] if 'REFUND' in d.row.description)
        ticks = [d.row_id for d in preview.context['rows'] if d.status in ('income',)] + [refund.row_id]
        cache.clear()
        res = self.client.post(reverse('upload'), self.data(
            STATEMENT, shown=preview.context['shown_ids'], include=ticks, import_income='1',
            file_hash=preview.context['analysis'].file_hash)).context['results']
        self.assertTrue(Income.objects.filter(description__contains='REFUND ORDER').exists())
        self.assertEqual(res['possible_skipped'], 0)

    def test_an_expense_does_not_stand_in_for_income_of_the_same_amount(self):
        Expense.objects.create(user=self.user, date=date(2026, 9, 3), amount=D('85000'), description='NEFT-ACME CORP-SALARY SEPTEMBER',
                               category='Other', currency='₹', account=self.bank)
        self.assertEqual(self.statuses(self.preview(STATEMENT))['NEFT-ACME CORP-SALARY SEPTEMBER'], 'income')


class FromOwnAccountsTests(Base):
    CARD_REFUND = [BANK_HEADER, ['2026-09-05', 'ICICI AMAZON CARD SELF TRANSFER', '', 2000],
                   ['2026-09-06', 'NEFT-ACME-SALARY', '', 5000]]

    def test_money_from_your_own_account_is_a_transfer_not_income(self):
        preview = self.preview(self.CARD_REFUND)
        decision = preview.context['rows'][0]
        self.assertEqual((decision.status, decision.target, decision.incoming), ('transfer', self.card, True))
        self.assertIn('Looks like a transfer from ICICI Amazon Card', decision.reason)
        self.assertEqual(preview.context['rows'][1].status, 'income')

    def test_confirming_records_it_from_that_account_into_this_one(self):
        _p, res = self.confirm(self.CARD_REFUND)
        transfer = Transfer.objects.get()
        self.assertEqual((transfer.from_account, transfer.to_account, transfer.amount), (self.card, self.bank, D('2000')))
        self.assertEqual((res['transfer_count'], res['income_count']), (1, 1))
        self.assertEqual(list(Income.objects.values_list('amount', flat=True)), [D('5000')])

    def test_a_transfer_already_recorded_the_other_way_round_is_not_income(self):
        Transfer.objects.create(user=self.user, from_account=self.card, to_account=self.bank, amount=D('2000'),
                                date=date(2026, 9, 4))
        decision = self.preview(self.CARD_REFUND).context['rows'][0]
        self.assertEqual(decision.status, 'duplicate')
        self.assertIn('Already recorded as a transfer', decision.reason)

    def test_without_a_preview_it_is_left_out_not_guessed(self):
        res = self.direct(self.CARD_REFUND)
        self.assertEqual((res['transfer_count'], res['transfer_skipped'], res['income_count']), (0, 1, 1))
        self.assertEqual(Transfer.objects.count(), 0)


class UndoAndParityTests(Base):
    def test_undo_removes_the_income_and_puts_the_balance_back(self):
        self.confirm(STATEMENT)
        batch = ImportBatch.objects.get()
        self.client.post(reverse('import-undo', args=[batch.uuid]))
        self.assertEqual((Income.objects.count(), Expense.objects.count()), (0, 0))
        self.bank.refresh_from_db()
        self.assertEqual(self.bank.balance, D('1000'))
        self.assertEqual(LedgerReadService.get_account_balance(self.bank), D('1000'))
        batch.refresh_from_db()
        self.assertEqual(batch.status, ImportBatch.UNDONE)

    def test_a_partial_undo_counts_income_as_work_left(self):
        from expenses.importing.undo import remaining_items, undo_batch
        self.confirm(STATEMENT)
        batch = ImportBatch.objects.get()
        self.assertEqual(remaining_items(batch), 5)
        self.assertEqual(undo_batch(batch, budget_seconds=0), (1, 4))
        while undo_batch(batch, budget_seconds=0)[1]:
            pass
        self.assertEqual(Income.objects.count(), 0)

    def test_the_history_shows_the_income_an_import_recorded(self):
        self.confirm(STATEMENT)
        html = self.client.get(reverse('import-history')).content.decode()
        self.assertIn('+4', html)
        self.assertIn('income', html)

    def test_imported_income_leaves_the_traces_typed_income_does(self):
        other = Account.objects.create(user=self.user, name='Typed', account_type='SAVINGS_ACCOUNT', balance=D('1000'), currency='₹')
        LedgerPostingService.post_opening_balance(account=other)
        Income.objects.create(user=self.user, date=date(2026, 9, 3), amount=D('85000'), description='Salary',
                              source='Salary', source_type='Salary', currency='₹', account=other)
        self.confirm([BANK_HEADER, ['2026-09-03', 'NEFT-ACME-SALARY', '', 85000]])
        typed, imported = Income.objects.get(account=other), Income.objects.get(account=self.bank)
        other.refresh_from_db()
        self.bank.refresh_from_db()
        self.assertEqual(other.balance, self.bank.balance)
        self.assertEqual(LedgerReadService.get_account_balance(other), LedgerReadService.get_account_balance(self.bank))
        entries = lambda i: JournalEntry.objects.filter(source_type='INCOME', source_id=i.id).count()
        self.assertEqual((entries(typed), entries(imported)), (1, 1))
        logs = lambda i: list(FinancialAuditLog.objects.filter(model_name='Income', object_id=i.id).values_list('action', flat=True))
        self.assertEqual(logs(typed), logs(imported))
        self.assertEqual((typed.base_amount, typed.exchange_rate), (imported.base_amount, imported.exchange_rate))


class SwitchingItOnTests(Base):
    def test_flipping_the_switch_on_a_checked_preview_turns_money_in_into_income_not_left_out(self):
        # The first preview has no tick box on a money-in row. Asking again with the switch on must not read that
        # as "unticked": the browser posts back what that preview listed.
        first = self.preview(STATEMENT, income=False)
        shown = first.context['shown_ids']
        ticked = [d.row_id for d in first.context['rows'] if d.status == 'new']
        self.assertEqual(len(shown.split(',')), 1)                       # only the spending row had a tick box
        cache.clear()
        second = self.client.post(reverse('upload-preview'), self.data(
            STATEMENT, shown=shown, include=ticked, import_income='1'))
        self.assertEqual(second.context['counts']['income'], 4)
        self.assertEqual(second.context['counts']['excluded'], 0)
        self.assertEqual({d.status for d in second.context['rows']}, {'new', 'income'})

    def test_unticking_a_row_still_survives_a_check_again(self):
        first = self.preview(STATEMENT)
        refund = next(d for d in first.context['rows'] if 'REFUND' in d.row.description)
        ticked = [d.row_id for d in first.context['rows'] if d.status in ('new', 'income') and d.row_id != refund.row_id]
        cache.clear()
        again = self.client.post(reverse('upload-preview'), self.data(
            STATEMENT, shown=first.context['shown_ids'], include=ticked, import_income='1'))
        self.assertEqual(again.context['counts']['excluded'], 1)

    def test_the_page_re_hides_the_original_select_after_an_htmx_swap(self):
        # htmx puts the server's attributes back on an element that kept its id once a swap has settled, which
        # un-hid the <select> a searchable-select widget had replaced, so two dropdowns showed after "Check again".
        source = open('static/js/app_base.js', encoding='utf-8').read()
        settle = source[source.index("document.addEventListener('htmx:afterSettle', function(e) {\n            var root"):]
        self.assertIn("select[data-searchable-init]", settle[:400])
        self.assertIn("sel.style.display = 'none'", settle[:500])
