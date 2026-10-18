"""The statement import: `analyse` reads a file and decides what would happen to every row without writing anything
(that is the preview), and `commit` writes it and records the import so it can be listed and undone.
`run_import` is the two together, for a direct import with no preview."""
import hashlib
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal
from typing import Dict, List, Optional

from django.db import transaction
from django.db.models import Count, Q
from django.utils import timezone
from django.utils.translation import gettext as _

from ..models import Account, Category, Expense, ImportBatch, Income, Transfer, get_exchange_rate
from .commit import clear_caches_after_import, save_batch, save_income_batch
from .layout import Layout, apply_overrides, data_rows, detect_layout
from .matching import (DATE_WINDOW_DAYS, Candidate, account_names, best_match, find_transfer_target, guess_income_type,
                       looks_like_transfer, meaningful_tokens)
from .parsing import MAX_IMPORT_ROWS, ImportFileError, read_sheets
from .rows import CREDIT, ParsedRow, assign_directions, date_cells, parse_rows
from .values import detect_day_first

BATCH_SIZE = 200

NEW, DUPLICATE, CREDIT_ROW, ERROR, LIMIT, EXCLUDED = 'new', 'duplicate', 'credit', 'error', 'limit', 'excluded'
POSSIBLE, TRANSFER = 'possible', 'transfer'     # need a person's say-so: shown unticked / shown with its account
INCOME = 'income'                               # money in, imported as income because the person asked for that


class TooManyRows(ImportFileError):
    pass


@dataclass
class ImportOptions:
    """What the person chose in the preview. Everything defaults to "work it out"."""
    columns: Dict[str, int] = field(default_factory=dict)   # role -> column index, -1 meaning "not used"
    sign_mode: str = 'auto'                                 # 'auto', 'positive' or 'negative' (which sign is spending)
    date_order: str = 'auto'                                # 'auto', 'day' or 'month'
    import_income: bool = False                             # bring money in as income instead of leaving it out
    income_types: Dict[str, str] = field(default_factory=dict)   # row id -> Income source type chosen by hand


@dataclass
class RowDecision:
    row: ParsedRow
    status: str
    reason: str = ''
    category: str = ''
    expense: Optional[Expense] = None
    income: Optional[Income] = None
    income_type: str = ''
    match: str = ''                         # the stored row a possible duplicate resembles
    target: Optional[Account] = None        # the own account a transfer row moves money to (or from, if incoming)
    incoming: bool = False                  # a transfer INTO the imported account (a credit), not out of it
    apply: bool = False                     # a transfer row that will actually be recorded
    key: str = ''

    @property
    def row_id(self):
        return f'{self.row.sheet}-{self.row.number}'


@dataclass
class SheetInfo:
    layout: Layout
    sample: List[list]


@dataclass
class Analysis:
    decisions: List[RowDecision]
    notes: List[str]
    sheets: List[SheetInfo]
    day_first: Optional[bool]
    file_hash: str

    def count(self, status):
        return sum(1 for d in self.decisions if d.status == status)

    @property
    def new_total(self):
        return sum((d.row.amount for d in self.decisions if d.status == NEW), Decimal('0'))


def normalise_description(text):
    return ' '.join(str(text).lower().split())


def _identity(row):
    return (row.date, row.amount, normalise_description(row.description))


def _history_map(user):
    exact, prefix = defaultdict(lambda: defaultdict(int)), defaultdict(lambda: defaultdict(int))
    records = (Expense.objects.filter(user=user).values('description', 'category')
               .annotate(cnt=Count('id')).order_by('-cnt'))
    for record in records:
        desc, cat, count = (record['description'] or '').strip(), record['category'], record['cnt']
        if desc and cat:
            lowered = desc.lower()
            exact[lowered][cat] += count
            words = lowered.split()
            if words and len(words[0]) > 3:
                prefix[words[0]][cat] += count
    pick = lambda cats: max(cats.items(), key=lambda kv: kv[1])[0]
    return {'exact': {d: pick(c) for d, c in exact.items()}, 'prefix': {w: pick(c) for w, c in prefix.items()}}


def _key(*parts):
    return hashlib.md5('_'.join(str(p) for p in parts).encode('utf-8')).hexdigest()


def file_digest(uploaded_file):
    uploaded_file.seek(0)
    digest = hashlib.sha256(uploaded_file.read()).hexdigest()
    uploaded_file.seek(0)
    return digest


