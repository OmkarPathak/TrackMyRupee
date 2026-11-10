"""Calculate statement-backed loans without writing payments to the cash ledger."""

from calendar import monthrange
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from django.db.models import prefetch_related_objects
from django.utils import timezone

from .models import Loan, LoanHistoryEntry

ZERO = Decimal('0.00')
CENT = Decimal('0.01')
MAX_INSTALLMENTS = 1200
Kind = LoanHistoryEntry.Kind


def money(value):
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def due_date(loan, number):
    """Return the date of the one-based installment using the original day."""
    anchor = loan.first_emi_date or loan.start_date
    offset = number - 1 if loan.first_emi_date else number
    year, month = divmod(anchor.year * 12 + anchor.month - 1 + offset, 12)
    month += 1
    return date(year, month, min(anchor.day, monthrange(year, month)[1]))


def is_due_date(loan, day):
    first = due_date(loan, 1)
    number = (day.year - first.year) * 12 + day.month - first.month + 1
    limit = MAX_INSTALLMENTS if loan.rate_reset == Loan.RateReset.KEEP_EMI else loan.duration_months
    return 1 <= number <= limit and due_date(loan, number) == day


def prefetch_statement_history(loans):
    statement_loans = [loan for loan in loans if loan.statement_history]
    if statement_loans:
        prefetch_related_objects(statement_loans, 'history_entries', 'interest_rates')


@dataclass
class HistoryRow:
    date: date
    status: str
    balance: Decimal
    amount: Decimal | None = None
    principal: Decimal | None = None
    interest: Decimal | None = None
    rate: Decimal | None = None
    unpaid_interest: Decimal = ZERO
    due_date: date | None = None
    entry: LoanHistoryEntry | None = None
    expected_amount: Decimal | None = None


@dataclass
class HistoryResult:
    balance: Decimal = ZERO
    unpaid_interest: Decimal = ZERO
    drawn: Decimal = ZERO
    principal_paid: Decimal = ZERO
    interest_paid: Decimal = ZERO
    rows: list[HistoryRow] = field(default_factory=list)
    invalid_entry: LoanHistoryEntry | None = None
    projected_balance: Decimal = ZERO
    projected_unpaid_interest: Decimal = ZERO

    @property
    def is_active(self):
        return self.drawn == ZERO or self.balance > ZERO or self.unpaid_interest > ZERO


