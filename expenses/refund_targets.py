"""What a refund can pay back, for the income form's search box: the user's expenses and capital events in one list.

The box opens on the latest few (so "the one I just paid" is one click) and searches both kinds as the user types.
Each row says which kind it is, so the form knows which link to fill in.
"""

from django.db.models import CharField, Q
from django.db.models.functions import Cast
from django.utils.formats import date_format

RECENT = 5       # shown before anything is typed
MATCHES = 12     # shown while searching


def _money(value, currency):
    from .templatetags.digit_filters import ind_comma

    return f"{currency}{ind_comma(value, currency)}"


def expense_row(expense):
    """An expense as the picker shows it."""
    symbol = expense.user.profile.currency      # what was refunded is kept in the user's own currency
    refunded = expense.refunded_base or 0
    return {
        'kind': 'expense', 'id': expense.pk,
        'description': (expense.description or '')[:90],
        'category': expense.category or '',
        'date': expense.date.isoformat(), 'date_label': date_format(expense.date, 'd M Y'),
        'amount_label': _money(expense.amount, expense.currency),
        'refunded': bool(refunded), 'refunded_label': _money(refunded, symbol) if refunded else '',
    }


def event_row(event):
    """A capital event as the picker shows it: its note (else its type) as the title."""
    symbol = event.user.profile.currency
    refunded = getattr(event, 'refunded_total', None)
    if refunded is None:
        from django.db.models import Sum

        from .models import Income
        refunded = (Income.objects.filter(refund_for_capital_event=event, is_deleted=False)
                    .aggregate(t=Sum('base_amount'))['t'] or 0)
    return {
        'kind': 'event', 'id': event.pk,
        'description': (event.note or str(event.get_subtype_display()))[:90],
        'category': event.report_category,
        'date': event.date.isoformat(), 'date_label': date_format(event.date, 'd M Y'),
        'amount_label': _money(event.amount, event.currency),
        'refunded': bool(refunded), 'refunded_label': _money(refunded, symbol) if refunded else '',
    }


def search_refund_targets(user, query):
    """Rows (dicts, newest first) for the user's expenses and capital events: the latest few when `query` is empty,
    else the ones where every word matches the description / note, the category, the type (events), the amount
    (commas are ignored) or the date."""
    from .models import CapitalEvent, Expense

    words = [w for w in (query or '').split() if w]
    limit = MATCHES if words else RECENT

    expenses = (Expense.objects.filter(user=user, is_deleted=False).select_related('user__profile')
                .annotate(_amount_text=Cast('amount', output_field=CharField()),
                          _date_text=Cast('date', output_field=CharField())))
    from .views.capital_events import refunded_total_subquery

    events = (CapitalEvent.objects.filter(user=user, is_deleted=False).select_related('user__profile')
              .annotate(refunded_total=refunded_total_subquery(),
                        _amount_text=Cast('amount', output_field=CharField()),
                        _date_text=Cast('date', output_field=CharField())))
    for word in words:
        plain = word.replace(',', '')
        expenses = expenses.filter(Q(description__icontains=word) | Q(category__icontains=word)
                                   | Q(_amount_text__icontains=plain) | Q(_date_text__icontains=plain))
        types = [code for code, label in CapitalEvent.SUBTYPE_CHOICES if word.lower() in str(label).lower()]
        events = events.filter(Q(note__icontains=word) | Q(category__icontains=word) | Q(subtype__in=types)
                               | Q(_amount_text__icontains=plain) | Q(_date_text__icontains=plain))

    found = ([(e.date, e.pk, 1, expense_row(e)) for e in expenses.order_by('-date', '-id')[:limit]]
             + [(c.date, c.pk, 0, event_row(c)) for c in events.order_by('-date', '-id')[:limit]])
    found.sort(key=lambda t: (t[0], t[1], t[2]), reverse=True)
    return [row for _d, _i, _k, row in found[:limit]]