class _Stored:
    """What is already stored for one kind of row (expenses, or income), which this file's rows of that kind are
    compared with: how many identical rows exist (a manual entry or an earlier import counts the same, so a
    re-imported file adds nothing however it was first entered), and the rest as candidates for a fuzzy match."""

    def __init__(self, model, *, user, account, currency, rows, first, last, prefix=''):
        window = timedelta(days=DATE_WINDOW_DAYS)
        self.prefix = (prefix,) if prefix else ()
        self.keys = set(model.objects.filter(user=user, client_dedup_key__isnull=False, date__range=(first, last))
                        .values_list('client_dedup_key', flat=True))
        stored = list(
            model.objects.filter(user=user, is_deleted=False, currency=currency,
                                 date__range=(first - window, last + window))
            .filter(Q(account=account) | Q(account__isnull=True))
            .values_list('id', 'date', 'amount', 'description'))
        self.identity = Counter((d, a, normalise_description(desc)) for _pk, d, a, desc in stored if first <= d <= last)
        # What an exact match already accounts for is not available to a fuzzy one
        file_counts = Counter(_identity(r) for r in rows if not r.reference)
        consumed = Counter({k: min(v, self.identity[k]) for k, v in file_counts.items()})
        self.pool: List[Candidate] = []
        for pk, date, amount, description in stored:
            if consumed[(date, amount, normalise_description(description))] > 0:
                consumed[(date, amount, normalise_description(description))] -= 1
                continue
            self.pool.append(Candidate(pk, date, amount, description, meaningful_tokens(description)))
        self.seen = set()
        self.occurrences = Counter()

    def judge(self, row, currency):
        """(the row's dedup key, whether it is already stored)."""
        identity = _identity(row)
        if row.reference:
            key = _key(*self.prefix, 'ref', row.date, row.amount, currency, row.reference)
            return key, key in self.keys or key in self.seen
        # Identical rows in one file are separate payments (two ₹50 teas), so they are told apart by how many came
        # before, and a row is a duplicate only when that many already exist in the database.
        self.occurrences[identity] += 1
        duplicate = self.occurrences[identity] <= self.identity[identity]
        n = self.occurrences[identity]
        key = _key(*self.prefix, row.date, row.amount, currency, identity[2], n)
        while key in self.keys or key in self.seen:
            n += 1
            key = _key(*self.prefix, row.date, row.amount, currency, identity[2], n)
        return key, duplicate

    def resembles(self, row):
        return best_match(row.date, row.amount, meaningful_tokens(row.description), self.pool) if self.pool else None


class _TransferPool:
    """The transfers already recorded out of (or into) the imported account, so a row that is one of them is not
    recorded again, or counted as spending (or income)."""

    def __init__(self, user, account, *, incoming, first, last):
        window = timedelta(days=DATE_WINDOW_DAYS)
        mine, other = ('to_account', 'from_account_id') if incoming else ('from_account', 'to_account_id')
        self.items = [
            {'amount': amount, 'date': date, 'other': other_id, 'used': False}
            for amount, date, other_id in Transfer.objects.filter(
                user=user, is_deleted=False, date__range=(first - window, last + window), **{mine: account}
            ).values_list('amount', 'date', other)]

    def take(self, row, target_id):
        for item in self.items:
            if (not item['used'] and item['amount'] == row.amount
                    and abs((item['date'] - row.date).days) <= DATE_WINDOW_DAYS
                    and (target_id is None or item['other'] == target_id)):
                item['used'] = True
                return True
        return False


