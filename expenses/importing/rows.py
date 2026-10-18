"""From raw sheet rows to typed transaction rows, then deciding which are money out (expenses) and which money in.

Money direction is the hard part of a statement. In order of trust it is read from:
  1. separate Debit / Credit (Withdrawal / Deposit) columns,
  2. a Dr/Cr column, or a "Dr"/"Cr" written after the amount,
  3. the running Balance column: a debit lowers the balance, a credit raises it (works for oldest-first and
     newest-first files alike),
  4. only then the sign of a single amount column, using the words in the narration (salary, refund, interest...)
     to tell which sign is the credits, and the sign most rows have when the words say nothing.
"""
import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import List, Optional

from django.utils.translation import gettext as _

from .layout import Layout, data_rows, is_footer_or_repeat
from .values import MAX_AMOUNT, parse_amount, parse_date

DEBIT, CREDIT = 'debit', 'credit'
_TOLERANCE = Decimal('0.01')

_DEBIT_WORDS = {'DR', 'D', 'DEBIT', 'WITHDRAWAL', 'W', 'PAID', 'OUT', 'DEBITED'}
_CREDIT_WORDS = {'CR', 'C', 'CREDIT', 'DEPOSIT', 'RECEIVED', 'IN', 'CREDITED'}
_CREDIT_NARRATION = re.compile(
    r'(?i)\b(salary|payroll|stipend|interest|int\.?\s?pd|refund|reversal|cashback|cash back|dividend|received|'
    r'deposit|credited|bonus|reimburse\w*)\b')


@dataclass
class ParsedRow:
    number: int
    sheet: int = 0
    date: Optional[date] = None
    amount: Decimal = Decimal('0')              # always positive
    signed: Optional[Decimal] = None            # as written, when it came from a single amount column
    hint: Optional[str] = None                  # DEBIT or CREDIT when the file says so outright
    direction: Optional[str] = None
    description: str = ''
    category: Optional[str] = None
    reference: str = ''
    balance: Optional[Decimal] = None
    error: Optional[str] = None


def date_cells(rows, layout: Layout):
    index = layout.columns.get('date')
    if index is None:
        return []
    return [row[index] for _number, row in data_rows(rows, layout) if index < len(row)]


def _cell(row, layout, role):
    index = layout.columns.get(role)
    return row[index] if index is not None and index < len(row) else None


def _hint_from_type(value):
    if value is None:
        return None
    word = re.sub(r'[^A-Za-z]', '', str(value)).upper()
    if word in _DEBIT_WORDS:
        return DEBIT
    if word in _CREDIT_WORDS:
        return CREDIT
    return None


def _marker_direction(marker):
    return {'DR': DEBIT, 'CR': CREDIT}.get(marker)


def parse_rows(rows, layout: Layout, day_first) -> List[ParsedRow]:
    """Every transaction row of a sheet. Rows with no amount (blank lines, balance-only lines) are left out;
    a row that has an amount but cannot be read comes back with `error` set."""
    parsed: List[ParsedRow] = []
    for number, row in data_rows(rows, layout):
        if is_footer_or_repeat(row, layout):
            continue
        item = ParsedRow(number=number)

        amount = parse_amount(_cell(row, layout, 'amount'))
        debit = parse_amount(_cell(row, layout, 'debit'))
        credit = parse_amount(_cell(row, layout, 'credit'))
        raw_amount = _cell(row, layout, 'amount')
        raw_text = '' if raw_amount is None else str(raw_amount).strip()

        if 'debit' in layout.columns or 'credit' in layout.columns:
            out = abs(debit.value) if debit else Decimal('0')
            into = abs(credit.value) if credit else Decimal('0')
            if out and into:
                item.error = _("Both a debit and a credit amount")
            elif out:
                item.amount, item.hint = out, DEBIT
            elif into:
                item.amount, item.hint = into, CREDIT
            elif amount and amount.value:
                item.amount, item.signed = abs(amount.value), amount.value
                item.hint = _marker_direction(amount.marker)
            else:
                continue                        # nothing in either column: a blank or balance-only line
        elif amount is not None and amount.value:
            item.amount, item.signed = abs(amount.value), amount.value
            item.hint = _marker_direction(amount.marker)
        elif amount is None and raw_text not in ('', '0', '0.0', '0.00'):
            item.error = _("Invalid amount format: ") + raw_text
        else:
            continue                            # an empty or zero amount is not a transaction

        type_hint = _hint_from_type(_cell(row, layout, 'type'))
        if type_hint:
            item.hint = type_hint

        date_cell = _cell(row, layout, 'date')
        item.date = parse_date(date_cell, day_first)
        if item.error is None and not item.date:
            item.error = _("Invalid date format: ") + str(date_cell)
        if item.error is None and item.amount >= MAX_AMOUNT:
            item.error = _("Amount is too large: ") + (raw_text or str(item.amount))

        desc = _cell(row, layout, 'description')
        item.description = str(desc).strip() if desc is not None else ''
        if item.error is None and not item.description:
            item.error = _("Missing description")

        category = _cell(row, layout, 'category')
        item.category = str(category).strip() if category not in (None, '') else None
        reference = _cell(row, layout, 'reference')
        item.reference = str(reference).strip() if reference not in (None, '') else ''
        balance = parse_amount(_cell(row, layout, 'balance'))
        if balance is not None:
            item.balance = -balance.value if balance.marker == 'DR' else balance.value
        parsed.append(item)
    return parsed


