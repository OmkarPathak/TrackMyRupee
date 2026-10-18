"""The expense import: `analyse` reads a file and decides what would happen to every row without writing anything
(that is the preview), and `commit` writes it and records the import so it can be listed and undone.
`run_import` is the two together, for a direct import with no preview."""
import hashlib
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Dict, List, Optional

from django.db.models import Count, Q
from django.utils import timezone
from django.utils.translation import gettext as _

from ..models import Category, Expense, ImportBatch, get_exchange_rate
from .commit import clear_caches_after_import, save_batch
from .layout import Layout, apply_overrides, data_rows, detect_layout
from .parsing import MAX_IMPORT_ROWS, ImportFileError, read_sheets
from .rows import CREDIT, ParsedRow, assign_directions, date_cells, parse_rows
from .values import detect_day_first

BATCH_SIZE = 200

NEW, DUPLICATE, CREDIT_ROW, ERROR, LIMIT, EXCLUDED = 'new', 'duplicate', 'credit', 'error', 'limit', 'excluded'


class TooManyRows(ImportFileError):
    pass


@dataclass
class ImportOptions:
    """What the person chose in the preview. Everything defaults to "work it out"."""
    columns: Dict[str, int] = field(default_factory=dict)   # role -> column index, -1 meaning "not used"
    sign_mode: str = 'auto'                                 # 'auto', 'positive' or 'negative' (which sign is spending)
    date_order: str = 'auto'                                # 'auto', 'day' or 'month'


@dataclass
class RowDecision:
    row: ParsedRow
    status: str
    reason: str = ''
    category: str = ''
    expense: Optional[Expense] = None

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


def _existing_identities(user, account, currency, first_date, last_date):
    """How many expenses already exist for each (date, amount, description) in the file's date range. A manual
    entry or an earlier import counts the same, so a re-imported file adds nothing however it was first entered."""
    rows = (
        Expense.objects.filter(user=user, is_deleted=False, currency=currency, date__range=(first_date, last_date))
        .filter(Q(account=account) | Q(account__isnull=True))
        .values_list('date', 'amount', 'description')
    )
    return Counter((d, a, normalise_description(desc)) for d, a, desc in rows)


def _key(*parts):
    return hashlib.md5('_'.join(str(p) for p in parts).encode('utf-8')).hexdigest()


def file_digest(uploaded_file):
    uploaded_file.seek(0)
    digest = hashlib.sha256(uploaded_file.read()).hexdigest()
    uploaded_file.seek(0)
    return digest


