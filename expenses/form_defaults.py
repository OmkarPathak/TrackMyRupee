"""Values a form can fill in so the person has less to choose.

Two rules, used by the money forms:

* When a required dropdown has exactly one choice, that choice is already selected.
* An account field starts on the account the person used last time, when that is still one they can pick.
"""
from django import forms


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
    person's last EMI on any loan, else the first salary, savings or current account, else the first account that is
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
    for account in accounts:
        if account.account_type in ('SALARY_ACCOUNT', 'SAVINGS_ACCOUNT', 'CURRENT_ACCOUNT'):
            return account
    return next((a for a in accounts if not is_liability(a.account_type)), None)