# ---------------------------------------------------------------------------------------------------------------
# Money direction
# ---------------------------------------------------------------------------------------------------------------

def _from_balance(rows):
    """Debit or credit from how the running balance moves. Needs a balance on most rows and amounts that agree
    with the balance changes, otherwise it does nothing."""
    seq = [r for r in rows if r.balance is not None and r.amount and not r.error]
    if len(seq) < 3:
        return
    pairs = list(zip(seq, seq[1:]))
    forward = [abs(abs(cur.balance - prev.balance) - cur.amount) <= _TOLERANCE for prev, cur in pairs]
    reverse = [abs(abs(prev.balance - cur.balance) - prev.amount) <= _TOLERANCE for prev, cur in pairs]
    best = max(sum(forward), sum(reverse))
    if best < max(2, 0.6 * len(pairs)):
        return
    if sum(forward) >= sum(reverse):            # oldest first: a row's effect is its balance minus the one above
        for (prev, cur), ok in zip(pairs, forward):
            if ok and cur.direction is None:
                cur.direction = DEBIT if cur.balance < prev.balance else CREDIT
    else:                                       # newest first: a row's effect is its balance minus the one below
        for (prev, cur), ok in zip(pairs, reverse):
            if ok and prev.direction is None:
                prev.direction = DEBIT if prev.balance < cur.balance else CREDIT


def _credit_share(rows):
    return sum(1 for r in rows if _CREDIT_NARRATION.search(r.description)) / len(rows) if rows else 0.0


def assign_directions(rows: List[ParsedRow], force=None):
    """Set `direction` on every readable row. Returns notes for the person to be told about (codes, see the view).

    `force` is the person's own answer for a single signed amount column: 'positive' means positive amounts are
    expenses, 'negative' the other way round. Rows the file itself marks as debit or credit keep what it says."""
    notes = []
    for row in rows:
        if row.hint and not row.error:
            row.direction = row.hint
    if force in ('positive', 'negative'):
        expense_sign = 1 if force == 'positive' else -1
        for row in rows:
            if row.direction is None and not row.error:
                row.direction = DEBIT if row.signed is None or (1 if row.signed > 0 else -1) == expense_sign else CREDIT
        return notes
    _from_balance(rows)

    rest = [r for r in rows if r.direction is None and not r.error]
    positive = [r for r in rest if r.signed and r.signed > 0]
    negative = [r for r in rest if r.signed and r.signed < 0]
    expense_sign = None
    if positive and negative:
        share_pos, share_neg = _credit_share(positive), _credit_share(negative)
        if share_pos >= 0.5 and share_pos - share_neg >= 0.4:
            expense_sign = -1                   # the words say the positive rows are the credits
        elif share_neg >= 0.5 and share_neg - share_pos >= 0.4:
            expense_sign = 1
        elif len(positive) != len(negative):
            expense_sign = 1 if len(positive) > len(negative) else -1
            notes.append('assumed_positive_are_expenses' if expense_sign == 1 else 'assumed_negative_are_expenses')
        # equal counts and no words to go on: not guessed, everything stays an expense
    for row in rest:
        if expense_sign is None or row.signed is None or (1 if row.signed > 0 else -1) == expense_sign:
            row.direction = DEBIT
        else:
            row.direction = CREDIT
    return notes
