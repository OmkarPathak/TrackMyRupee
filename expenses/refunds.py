"""Money paid back against an expense (Income.refund_for_expense).

An expense keeps the total refunded against it in `refunded_base` (base currency), so every report that adds up
spending can use "amount minus what came back" without joining the income table. Two pieces:

  * `NET_EXPENSE`: the expression reports sum instead of `base_amount`;
  * `recompute_expense_refunds`: rebuilds `refunded_base` from the income entries linked to an expense. Income calls
    it whenever a refund is saved, moved to another expense, deleted or soft-deleted.

The ledger, account balances and exports keep the gross amount: the money really did leave the account, and the
refund really did come back in.
"""

from decimal import Decimal

from django.db.models import DecimalField, F, Sum, Value
from django.db.models.functions import Coalesce

# what an expense really cost (base currency): the amount less everything refunded against it
NET_EXPENSE = F('base_amount') - F('refunded_base')


def net_sum(**kwargs):
    """Sum of what the expenses in a queryset really cost; takes the same `filter=` as Sum."""
    return Sum(NET_EXPENSE, **kwargs)


def recompute_expense_refunds(expense_ids):
    """Rebuild `refunded_base` for these expenses from the (not deleted) income entries linked to them."""
    from .models import Expense, Income

    ids = {pk for pk in expense_ids if pk}
    if not ids:
        return
    sums = dict(Income.objects.filter(refund_for_expense_id__in=ids, is_deleted=False)
                .order_by().values('refund_for_expense_id').annotate(total=Sum('base_amount'))
                .values_list('refund_for_expense_id', 'total'))
    for pk in ids:
        Expense.objects.filter(pk=pk).update(refunded_base=sums.get(pk) or Decimal('0'))


def refunded_for(expense_ids):
    """{expense id: refunded base amount} read from the income entries (not the stored total)."""
    from .models import Income

    return {pk: total for pk, total in (
        Income.objects.filter(refund_for_expense_id__in=list(expense_ids), is_deleted=False)
        .order_by().values('refund_for_expense_id').annotate(total=Sum('base_amount'))
        .values_list('refund_for_expense_id', 'total'))}
