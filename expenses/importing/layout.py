"""Working out which column of a statement is which, and which rows are transactions.

A header row is recognised by what each of its cells says (each cell is classified on its own, so "Transaction
Remarks" is a description and "Transaction Date" is a date). A file with no header falls back to guessing from the
data. Preamble above the header, repeated headers on later pages and footers such as "Closing Balance" are skipped.
"""
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .values import parse_amount, parse_date

HEADER_SCAN_ROWS = 60                   # a statement can start with a long block of account details
HEADERLESS_SCAN_ROWS = 20

ROLES = ('date', 'description', 'amount', 'debit', 'credit', 'balance', 'category', 'reference', 'type')

_FOOTER = re.compile(
    r'^\s*(opening balance|closing balance|total\b|grand total|statement summary|end of statement|'
    r'page \d|\*{3,}|-{3,}|brought forward|carried forward)', re.I)


def _words(text):
    return re.sub(r'[^a-z0-9]+', ' ', str(text).lower()).strip()


def classify_header(text):
    """The role one header cell plays, or None. Order matters: the specific kinds come before the general ones."""
    h = _words(text)
    if not h:
        return None
    w = set(h.split())
    if w & {'balance', 'bal'}:
        return 'balance'
    if ('dr' in w and 'cr' in w) or ('debit' in w and 'credit' in w) or h in (
            'transaction type', 'txn type', 'trans type', 'tran type', 'indicator', 'cr dr', 'dr cr'):
        return 'type'
    if w & {'ref', 'reference', 'utr', 'rrn', 'cheque', 'chq'} or h in (
            'transaction id', 'txn id', 'trans id', 'transaction no', 'txn no', 'transaction number'):
        return 'reference'
    if w & {'deposit', 'deposits', 'credit', 'credits', 'cr', 'received', 'receipt'} or 'paid in' in h or 'money in' in h:
        return 'credit'
    if w & {'withdrawal', 'withdrawals', 'withdraw', 'debit', 'debits', 'dr', 'spent'} or 'paid out' in h or 'money out' in h:
        return 'debit'
    if w & {'date', 'dt', 'time', 'dated', 'posted', 'posting'} or h == 'day':
        return 'value_date' if 'value' in w else 'date'
    if w & {'description', 'details', 'narration', 'narrative', 'particulars', 'remarks', 'memo', 'payee',
            'merchant', 'note', 'notes'}:
        return 'description'
    if w & {'amount', 'amt', 'total', 'cost', 'price', 'value'}:
        return 'amount'
    if w & {'category', 'tag', 'label'} or h in ('type', 'expense type'):
        return 'category'
    return None


@dataclass
class Layout:
    columns: Dict[str, int] = field(default_factory=dict)       # role -> column index
    start: int = 0                                              # index of the first row after the header
    has_header: bool = True
    header: List[str] = field(default_factory=list)             # the header cells as text, for showing them back
    width: int = 0                                              # how many columns the sheet has

    @property
    def has_money_columns(self):
        return 'amount' in self.columns or 'debit' in self.columns or 'credit' in self.columns


def _header_columns(row):
    columns = {}
    for index, cell in enumerate(row):
        role = classify_header(cell) if cell is not None else None
        if role and role not in columns:
            columns[role] = index
    if 'date' not in columns and 'value_date' in columns:
        columns['date'] = columns['value_date']
    columns.pop('value_date', None)
    return columns


def _qualifies(columns):
    money = 'amount' in columns or 'debit' in columns or 'credit' in columns
    return 'date' in columns and 'description' in columns and money


def _infer_columns(rows):
    """No header: guess from the first usable row. The date is the cell that reads as one, the amount the next
    number, and the description the longest remaining text."""
    for row in rows:
        if not row or not any(row):
            continue
        cells = [str(c).strip() if c is not None else '' for c in row]
        columns = {}
        for i, value in enumerate(cells):
            if parse_date(value):
                columns['date'] = i
                break
        for i, value in enumerate(cells):
            if i == columns.get('date') or parse_date(value):
                continue
            parsed = parse_amount(value)
            if parsed is not None and parsed.value:
                columns['amount'] = i
                break
        best, longest = None, -1
        for i, value in enumerate(cells):
            if i in columns.values():
                continue
            if len(value) > longest:
                best, longest = i, len(value)
        if best is not None:
            columns['description'] = best
        if len(columns) >= 2:
            return columns
    return {}


def detect_layout(rows) -> Optional[Layout]:
    """The layout of one sheet, or None when no columns can be made out."""
    for index, row in enumerate(rows[:HEADER_SCAN_ROWS]):
        if not row or not any(c not in (None, '') for c in row):
            continue
        columns = _header_columns(row)
        if _qualifies(columns):
            return Layout(columns=columns, start=index + 1, has_header=True,
                          header=['' if c is None else str(c).strip() for c in row], width=_width(rows))
    columns = _infer_columns(rows[:HEADERLESS_SCAN_ROWS])
    if columns:
        return Layout(columns=columns, start=0, has_header=False, width=_width(rows))
    return None


def _width(rows):
    return max((len(r) for r in rows[:HEADER_SCAN_ROWS + 40] if r), default=0)


def column_label(layout, index):
    """"B: Narration" for the column picker."""
    letter = ''
    n = index
    while True:
        letter = chr(ord('A') + n % 26) + letter
        n = n // 26 - 1
        if n < 0:
            break
    name = layout.header[index] if index < len(layout.header) else ''
    return f"{letter}: {name}" if name else letter


def apply_overrides(layout, overrides):
    """The layout with the person's own column choices applied: {role: column index}, or -1 for "not used".
    Returns None if what is left cannot make a transaction (no date, no money column or no description)."""
    if not overrides:
        return layout
    columns = dict(layout.columns)
    for role, index in overrides.items():
        if role not in ROLES:
            continue
        if index is None or index < 0:
            columns.pop(role, None)
        else:
            columns[role] = index
    if not _qualifies(columns):
        return None
    return Layout(columns=columns, start=layout.start, has_header=layout.has_header,
                  header=layout.header, width=layout.width)


def _is_header_row(row, layout):
    if not layout.has_header:
        return False
    return _header_columns(row) == layout.columns


def is_footer_or_repeat(row, layout):
    """A row that is not a transaction: a repeated header, or "Closing Balance"/"Total" and the like."""
    if _is_header_row(row, layout):
        return True
    first = next((str(c) for c in row if c not in (None, '')), '')
    if not _FOOTER.match(first):
        return False
    date_at = layout.columns.get('date')
    return parse_date(row[date_at] if date_at is not None and date_at < len(row) else None) is None


def data_rows(rows, layout):
    """(row number, row) for every non-empty row after the header; numbers start at 1 as in a spreadsheet."""
    out: List[tuple] = []
    for number, row in enumerate(rows[layout.start:], start=layout.start + 1):
        if row and any(v is not None and str(v).strip() for v in row):
            out.append((number, row))
    return out