def analyse(*, user, uploaded_file, file_name, account, currency, predict_category, plan_cap=None,
            max_rows=MAX_IMPORT_ROWS, options=None, excluded=frozenset()) -> Optional[Analysis]:
    """Decide what would happen to every row of the file. Writes nothing. Returns None when no columns could be
    recognised; raises ImportFileError (or TooManyRows) for a file that cannot be used at all."""
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

    dated = [r.date for r in parsed if r.date and not r.error]
    existing_keys = set()
    existing_identity = Counter()
    if dated:
        existing_keys = set(
            Expense.objects.filter(user=user, client_dedup_key__isnull=False, date__range=(min(dated), max(dated)))
            .values_list('client_dedup_key', flat=True))
        existing_identity = _existing_identities(user, account, currency, min(dated), max(dated))
    seen_keys = set()
    occurrences = Counter()

    history_map = _history_map(user)
    category_names = list(Category.objects.filter(user=user).values_list('name', flat=True))

    base_currency = user.profile.currency
    exchange_rate = Decimal('1.0') if currency == base_currency else get_exchange_rate(currency, base_currency)

    decisions: List[RowDecision] = []
    for row in parsed:
        if row.error:
            decisions.append(RowDecision(row, ERROR, row.error))
            continue
        if row.direction == CREDIT:
            decisions.append(RowDecision(row, CREDIT_ROW, _("Money coming in, not spending")))
            continue

        category = (row.category or predict_category(
            row.description, user=user, history_map=history_map, categories=category_names) or 'Other')[:255]

        identity = (row.date, row.amount, normalise_description(row.description))
        if row.reference:
            key = _key('ref', row.date, row.amount, currency, row.reference)
            duplicate = key in existing_keys or key in seen_keys
        else:
            # Identical rows in one file are separate payments (two ₹50 teas), so they are told apart by how
            # many came before, and a row is a duplicate only when that many already exist in the database.
            occurrences[identity] += 1
            duplicate = occurrences[identity] <= existing_identity[identity]
            n = occurrences[identity]
            key = _key(row.date, row.amount, currency, identity[2], n)
            while key in existing_keys or key in seen_keys:
                n += 1
                key = _key(row.date, row.amount, currency, identity[2], n)
        if duplicate:
            decisions.append(RowDecision(row, DUPLICATE, _("Already imported or entered"), category))
            continue
        if f'{row.sheet}-{row.number}' in excluded:
            decisions.append(RowDecision(row, EXCLUDED, _("Left out by you"), category))
            continue

        # The plan's monthly cap counts only the current month, like adding expenses one at a time
        if remaining_this_month is not None and row.date.year == today.year and row.date.month == today.month:
            if remaining_this_month <= 0:
                decisions.append(RowDecision(row, LIMIT, _("Over your plan's monthly expense limit"), category))
                continue
            remaining_this_month -= 1

        seen_keys.add(key)
        expense = Expense(
            user=user, date=row.date, amount=row.amount,
            base_amount=row.amount if currency == base_currency else (row.amount * exchange_rate).quantize(Decimal('0.01')),
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
        'duplicate_count': 0,
        'limit_skipped': 0,
        'credit_skipped': 0,
        'excluded_count': 0,
        'error_count': 0,
        'errors': [],                       # the first few, with row numbers (the rest are in the batch's report)
        'total_amount': 0,
        'currency_symbol': currency,
        'notes': [],                        # codes the view turns into messages
        'batch_uuid': None,
    }


def _add_error(summary, row, reason):
    if len(summary['errors']) < 15:
        summary['errors'].append({'row': row, 'reason': reason})
    summary['error_count'] += 1


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
        if decision.status in (ERROR, DUPLICATE, CREDIT_ROW, LIMIT):
            skipped.append({
                'row': decision.row.number, 'status': decision.status, 'reason': decision.reason,
                'date': decision.row.date.isoformat() if decision.row.date else '',
                'description': decision.row.description, 'amount': str(decision.row.amount),
            })

    to_write = [d.expense for d in analysis.decisions if d.status == NEW]
    batch = None
    if to_write:
        batch = ImportBatch.objects.create(user=user, account=account, file_name=file_name[:255],
                                           file_hash=analysis.file_hash, currency=currency)
        for expense in to_write:
            expense.import_batch = batch
        account_rate = Decimal('1.0')
        if account and currency != account.currency:
            account_rate = get_exchange_rate(currency, account.currency)
        for start in range(0, len(to_write), BATCH_SIZE):
            save_batch(to_write[start:start + BATCH_SIZE], account=account, account_rate=account_rate, summary=summary)

    if summary['created_count'] and batch:
        batch.total_rows = summary['total_rows']
        batch.created_count = summary['created_count']
        batch.duplicate_count = summary['duplicate_count']
        batch.credit_count = summary['credit_skipped']
        batch.error_count = summary['error_count']
        batch.limit_count = summary['limit_skipped']
        batch.excluded_count = summary['excluded_count']
        batch.total_amount = Decimal(str(round(summary['total_amount'], 2)))
        batch.report = skipped
        batch.save()
        summary['batch_uuid'] = str(batch.uuid)
        clear_caches_after_import(user.id)
    elif batch:
        batch.delete()                       # nothing was written: there is nothing to list or undo
    return summary


def run_import(*, user, uploaded_file, file_name, account, currency, predict_category, plan_cap=None,
               max_rows=MAX_IMPORT_ROWS, options=None, excluded=frozenset()):
    """Import one file in one go. Returns the summary dict, or None when no columns could be recognised."""
    analysis = analyse(user=user, uploaded_file=uploaded_file, file_name=file_name, account=account,
                       currency=currency, predict_category=predict_category, plan_cap=plan_cap,
                       max_rows=max_rows, options=options, excluded=excluded)
    if analysis is None:
        return None
    return commit(analysis, user=user, account=account, currency=currency, file_name=file_name)
