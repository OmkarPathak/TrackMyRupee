"""
Reconcile an account against a bank statement.

The balance a user reconciles against is the one the app shows
(``LedgerReadService.get_account_balance``). A statement is dated, but journal entries are
stamped with the time they were posted, so the balance on a past date is the current balance
minus the net of the dated transactions after that date. Manual adjustments (opening balance,
balance edits, earlier reconciliations) carry no transaction date and always count.
"""

from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.db import transaction

from .account_types import STRATEGY, strategy_for
from .ledger_read_service import LedgerReadService
from .models import (
    Account,
    AccountReconciliation,
    CapitalEvent,
    Expense,
    FinancialAuditLog,
    GoalContribution,
    Income,
    JournalEntry,
    LoanRepayment,
    Transfer,
)
from .utils import get_exchange_rate

TOLERANCE = Decimal('0.01')
CENTS = Decimal('0.01')

# Only accounts whose balance is what a bank or card statement reports. Deposits, holdings,
# loans and physical assets are valued by other strategies, so a statement says nothing about them.
RECONCILABLE_STRATEGIES = frozenset({STRATEGY.BALANCE, STRATEGY.REVOLVING_CREDIT})

RECENT_WINDOW_DAYS = 30
RECENT_LIMIT = 15
SUSPECT_LIMIT = 5


class ReconciliationError(Exception):
    """The reconciliation could not be recorded (invalid account or ledger failure)."""


@dataclass(frozen=True)
class Flow:
    date: object
    kind: str
    description: str
    amount: Decimal  # signed, in the account's currency: positive raises the balance


@dataclass
class Comparison:
    account: Account
    statement_date: object
    statement_balance: Decimal
    app_balance: Decimal
    difference: Decimal
    matched: bool
    current_balance: Decimal = Decimal('0.00')
    suspects: list = field(default_factory=list)
    recent: list = field(default_factory=list)
    later_count: int = 0
    unaccounted: dict = field(default_factory=dict)
    missing_opening_entry: bool = False

    @property
    def balance_after(self):
        """What the account would show today once the difference is corrected."""
        return (self.current_balance + self.difference).quantize(CENTS)


def is_reconcilable(account):
    return strategy_for(account.account_type) in RECONCILABLE_STRATEGIES


def _quantize(value):
    return Decimal(str(value)).quantize(CENTS)


def _convert(amount, from_currency, account):
    if from_currency and from_currency != account.currency:
        amount = amount * get_exchange_rate(from_currency, account.currency)
    return _quantize(amount)


def dated_flows(account):
    """Every dated transaction touching ``account``, signed in the account's currency."""
    user = account.user
    flows = []

    for e in Expense.objects.filter(user=user, account=account):
        flows.append(Flow(e.date, 'expense', e.description, -_convert(e.amount, e.currency, account)))

    for i in Income.objects.filter(user=user, account=account):
        flows.append(Flow(i.date, 'income', i.description or '', _convert(i.amount, i.currency, account)))

    for t in Transfer.objects.filter(user=user, from_account=account):
        flows.append(Flow(t.date, 'transfer', t.description or '', -_quantize(t.amount)))

    for t in Transfer.objects.filter(user=user, to_account=account).select_related('from_account'):
        flows.append(Flow(t.date, 'transfer', t.description or '', _convert(t.amount, t.from_account.currency, account)))

    for c in GoalContribution.objects.filter(goal__user=user, account=account).select_related('goal'):
        flows.append(Flow(c.date, 'goal', c.goal.name, -_convert(c.amount, c.goal.currency, account)))

    for r in LoanRepayment.objects.filter(loan__user=user, from_account=account).select_related('loan'):
        flows.append(Flow(r.date, 'loan', r.loan.name, -_convert(r.amount, r.loan.currency, account)))

    for ce in CapitalEvent.objects.filter(user=user, account=account, include_in_net_worth=True):
        flows.append(Flow(ce.date, 'capital', ce.note or '', -_convert(ce.amount, ce.currency, account)))

    return flows


def current_balance(account):
    """The balance the app shows for ``account`` today."""
    # Read from a fresh row: the display path falls back to the passed instance's own balance.
    account = Account.objects.select_related('user').get(pk=account.pk)
    return _quantize(LedgerReadService.get_account_balance(account))


def balance_as_of(account, as_of, *, flows=None, current=None):
    flows = dated_flows(account) if flows is None else flows
    current = current_balance(account) if current is None else current
    after = sum((f.amount for f in flows if f.date > as_of), Decimal('0.00'))
    return _quantize(current - after)


def _missing_opening_entry(account):
    return not JournalEntry.objects.filter(
        user_id=account.user_id,
        source_type='ADJUSTMENT',
        status='POSTED',
        metadata__opening_account_id=account.id,
    ).exists()