def build_history(loan, as_of=None, *, project=False, entries=None, initial_rate=None):
    """Replay the dated events and monthly installments as of the requested day.

    A correction reserves its due date even if the payment happened later.
    Its money takes effect on its actual effective date.
    """
    from .services import LoanService

    today = timezone.localdate()
    cutoff = min(as_of or today, today)
    result = HistoryResult()
    if cutoff < loan.start_date and not project:
        return result

    if entries is None:
        entries = loan.history_entries.all()
    entries = sorted(entries, key=lambda entry: (entry.date, entry.pk or 0))
    events = defaultdict(list)
    corrections = {}
    for entry in entries:
        if entry.installment_date:
            corrections[entry.installment_date] = entry
        if entry.date <= cutoff or (project and entry.kind in (Kind.RATE, Kind.EMI)):
            events[entry.date].append(entry)

    if initial_rate is None:
        initial_rate = next(
            (rate.interest_rate for rate in loan.interest_rates.all() if rate.effective_date == loan.start_date),
            ZERO,
        )
    rate = initial_rate
    balance = accrued = unpaid = ZERO
    last_date = loan.start_date
    fixed_emi = entered_emi = None
    daily = loan.interest_method == Loan.InterestMethod.DAILY
    keep_emi = loan.rate_reset == Loan.RateReset.KEEP_EMI
    limit = MAX_INSTALLMENTS if keep_emi else loan.duration_months
    dues = {}
    for number in range(1, limit + 1):
        day = due_date(loan, number)
        if not project and day > cutoff:
            break
        dues[day] = number
    days = sorted(set(dues) | set(events) | {cutoff})
    expected_by_date = {}
    prepaid_interest = {}
    latest_event = max(events, default=loan.start_date)
    for day in days:
        if daily:
            accrued += balance * rate * Decimal((day - last_date).days) / Decimal('36500')
        last_date = day
        due = dues.get(day)
        day_events = events.get(day, ())
        for entry in day_events:
            if entry.kind == Kind.RATE:
                rate = entry.interest_rate
                result.rows.append(HistoryRow(day, Kind.RATE, balance, rate=rate, entry=entry))
            elif entry.kind == Kind.EMI:
                entered_emi = entry.amount
                result.rows.append(HistoryRow(day, Kind.EMI, balance, amount=entered_emi, entry=entry))
            elif entry.kind == Kind.DISBURSEMENT:
                balance += entry.amount
                result.drawn += entry.amount
                result.rows.append(HistoryRow(day, Kind.DISBURSEMENT, balance, amount=entry.amount, entry=entry))

        # Book the period before applying same-day payments. An early payment linked
        # to this installment may already have covered some of its interest.
        period_interest = ZERO
        if due:
            if daily:
                period_interest = money(accrued)
                accrued = ZERO
            else:
                period_interest = LoanService.period_interest(balance, rate)
            unpaid = max(ZERO, unpaid + period_interest - prepaid_interest.pop(day, ZERO))

        correction = corrections.get(day)
        expected = ZERO
        if due and (balance > ZERO or unpaid > ZERO or correction is not None):
            remaining = max(1, loan.duration_months - due + 1)
            early = correction if correction and correction.kind == Kind.PAYMENT and correction.date < day else None
            scheduled_balance = balance + (early.principal if early else ZERO)
            scheduled_owed = balance + unpaid + (early.amount if early else ZERO)
            if scheduled_balance > ZERO and (fixed_emi is None or not keep_emi):
                fixed_emi = max(CENT, LoanService.decimal_emi(scheduled_balance, rate, remaining))
            expected = min(entered_emi if entered_emi is not None else fixed_emi or ZERO, scheduled_owed)
            if not keep_emi and entered_emi is None and remaining == 1:
                expected = scheduled_owed

        for entry in day_events:
            if entry.kind != Kind.PAYMENT:
                continue
            if entry.principal > balance:
                result.invalid_entry = entry
                return result
            balance -= entry.principal
            interest = entry.interest
            if daily:
                settled = min(interest, money(accrued))
                accrued = max(ZERO, accrued - settled)
                interest -= settled
            if entry.installment_date and entry.installment_date > day:
                prepaid_interest[entry.installment_date] = max(ZERO, interest - unpaid)
            unpaid = max(ZERO, unpaid - interest)

            result.principal_paid += entry.principal
            result.interest_paid += entry.interest
            result.rows.append(HistoryRow(
                day, 'recorded', balance, entry.amount, entry.principal, entry.interest,
                unpaid_interest=money(unpaid + accrued), due_date=entry.installment_date, entry=entry,
            ))

        if due and (expected > ZERO or balance > ZERO or unpaid > ZERO or day in corrections):
            if correction is None:
                interest = min(expected, unpaid)
                principal = min(balance, expected - interest)
                balance -= principal
                unpaid -= interest
                status = 'assumed' if day <= cutoff else 'projected'
                if day <= cutoff:
                    result.principal_paid += principal
                    result.interest_paid += interest
                result.rows.append(HistoryRow(
                    day, status, balance, interest + principal, principal, interest,
                    rate, unpaid, due_date=day, expected_amount=expected,
                ))
            elif correction.kind == Kind.MISSED:
                result.rows.append(HistoryRow(
                    day, 'missed', balance, unpaid_interest=unpaid, due_date=day,
                    entry=correction, expected_amount=expected,
                ))
            elif correction.date > day:
                result.rows.append(HistoryRow(
                    day, 'pending_recorded', balance, unpaid_interest=unpaid, due_date=day,
                    expected_amount=expected,
                ))
            expected_by_date[day] = expected

        if day <= cutoff:
            result.balance = balance
            result.unpaid_interest = money(unpaid + accrued)
        elif project and day >= latest_event:
            if balance == ZERO and unpaid == ZERO:
                break
            if keep_emi and not daily and due and expected <= period_interest and balance > ZERO:
                # With no further terms changes, this fixed payment cannot reduce principal.
                break

    result.projected_balance = balance
    result.projected_unpaid_interest = money(unpaid + accrued)
    # A linked actual can occur either before or after its contractual due date.
    for row in result.rows:
        if row.status == 'recorded' and row.due_date is not None:
            expected = expected_by_date.get(row.due_date)
            row.expected_amount = expected
            if expected is not None and row.amount < expected:
                row.status = 'partial'
    return result
