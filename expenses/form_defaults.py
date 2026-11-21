"""Values a form can fill in so the person has less to choose.

Rules used by the money forms and the Guided Setups:

* When a required dropdown has exactly one choice, that choice is already selected.
* An account field starts on the account the person used last time for that kind of entry, when that is still one they
  can pick; failing that, the cash or bank account they use most; failing that, the one called Cash (on the forms that
  always did that; the others leave the choice to the person).
"""
from datetime import timedelta

from django import forms
from django.db.models import Count
from django.utils import timezone

# Accounts money is normally paid from or received into (not deposits, investments, cards or loans).
CASH_AND_BANK_TYPES = ('CASH_WALLET', 'SAVINGS_ACCOUNT', 'SALARY_ACCOUNT', 'CURRENT_ACCOUNT', 'DIGITAL_WALLET')
MOST_USED_WINDOW_DAYS = 180


def only_choice(field):
    """The value of a required dropdown's one and only choice, or None when there are none or several."""
    if not field.required:
        return None
    if isinstance(field, forms.ModelChoiceField):
        found = list(field.queryset[:2])
        return found[0].pk if len(found) == 1 else None
    choices = getattr(field, 'choices', None)
    if choices is None:
        return None
    real = [value for value, _label in _flatten(choices) if value not in (None, '')]
    return real[0] if len(real) == 1 else None


def _flatten(choices):
    for value, label in choices:
        if isinstance(label, (list, tuple)):
            yield from _flatten(label)
        else:
            yield value, label


def last_used_account(queryset, rows, *fields):
    """The account named by the newest of ``rows`` (any of ``fields``, tried in order), if the person can still pick it.

    ``rows`` is a queryset ordered newest first. Returns an Account or None.
    """
    allowed = {account.pk: account for account in queryset}
    for row in rows.values_list(*fields)[:5]:
        values = row if isinstance(row, tuple) else (row,)
        for account_id in values:
            if account_id in allowed:
                return allowed[account_id]
    return None


def default_payer_account(queryset, loan, user):
    """The account most likely to pay a loan: the one that paid this loan's last EMI, else the one that paid the
    person's last EMI on any loan, else the cash or bank account they use most, else the first salary, savings or current account, else the first account that is
    not a debt (never a card or a loan)."""
    from .account_types import is_liability
    from .models import LoanRepayment
    accounts = list(queryset)
    if loan is not None:
        found = last_used_account(queryset, loan.repayments.order_by('-date', '-id'), 'from_account_id')
        if found:
            return found
    found = last_used_account(queryset, LoanRepayment.objects.filter(loan__user=user).order_by('-date', '-id'), 'from_account_id')
    if found:
        return found
    busiest = most_used_account(accounts)
    if busiest:
        return busiest
    for account in accounts:
        if account.account_type in ('SALARY_ACCOUNT', 'SAVINGS_ACCOUNT', 'CURRENT_ACCOUNT'):
            return account
    return next((a for a in accounts if not is_liability(a.account_type)), None)


def most_used_account(accounts):
    """The cash or bank account among ``accounts`` (a queryset or a list) with the most expense and income entries in the
    last 180 days, or None (nothing recorded yet, or no cash or bank account). Ties go to the name that comes first; a
    credit card, deposit or investment never counts, and neither does a deleted entry.

    Two small grouped queries on the entries, none on the accounts, so a form that already holds its accounts pays
    little for it."""
    from collections import Counter

    from .models import Expense, Income
    candidates = [a for a in accounts if a.account_type in CASH_AND_BANK_TYPES]
    if not candidates:
        return None
    ids = [a.pk for a in candidates]
    since = timezone.localdate() - timedelta(days=MOST_USED_WINDOW_DAYS)
    uses = Counter()
    for model, extra in ((Expense, {'is_deleted': False}), (Income, {})):
        rows = (model.objects.filter(user_id=candidates[0].user_id, date__gte=since, account_id__in=ids, **extra)
                .values('account_id').annotate(n=Count('id')))
        for row in rows:
            uses[row['account_id']] += row['n']
    if not uses:
        return None
    top = max(uses.values())
    return min((a for a in candidates if uses.get(a.pk) == top), key=lambda a: a.name)


def default_account(queryset, rows=None, *fields, cash=True):
    """The account a form should start on: the one named by the newest of ``rows`` (see last_used_account), else the
    cash or bank account used most, else (with ``cash``) the one called Cash, else None: a form that never guessed
    before, with nothing to go on, still leaves the choice to the person."""
    accounts = list(queryset)
    found = last_used_account(accounts, rows, *(fields or ('account_id',))) if rows is not None else None
    found = found or most_used_account(accounts)
    if found or not cash:
        return found
    return next((a for a in accounts if a.name == 'Cash'), None)
