"""Activity numbers for the admin: how many people added an expense, and how many signed in, lately."""
from datetime import timedelta

from django.contrib.auth.models import User
from django.utils import timezone

WINDOWS = (7, 15, 30)          # days, counted back from now


def _real_users():
    # The demo account is not a person (the User list hides it too)
    return User.objects.exclude(username='demo')


def activity_stats(now=None) -> dict:
    """{'total': all users, 'rows': [{'days', 'expense_users', 'login_users', 'expense_pct', 'login_pct'}]}.

    * An expense counts when it was added (its creation time), not the date written on it, so a backdated entry
      counts for the day it was entered. Entries since deleted are left out.
    * A sign-in is the user's last_login. Someone who stays signed in on their phone (a session lasts two weeks)
      does not sign in again, so this undercounts people who simply keep using the app.
    """
    from .models import Expense

    now = now or timezone.now()
    users = _real_users()
    total = users.count()
    rows = []
    for days in WINDOWS:
        since = now - timedelta(days=days)
        expense_users = (Expense.objects.filter(created_at__gte=since, is_deleted=False)
                         .exclude(user__username='demo').order_by().values('user_id').distinct().count())
        login_users = users.filter(last_login__gte=since).count()
        rows.append({
            'days': days,
            'expense_users': expense_users,
            'login_users': login_users,
            'expense_pct': round(expense_users * 100 / total) if total else 0,
            'login_pct': round(login_users * 100 / total) if total else 0,
        })
    return {'total': total, 'rows': rows, 'as_of': now}