def analyse(*, user, uploaded_file, file_name, account, currency, predict_category, plan_cap=None,
            max_rows=MAX_IMPORT_ROWS, options=None, excluded=frozenset(), included=frozenset(),
            reviewed=True) -> Optional[Analysis]:
    """Decide what would happen to every row of the file. Writes nothing. Returns None when no columns could be
    recognised; raises ImportFileError (or TooManyRows) for a file that cannot be used at all.

    `excluded` are rows the person unticked; `included` are the ones they ticked, which is what turns a possible
    duplicate into an import. `reviewed` is False for an import with no preview: nobody has looked at the
    possible duplicates and the detected transfers, so those are left alone (and reported) rather than guessed."""
    options = options or ImportOptions()
    digest = file_digest(uploaded_file)
    sheets = read_sheets(uploaded_file, file_name, max_rows)

    laid_out = []
    for rows in sheets:
        layout = detect_layout(rows)
        if layout:
            layout = apply_overrides(layout, options.columns)
        if layout:
            laid_out.append((rows, layout))
    if sum(len(data_rows(rows, layout)) for rows, layout in laid_out) > max_rows:
        raise TooManyRows(_("This file has more than 5,000 rows. Split it into smaller files and import them one at a time."))
    if not any(data_rows(rows, layout) for rows, layout in laid_out):
        return None

    if options.date_order == 'day':
        day_first = True
    elif options.date_order == 'month':
        day_first = False
    else:
        day_first = detect_day_first([c for rows, layout in laid_out for c in date_cells(rows, layout)])

    parsed, notes, infos = [], [], []
    for sheet_number, (rows, layout) in enumerate(laid_out):
        sheet_rows = parse_rows(rows, layout, day_first)
        for row in sheet_rows:
            row.sheet = sheet_number
        force = options.sign_mode if options.sign_mode in ('positive', 'negative') else None
        for note in assign_directions(sheet_rows, force=force):
            if note not in notes:
                notes.append(note)
        parsed.extend(sheet_rows)
        infos.append(SheetInfo(layout=layout, sample=[r for _n, r in data_rows(rows, layout)[:3]]))

    today = timezone.localdate()
    remaining_this_month = None
    if plan_cap is not None and plan_cap != -1:
        used = Expense.objects.filter(user=user, date__year=today.year, date__month=today.month).count()
        remaining_this_month = max(0, plan_cap - used)

    readable = [r for r in parsed if not r.error]
    dated = [r.date for r in readable if r.date]
    spending_rows = [r for r in readable if r.direction != CREDIT]
    money_in_rows = [r for r in readable if r.direction == CREDIT]
    expenses = incomes = transfers_out = transfers_in = None
    own_accounts = []
    if dated:
        first, last = min(dated), max(dated)
        expenses = _Stored(Expense, user=user, account=account, currency=currency, rows=spending_rows,
                           first=first, last=last)
        if options.import_income and money_in_rows:
            incomes = _Stored(Income, user=user, account=account, currency=currency, rows=money_in_rows,
                              first=first, last=last, prefix='income')
        if account.currency == currency:
            own_accounts = account_names(
                Account.objects.filter(user=user, is_active=True, currency=currency).exclude(pk=account.pk))
            transfers_out = _TransferPool(user, account, incoming=False, first=first, last=last)
            if incomes is not None:
                transfers_in = _TransferPool(user, account, incoming=True, first=first, last=last)
    own_by_id = {a.id: a for a in Account.objects.filter(user=user, pk__in=[a.id for a in own_accounts])}

    history_map = _history_map(user)
    category_names = list(Category.objects.filter(user=user).values_list('name', flat=True))

    base_currency = user.profile.currency
    exchange_rate = Decimal('1.0') if currency == base_currency else get_exchange_rate(currency, base_currency)

    def base_amount(row):
        return row.amount if currency == base_currency else (row.amount * exchange_rate).quantize(Decimal('0.01'))

    def money_in(row, row_id):
        """A credit the person asked to have imported as income."""
        source_type = options.income_types.get(row_id) or guess_income_type(row.description)
        key, duplicate = incomes.judge(row, currency)
        if duplicate:
            return RowDecision(row, DUPLICATE, _("Already imported or entered"), income_type=source_type)
        target_id = find_transfer_target(row.description, own_accounts) if own_accounts else None
        if transfers_in and (target_id or looks_like_transfer(row.description)) and transfers_in.take(row, target_id):
            return RowDecision(row, DUPLICATE, _("Already recorded as a transfer"), income_type=source_type)
        if target_id:
            target = own_by_id[target_id]
            if row_id in excluded:
                return RowDecision(row, EXCLUDED, _("Left out by you"), income_type=source_type, target=target, incoming=True)
            return RowDecision(
                row, TRANSFER, _("Looks like a transfer from %(account)s") % {'account': target.name},
                income_type=source_type, target=target, incoming=True, apply=reviewed,
                key=_key('transfer', row.date, row.amount, currency, target_id, account.pk, key))
        resembles = incomes.resembles(row)
        if resembles:
            resembles.used = True
            if row_id not in included:
                what = f"{resembles.date:%d %b %Y} · {resembles.description[:40]}"
                return RowDecision(row, POSSIBLE, _("Looks like income you already have: %(what)s") % {'what': what},
                                   income_type=source_type, match=what)
        if row_id in excluded:
            return RowDecision(row, EXCLUDED, _("Left out by you"), income_type=source_type)
        incomes.seen.add(key)
        income = Income(
            user=user, date=row.date, amount=row.amount, base_amount=base_amount(row), exchange_rate=exchange_rate,
            description=row.description, source=source_type, source_type=source_type, currency=currency,
            account=account, client_dedup_key=key)
        return RowDecision(row, INCOME, '', income_type=source_type, income=income)

    decisions: List[RowDecision] = []
    for row in parsed:
        row_id = f'{row.sheet}-{row.number}'
        if row.error:
            decisions.append(RowDecision(row, ERROR, row.error))
            continue
        if row.direction == CREDIT:
            if incomes is not None:
                decisions.append(money_in(row, row_id))
            else:
                decisions.append(RowDecision(row, CREDIT_ROW, _("Money coming in, not spending")))
            continue

        category = (row.category or predict_category(
            row.description, user=user, history_map=history_map, categories=category_names) or 'Other')[:255]

        key, duplicate = expenses.judge(row, currency)
        if duplicate:
            decisions.append(RowDecision(row, DUPLICATE, _("Already imported or entered"), category))
            continue

        # Money moved between the person's own accounts: a transfer, never an expense
        target_id = find_transfer_target(row.description, own_accounts) if own_accounts else None
        if transfers_out and (target_id or looks_like_transfer(row.description)) and transfers_out.take(row, target_id):
            decisions.append(RowDecision(row, DUPLICATE, _("Already recorded as a transfer"), category))
            continue
        if target_id:
            target = own_by_id[target_id]
            if row_id in excluded:
                decisions.append(RowDecision(row, EXCLUDED, _("Left out by you"), category, target=target))
            else:
                reason = _("Looks like a transfer to %(account)s") % {'account': target.name}
                decisions.append(RowDecision(row, TRANSFER, reason, category, target=target, apply=reviewed,
                                             key=_key('transfer', row.date, row.amount, currency, account.pk,
                                                      target_id, key)))
            continue

        # Probably an expense that is already there, typed in by hand with other words
        resembles = expenses.resembles(row)
        if resembles:
            resembles.used = True
            if row_id not in included:
                what = f"{resembles.date:%d %b %Y} · {resembles.description[:40]}"
                decisions.append(RowDecision(
                    row, POSSIBLE, _("Looks like an expense you already have: %(what)s") % {'what': what},
                    category, match=what))
                continue

        if row_id in excluded:
            decisions.append(RowDecision(row, EXCLUDED, _("Left out by you"), category))
            continue

        # The plan's monthly cap counts only the current month, like adding expenses one at a time
        if remaining_this_month is not None and row.date.year == today.year and row.date.month == today.month:
            if remaining_this_month <= 0:
                decisions.append(RowDecision(row, LIMIT, _("Over your plan's monthly expense limit"), category))
                continue
            remaining_this_month -= 1

        expenses.seen.add(key)
        expense = Expense(
            user=user, date=row.date, amount=row.amount, base_amount=base_amount(row),
            exchange_rate=exchange_rate, description=row.description, category=category,
            currency=currency, account=account, client_dedup_key=key,
        )
        expense._row_idx = row.number
        decisions.append(RowDecision(row, NEW, '', category, expense))

    return Analysis(decisions=decisions, notes=notes, sheets=infos, day_first=day_first, file_hash=digest)