def _unaccounted_rows(user):
    return {
        'expenses': Expense.objects.filter(user=user, account__isnull=True).count(),
        'income': Income.objects.filter(user=user, account__isnull=True).count(),
        'capital_events': CapitalEvent.objects.filter(user=user, account__isnull=True).count(),
    }


def _suspects(flows, difference, statement_date):
    """Transactions whose size explains the gap: the same amount (a duplicate, or one missing
    from the app) or half of it (entered in the wrong direction)."""
    target = abs(difference)
    window_start = statement_date - timedelta(days=RECENT_WINDOW_DAYS * 3)
    suspects = []
    for f in flows:
        if not window_start <= f.date <= statement_date:
            continue
        size = abs(f.amount)
        if size == target:
            suspects.append((f, 'same_amount'))
        elif size * 2 == target:
            suspects.append((f, 'wrong_direction'))
    suspects.sort(key=lambda pair: pair[0].date, reverse=True)
    return suspects[:SUSPECT_LIMIT]


def compare(account, statement_date, statement_balance):
    flows = dated_flows(account)
    current = current_balance(account)
    app_balance = balance_as_of(account, statement_date, flows=flows, current=current)
    statement_balance = _quantize(statement_balance)
    difference = (statement_balance - app_balance).quantize(CENTS)
    matched = abs(difference) <= TOLERANCE

    comparison = Comparison(
        account=account,
        statement_date=statement_date,
        statement_balance=statement_balance,
        app_balance=app_balance,
        difference=difference,
        matched=matched,
        current_balance=current,
        later_count=sum(1 for f in flows if f.date > statement_date),
    )
    recent_start = statement_date - timedelta(days=RECENT_WINDOW_DAYS)
    comparison.recent = sorted(
        (f for f in flows if recent_start <= f.date <= statement_date),
        key=lambda f: f.date,
        reverse=True,
    )[:RECENT_LIMIT]
    if not matched:
        comparison.suspects = _suspects(flows, difference, statement_date)
        comparison.unaccounted = _unaccounted_rows(account.user)
        comparison.missing_opening_entry = _missing_opening_entry(account)
    return comparison


def record(account, statement_date, statement_balance, *, action, note=''):
    """Persist a reconciliation. ``action`` is 'adjust' (post a correcting entry) or 'open'.

    The comparison is recomputed under a lock, never taken from the submitted form, so a stale
    page or a double submit cannot post a wrong or duplicate adjustment: once the first
    adjustment lands, the second sees no difference and records a match.
    """
    with transaction.atomic():
        locked = Account.objects.select_for_update().select_related('user').get(pk=account.pk)
        if not locked.is_active or not is_reconcilable(locked):
            raise ReconciliationError('This account cannot be reconciled.')

        result = compare(locked, statement_date, statement_balance)
        entry = None
        if result.matched:
            status = 'MATCHED'
        elif action == 'adjust':
            status = 'ADJUSTED'
        else:
            status = 'OPEN'

        reconciliation = AccountReconciliation.objects.create(
            user=locked.user,
            account=locked,
            statement_date=statement_date,
            statement_balance=result.statement_balance,
            app_balance=result.app_balance,
            difference=result.difference,
            status=status,
            note=note[:255],
        )

        if status == 'ADJUSTED':
            if getattr(settings, 'LEDGER_WRITE_ENABLED', False):
                from .ledger_service import LedgerPostingService

                entry, _created = LedgerPostingService.post_account_balance_adjustment(
                    account=locked,
                    delta=result.difference,
                    version_token=f"RECON-{reconciliation.id}",
                    metadata={
                        'kind': 'RECONCILIATION',
                        'reconciliation_id': reconciliation.id,
                        'statement_date': statement_date.isoformat(),
                        'statement_balance': str(result.statement_balance),
                        'app_balance': str(result.app_balance),
                    },
                )
                if entry is None:
                    raise ReconciliationError('The correcting ledger entry could not be posted.')
                reconciliation.adjustment_entry = entry
                reconciliation.save(update_fields=['adjustment_entry'])

            locked.balance = (locked.balance + result.difference).quantize(CENTS)
            locked.save(update_fields=['balance', 'updated_at'])

        FinancialAuditLog.objects.create(
            user=locked.user,
            model_name='AccountReconciliation',
            object_id=reconciliation.id,
            action='CREATE',
            diff={
                'account_id': locked.id,
                'status': status,
                'statement_date': statement_date.isoformat(),
                'statement_balance': str(result.statement_balance),
                'app_balance': str(result.app_balance),
                'difference': str(result.difference),
            },
        )
        return reconciliation
