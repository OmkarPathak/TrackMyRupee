"""The expense import, start to finish: read the file, work out what each row is, skip what is already there,
and write the rest. The view only handles the request and the messages."""
import hashlib
from collections import Counter, defaultdict
from decimal import Decimal

from django.db.models import Count, Q
from django.utils import timezone
from django.utils.translation import gettext as _

from ..models import Category, Expense, get_exchange_rate
from .commit import clear_caches_after_import, save_batch
from .layout import data_rows, detect_layout
from .parsing import MAX_IMPORT_ROWS, ImportFileError, read_sheets
from .rows import CREDIT, assign_directions, date_cells, parse_rows
from .values import detect_day_first

BATCH_SIZE = 200


class TooManyRows(ImportFileError):
    pass


def new_summary(currency):
    return {
        'total_rows': 0,
        'created_count': 0,
        'duplicate_count': 0,
        'limit_skipped': 0,
        'credit_skipped': 0,
        'error_count': 0,
        'errors': [],                       # the first few, with row numbers
        'total_amount': 0,
        'currency_symbol': currency,
        'notes': [],                        # codes the view turns into messages
    }


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


def run_import(*, user, uploaded_file, file_name, account, currency, predict_category, plan_cap=None,
               max_rows=MAX_IMPORT_ROWS):
    """Import one file of expenses. Returns the summary dict, or None when no columns could be recognised.
    Raises ImportFileError (or TooManyRows) for a file that cannot be used at all."""
    sheets = read_sheets(uploaded_file, file_name, max_rows)

    laid_out = []
    for rows in sheets:
        layout = detect_layout(rows)
        if layout:
            laid_out.append((rows, layout))
    if sum(len(data_rows(rows, layout)) for rows, layout in laid_out) > max_rows:
        raise TooManyRows(_("This file has more than 5,000 rows. Split it into smaller files and import them one at a time."))
    if not any(data_rows(rows, layout) for rows, layout in laid_out):
        return None

    day_first = detect_day_first([c for rows, layout in laid_out for c in date_cells(rows, layout)])
    parsed, notes = [], []
    for rows, layout in laid_out:
        sheet_rows = parse_rows(rows, layout, day_first)
        for note in assign_directions(sheet_rows):
            if note not in notes:
                notes.append(note)
        parsed.extend(sheet_rows)

    summary = new_summary(currency)
    summary['notes'] = notes

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
    account_rate = Decimal('1.0')
    if account and currency != account.currency:
        account_rate = get_exchange_rate(currency, account.currency)

    batch = []
    for row in parsed:
        summary['total_rows'] += 1
        if row.error:
            _add_error(summary, row.number, row.error)
            continue
        if row.direction == CREDIT:
            summary['credit_skipped'] += 1
            continue

        category = row.category or predict_category(
            row.description, user=user, history_map=history_map, categories=category_names) or 'Other'
        category = category[:255]

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
            summary['duplicate_count'] += 1
            continue

        # The plan's monthly cap counts only the current month, like adding expenses one at a time
        if remaining_this_month is not None and row.date.year == today.year and row.date.month == today.month:
            if remaining_this_month <= 0:
                summary['limit_skipped'] += 1
                continue
            remaining_this_month -= 1

        seen_keys.add(key)
        item = Expense(
            user=user, date=row.date, amount=row.amount,
            base_amount=row.amount if currency == base_currency else (row.amount * exchange_rate).quantize(Decimal('0.01')),
            exchange_rate=exchange_rate, description=row.description, category=category,
            currency=currency, account=account, client_dedup_key=key,
        )
        item._row_idx = row.number
        batch.append(item)
        if len(batch) >= BATCH_SIZE:
            save_batch(batch, account=account, account_rate=account_rate, summary=summary)
            batch = []
    save_batch(batch, account=account, account_rate=account_rate, summary=summary)

    if summary['created_count']:
        clear_caches_after_import(user.id)
    return summary


def _add_error(summary, row, reason):
    if len(summary['errors']) < 15:
        summary['errors'].append({'row': row, 'reason': reason})
    summary['error_count'] += 1
