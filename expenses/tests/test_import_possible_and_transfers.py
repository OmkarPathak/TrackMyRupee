"""Phase 3 of the import: a row that looks like an expense already stored, and a row that moves money between the
person's own accounts, are not imported as new spending without the person's say-so."""
import csv as csvlib
import io
from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from expenses.ledger_service import LedgerPostingService
from expenses.models import Account, Expense, ImportBatch, Transfer

D = Decimal


def csv_file(rows, name='statement.csv'):
    out = io.StringIO()
    csvlib.writer(out).writerows(rows)
    return SimpleUploadedFile(name, out.getvalue().encode('utf-8'), content_type='text/csv')


HEADER = ['Date', 'Narration', 'Amount']


@override_settings(LEDGER_WRITE_ENABLED=True)
class Base(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(username='p3', password='pw')
        profile = self.user.profile
        profile.tier, profile.has_seen_tutorial, profile.currency = 'PRO', True, '₹'
        profile.save()
        self.client.force_login(self.user)
        self.bank = Account.objects.create(user=self.user, name='HDFC Savings', account_type='SAVINGS_ACCOUNT',
                                           balance=D('100000'), currency='₹')
        self.card = Account.objects.create(user=self.user, name='ICICI Amazon Card', account_type='CREDIT_CARD',
                                           balance=D('0'), currency='₹')
        for account in (self.bank, self.card):
            LedgerPostingService.post_opening_balance(account=account)

    def data(self, rows, **extra):
        data = {'account': self.bank.id, 'currency': '₹', 'file': csv_file(rows)}
        data.update(extra)
        return data

    def preview(self, rows, **extra):
        cache.clear()
        return self.client.post(reverse('upload-preview'), self.data(rows, **extra))

    def direct(self, rows):
        cache.clear()
        response = self.client.post(reverse('upload'), self.data(rows))
        return response.context['results']

    def confirm(self, rows, tick=None):
        """The preview, then the Import button with the rows ticked (all ticked by default as shown)."""
        preview = self.preview(rows)
        decisions = preview.context['rows']
        ticks = [d.row_id for d in decisions if d.status in ('new', 'transfer')]
        ticks += [d.row_id for d in decisions if d.row_id in (tick or ())]
        cache.clear()
        response = self.client.post(reverse('upload'), self.data(
            rows, shown=preview.context['shown_ids'], include=ticks, file_hash=preview.context['analysis'].file_hash))
        return preview, response.context['results']

    def stored(self, text='Swiggy dinner', amount=300, day=1, account=None):
        return Expense.objects.create(user=self.user, date=date(2026, 9, day), amount=D(str(amount)), description=text,
                                      category='Food', currency='₹', account=account or self.bank)

    def statuses(self, preview):
        return {d.row.description: d.status for d in preview.context['rows']}


class PossibleDuplicateTests(Base):
    def test_a_row_that_looks_like_a_typed_expense_is_flagged_not_imported(self):
        self.stored()
        preview = self.preview([HEADER, ['2026-09-02', 'UPI-SWIGGY-9876543210@ybl', 300]])
        decision = preview.context['rows'][0]
        self.assertEqual(decision.status, 'possible')
        self.assertIn('Swiggy dinner', decision.reason)
        self.assertEqual(preview.context['counts']['possible'], 1)
        self.assertEqual(Expense.objects.count(), 1)
        html = preview.content.decode()
        self.assertIn('Possible duplicate', html)
        self.assertNotIn(f'value="{decision.row_id}" checked', html)            # unticked

    def test_ticking_it_in_the_preview_imports_it(self):
        self.stored()
        rows = [HEADER, ['2026-09-02', 'UPI-SWIGGY-9876543210@ybl', 300]]
        _preview, res = self.confirm(rows, tick={'0-2'})
        self.assertEqual((res['created_count'], res['possible_skipped']), (1, 0))
        self.assertEqual(Expense.objects.filter(description__startswith='UPI-SWIGGY').count(), 1)

    def test_leaving_it_unticked_does_not(self):
        self.stored()
        _preview, res = self.confirm([HEADER, ['2026-09-02', 'UPI-SWIGGY-1@ybl', 300]])
        self.assertEqual((res['created_count'], res['possible_skipped']), (0, 1))
        self.assertEqual(Expense.objects.count(), 1)

    def test_an_import_with_no_preview_leaves_it_out_and_says_so(self):
        self.stored()
        res = self.direct([HEADER, ['2026-09-02', 'UPI-SWIGGY-1@ybl', 300], ['2026-09-03', 'Zomato', 120]])
        self.assertEqual((res['created_count'], res['possible_skipped']), (1, 1))
        self.assertEqual(Expense.objects.filter(description='Zomato').count(), 1)

    def test_it_is_listed_in_the_skipped_rows_download(self):
        self.stored()
        res = self.direct([HEADER, ['2026-09-02', 'UPI-SWIGGY-1@ybl', 300], ['2026-09-03', 'Zomato', 120]])
        body = self.client.get(reverse('import-report', args=[res['batch_uuid']])).content.decode('utf-8-sig')
        table = list(csvlib.reader(io.StringIO(body)))
        self.assertEqual([r[4] for r in table[1:]], ['Possible duplicate'])
        self.assertIn('Swiggy dinner', table[1][5])

    def test_each_stored_expense_accounts_for_one_row_only(self):
        self.stored()
        preview = self.preview([HEADER, ['2026-09-01', 'UPI-SWIGGY-1@ybl', 300], ['2026-09-02', 'SWIGGY ORDER', 300]])
        self.assertEqual([d.status for d in preview.context['rows']], ['possible', 'new'])

    def test_an_exact_match_uses_up_the_stored_expense_before_a_fuzzy_one_can(self):
        self.stored()
        preview = self.preview([HEADER, ['2026-09-02', 'UPI-SWIGGY-1@ybl', 300], ['2026-09-01', 'Swiggy dinner', 300]])
        self.assertEqual([d.status for d in preview.context['rows']], ['new', 'duplicate'])

    def test_close_but_not_close_enough_is_just_new(self):
        self.stored()
        rows = [HEADER,
                ['2026-09-01', 'UPI-RAHUL-1@ybl', 300],      # same amount and day, no word in common
                ['2026-09-01', 'Swiggy dinner', 301],         # one rupee off
                ['2026-09-05', 'Swiggy dinner', 300]]         # four days away
        self.assertEqual([d.status for d in self.preview(rows).context['rows']], ['new', 'new', 'new'])

    def test_an_expense_on_another_account_is_not_a_candidate(self):
        self.stored(account=self.card)
        self.assertEqual(self.preview([HEADER, ['2026-09-02', 'UPI-SWIGGY-1@ybl', 300]]).context['rows'][0].status, 'new')

    def test_a_deleted_expense_is_not_a_candidate(self):
        self.stored().delete()
        self.assertEqual(self.preview([HEADER, ['2026-09-02', 'UPI-SWIGGY-1@ybl', 300]]).context['rows'][0].status, 'new')


class TransferTests(Base):
    BILL = [HEADER, ['2026-09-05', 'ICICI AMAZON CARD CC PAYMENT', 5000], ['2026-09-06', 'Zomato', 120]]

    def test_a_credit_card_bill_is_proposed_as_a_transfer_to_that_card(self):
        preview = self.preview(self.BILL)
        decision = preview.context['rows'][0]
        self.assertEqual((decision.status, decision.target), ('transfer', self.card))
        self.assertTrue(decision.apply)
        self.assertEqual(preview.context['counts']['transfer'], 1)
        html = preview.content.decode()
        self.assertIn('ICICI Amazon Card', html)
        self.assertIn(f'value="{decision.row_id}" checked', html)               # ticked: it is what is wanted
        self.assertEqual((Expense.objects.count(), Transfer.objects.count()), (0, 0))

    def test_confirming_records_a_transfer_not_an_expense(self):
        _preview, res = self.confirm(self.BILL)
        self.assertEqual((res['transfer_count'], res['created_count']), (1, 1))
        transfer = Transfer.objects.get()
        self.assertEqual((transfer.from_account, transfer.to_account, transfer.amount, transfer.date),
                         (self.bank, self.card, D('5000'), date(2026, 9, 5)))
        self.assertEqual(list(Expense.objects.values_list('description', flat=True)), ['Zomato'])
        batch = ImportBatch.objects.get()
        self.assertEqual((batch.transfer_count, batch.created_count), (1, 1))
        self.assertEqual(transfer.import_batch, batch)

    def test_the_transfer_moves_the_balances_like_one_typed_in(self):
        self.confirm(self.BILL)
        self.bank.refresh_from_db()
        self.card.refresh_from_db()
        self.assertEqual(self.bank.balance, D('100000') - D('5000') - D('120'))
        self.assertEqual(self.card.balance, D('5000'))

    def test_unticking_it_leaves_it_out_entirely(self):
        preview = self.preview(self.BILL)
        shown = preview.context['shown_ids']
        zomato = preview.context['rows'][1].row_id
        cache.clear()
        res = self.client.post(reverse('upload'), self.data(self.BILL, shown=shown, include=[zomato])).context['results']
        self.assertEqual((res['transfer_count'], res['created_count'], res['excluded_count']), (0, 1, 1))
        self.assertEqual(Transfer.objects.count(), 0)

    def test_an_import_with_no_preview_never_guesses_a_transfer(self):
        res = self.direct(self.BILL)
        self.assertEqual((res['transfer_count'], res['transfer_skipped'], res['created_count']), (0, 1, 1))
        self.assertEqual(Transfer.objects.count(), 0)
        self.assertEqual(Expense.objects.filter(description__contains='PAYMENT').count(), 0)
        body = self.client.get(reverse('import-report', args=[res['batch_uuid']])).content.decode('utf-8-sig')
        self.assertIn('preview the file to record it as a transfer', body)

    def test_a_transfer_already_recorded_is_not_recorded_or_counted_as_spending_again(self):
        Transfer.objects.create(user=self.user, from_account=self.bank, to_account=self.card, amount=D('5000'),
                                date=date(2026, 9, 4), description='card bill')
        preview = self.preview(self.BILL)
        self.assertEqual(preview.context['rows'][0].status, 'duplicate')
        self.assertIn('Already recorded as a transfer', preview.context['rows'][0].reason)
        _p, res = self.confirm(self.BILL)
        self.assertEqual((Transfer.objects.count(), res['transfer_count']), (1, 0))

    def test_an_existing_transfer_to_a_different_account_does_not_hide_the_row(self):
        other = Account.objects.create(user=self.user, name='Piggy', account_type='SAVINGS_ACCOUNT', balance=0, currency='₹')
        Transfer.objects.create(user=self.user, from_account=self.bank, to_account=other, amount=D('5000'),
                                date=date(2026, 9, 5))
        self.assertEqual(self.preview(self.BILL).context['rows'][0].status, 'transfer')

    def test_a_transfer_word_without_one_of_your_accounts_is_just_spending(self):
        preview = self.preview([HEADER, ['2026-09-05', 'NEFT TRANSFER TO RAHUL SHARMA', 2500]])
        self.assertEqual(preview.context['rows'][0].status, 'new')

    def test_an_account_in_another_currency_is_not_a_target(self):
        self.card.currency = '$'
        self.card.save()
        self.assertEqual(self.preview(self.BILL).context['rows'][0].status, 'new')

    def test_the_account_being_imported_into_is_never_its_own_target(self):
        self.assertEqual(self.preview([HEADER, ['2026-09-05', 'HDFC SAVINGS SELF TRANSFER', 100]]).context['rows'][0].status, 'new')


class UndoAndHistoryTests(Base):
    def test_undo_removes_the_transfer_too_and_restores_both_balances(self):
        self.confirm(TransferTests.BILL)
        batch = ImportBatch.objects.get()
        response = self.client.post(reverse('import-undo', args=[batch.uuid]), follow=True)
        self.assertTrue(any('was undone' in str(m) for m in response.context['messages']))
        self.assertEqual((Transfer.objects.count(), Expense.objects.count()), (0, 0))
        self.bank.refresh_from_db()
        self.card.refresh_from_db()
        self.assertEqual((self.bank.balance, self.card.balance), (D('100000'), D('0')))
        batch.refresh_from_db()
        self.assertEqual(batch.status, ImportBatch.UNDONE)

    def test_a_partial_undo_counts_transfers_as_work_left(self):
        from expenses.importing.undo import undo_batch
        self.confirm(TransferTests.BILL)
        batch = ImportBatch.objects.get()
        self.assertEqual(undo_batch(batch, budget_seconds=0), (1, 1))
        self.assertEqual(undo_batch(batch, budget_seconds=0), (1, 0))
        self.assertEqual(Transfer.objects.count(), 0)

    def test_the_history_shows_the_transfers_an_import_recorded(self):
        self.confirm(TransferTests.BILL)
        html = self.client.get(reverse('import-history')).content.decode()
        self.assertIn('+1', html)
        self.assertIn('transfers', html)

    def test_only_the_batch_s_own_transfers_are_undone(self):
        mine = Transfer.objects.create(user=self.user, from_account=self.bank, to_account=self.card, amount=D('700'),
                                       date=date(2026, 8, 1))
        self.confirm(TransferTests.BILL)
        self.client.post(reverse('import-undo', args=[ImportBatch.objects.get().uuid]))
        self.assertEqual(list(Transfer.objects.values_list('pk', flat=True)), [mine.pk])
