from datetime import date

from expenses.models import BudgetLimitVersion


def backdate_limits(user, since=date(2000, 1, 1)):
    """Make every recurring limit the user has applied 'since forever', the way the migration's
    backfill does for real users, so tests about past months see the limits their setup created."""
    BudgetLimitVersion.objects.filter(user=user).update(effective_from=since)


def legacy_page_ctx(context):
    """The Budget page's figures under the names the page's older tests use, built from the
    page payload (context['page']), so those tests keep checking the same behaviour."""
    from decimal import Decimal
    from types import SimpleNamespace

    zero = Decimal('0')
    dec = lambda x: None if x is None else Decimal(str(x))  # noqa: E731
    page = context['page']
    rows = []
    for r in page['categories']:
        limit, spent = dec(r['limit']), dec(r['spent']) or zero
        pct = r['percent'] or 0.0
        rows.append({
            'category': SimpleNamespace(name=r['name'], id=r['id']),
            'spent': spent, 'limit': limit, 'status': r['status'],
            'percentage': min(pct, 100), 'actual_percentage': pct,
            'remaining': dec(r['remaining']) if limit is not None and spent <= limit else zero,
            'over_budget': dec(r['over_by']) or zero,
            'left_percentage': max(0.0, 100.0 - pct) if limit else 0,
        })
    budgeted = dec(page['spent']) or zero
    unbudgeted = dec(page['unbudgeted_spent']) or zero
    percent = page['percent'] or 0.0
    return {
        'budget_data': rows,
        'needs_attention': [r for r in rows if r['status'] in ('over', 'limit')],
        'on_track': [r for r in rows if r['status'] == 'ontrack'],
        'no_limit': [r for r in rows if r['status'] == 'nolimit'],
        'over_budget_count': page['counts']['over'], 'at_limit_count': page['counts']['at_limit'],
        'on_track_count': page['counts']['on_track'], 'no_limit_count': page['counts']['no_limit'],
        'total_budget': dec(page['total']), 'budgeted_spent': budgeted, 'unbudgeted_spent': unbudgeted,
        'total_spent': budgeted + unbudgeted, 'total_remaining': dec(page['remaining']) or zero,
        'over_budget_amount': dec(page['over_by']) or zero,
        'total_percentage': min(percent, 100.0), 'actual_total_percentage': percent,
        'pace_state': page['pace_state'],
        'current_month': context['current_month'], 'current_year': context['current_year'],
        'month_name': context['month_start'].strftime('%B'),
    }