def new_summary(currency):
    return {
        'total_rows': 0,
        'created_count': 0,
        'income_count': 0,                  # money in, recorded as income
        'transfer_count': 0,                # transfers between own accounts recorded
        'duplicate_count': 0,
        'possible_skipped': 0,              # looked like something already there, and nobody said to import it
        'transfer_skipped': 0,              # looked like a transfer, and nobody was there to confirm it
        'limit_skipped': 0,
        'credit_skipped': 0,
        'excluded_count': 0,
        'error_count': 0,
        'errors': [],                       # the first few, with row numbers (the rest are in the batch's report)
        'total_amount': 0,
        'income_total': 0,
        'currency_symbol': currency,
        'notes': [],                        # codes the view turns into messages
        'batch_uuid': None,
    }


def _add_error(summary, row, reason):
    if len(summary['errors']) < 15:
        summary['errors'].append({'row': row, 'reason': reason})
    summary['error_count'] += 1


def _record_transfers(decisions, *, user, account, batch, summary):
    """Record each confirmed transfer the normal way (Transfer.save posts balances and the ledger)."""
    for decision in decisions:
        row = decision.row
        source, destination = (decision.target, account) if decision.incoming else (account, decision.target)
        transfer = Transfer(
            user=user, from_account=source, to_account=destination, amount=row.amount, date=row.date,
            description=row.description[:1000], client_dedup_key=decision.key, import_batch=batch)
        try:
            with transaction.atomic():
                transfer.save()
            summary['transfer_count'] += 1
        except Exception as error:                      # one bad row must not spoil the rest
            _add_error(summary, row.number, str(getattr(error, 'message', None) or error))


def commit(analysis: Analysis, *, user, account, currency, file_name):
    """Write what `analyse` decided, and record the import. Returns the summary dict."""
    summary = new_summary(currency)
    summary['notes'] = list(analysis.notes)
    summary['total_rows'] = len(analysis.decisions)
    skipped = []
    for decision in analysis.decisions:
        if decision.status == ERROR:
            _add_error(summary, decision.row.number, decision.reason)
        elif decision.status == DUPLICATE:
            summary['duplicate_count'] += 1
        elif decision.status == CREDIT_ROW:
            summary['credit_skipped'] += 1
        elif decision.status == LIMIT:
            summary['limit_skipped'] += 1
        elif decision.status == EXCLUDED:
            summary['excluded_count'] += 1
        elif decision.status == POSSIBLE:
            summary['possible_skipped'] += 1
        elif decision.status == TRANSFER and not decision.apply:
            summary['transfer_skipped'] += 1
        left_out = decision.status in (ERROR, DUPLICATE, CREDIT_ROW, LIMIT, POSSIBLE) or (
            decision.status == TRANSFER and not decision.apply)
        if left_out:
            reason = decision.reason
            if decision.status == TRANSFER:
                reason = _("%(reason)s. It was not imported: preview the file to record it as a transfer.") % {
                    'reason': decision.reason}
            skipped.append({
                'row': decision.row.number, 'status': decision.status, 'reason': reason,
                'date': decision.row.date.isoformat() if decision.row.date else '',
                'description': decision.row.description, 'amount': str(decision.row.amount),
            })

    to_write = [d.expense for d in analysis.decisions if d.status == NEW]
    incomes = [d.income for d in analysis.decisions if d.status == INCOME]
    transfers = [d for d in analysis.decisions if d.status == TRANSFER and d.apply]
    batch = None
    if to_write or incomes or transfers:
        batch = ImportBatch.objects.create(user=user, account=account, file_name=file_name[:255],
                                           file_hash=analysis.file_hash, currency=currency)
        account_rate = Decimal('1.0')
        if account and currency != account.currency:
            account_rate = get_exchange_rate(currency, account.currency)
        for expense in to_write:
            expense.import_batch = batch
        for start in range(0, len(to_write), BATCH_SIZE):
            save_batch(to_write[start:start + BATCH_SIZE], account=account, account_rate=account_rate, summary=summary)
        for income in incomes:
            income.import_batch = batch
        for start in range(0, len(incomes), BATCH_SIZE):
            save_income_batch(incomes[start:start + BATCH_SIZE], account=account, account_rate=account_rate,
                              summary=summary)
        _record_transfers(transfers, user=user, account=account, batch=batch, summary=summary)

    if batch and (summary['created_count'] or summary['income_count'] or summary['transfer_count']):
        batch.total_rows = summary['total_rows']
        batch.created_count = summary['created_count']
        batch.income_count = summary['income_count']
        batch.transfer_count = summary['transfer_count']
        batch.duplicate_count = summary['duplicate_count']
        batch.credit_count = summary['credit_skipped']
        batch.error_count = summary['error_count']
        batch.limit_count = summary['limit_skipped']
        batch.excluded_count = summary['excluded_count']
        batch.possible_count = summary['possible_skipped']
        batch.total_amount = Decimal(str(round(summary['total_amount'], 2)))
        batch.report = skipped
        batch.save()
        summary['batch_uuid'] = str(batch.uuid)
        clear_caches_after_import(user.id)
    elif batch:
        batch.delete()                       # nothing was written: there is nothing to list or undo
    return summary


def run_import(*, user, uploaded_file, file_name, account, currency, predict_category, plan_cap=None,
               max_rows=MAX_IMPORT_ROWS, options=None, excluded=frozenset(), included=frozenset(), reviewed=False):
    """Import one file in one go. Returns the summary dict, or None when no columns could be recognised."""
    analysis = analyse(user=user, uploaded_file=uploaded_file, file_name=file_name, account=account,
                       currency=currency, predict_category=predict_category, plan_cap=plan_cap,
                       max_rows=max_rows, options=options, excluded=excluded, included=included, reviewed=reviewed)
    if analysis is None:
        return None
    return commit(analysis, user=user, account=account, currency=currency, file_name=file_name)
