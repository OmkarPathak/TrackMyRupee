"""Writes for budget limits. Rules (all in one transaction):

  * "every_month" from month M: upsert a version at effective_from=M and delete any override for M.
    Overrides in later months stay (the user set them on purpose). Only for the current or a future
    month, so past months can never be rewritten.
  * "this_month": upsert an override for M. Never touches versions. Allowed for any month.
  * amount None = no limit (scoped the same way). amount must be >= 0.
  * Last write wins; unique constraints + update_or_create keep concurrent requests duplicate-free.
  * Categories outside the user's plan allowance (plans.py `budget_categories`) are refused.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from . import budget_resolver as resolver
from .budgets import has_limit, monthly_spend_by_category, normalize_category

THIS_MONTH, EVERY_MONTH = 'this_month', 'every_month'
SCOPES = (THIS_MONTH, EVERY_MONTH)


class BudgetError(ValueError):
    """A rejected budget write. `code` is stable for the API; str() is a translated message."""
    status = 400

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


class BudgetPlanLocked(BudgetError):
    status = 403


class BudgetNotFound(BudgetError):
    status = 404


def _clean_amount(amount):
    if amount is None:
        return None
    try:
        value = Decimal(str(amount)).quantize(Decimal('0.01'))
    except (InvalidOperation, ValueError):
        raise BudgetError('invalid_amount', _('Enter a valid amount.')) from None
    if not value.is_finite() or value < 0:
        raise BudgetError('invalid_amount', _('The limit cannot be negative.'))
    if value >= Decimal('10') ** 13:
        raise BudgetError('invalid_amount', _('That amount is too large.'))
    return value


def _check(user, category, month, scope):
    if category.user_id != user.id:
        raise BudgetNotFound('not_found', _('Category not found.'))
    if scope not in SCOPES:
        raise BudgetError('invalid_scope', _('Choose "This month only" or "Every month".'))
    if category.id not in resolver.plan_allowed_category_ids(user):
        raise BudgetPlanLocked('plan_locked', _('This category is locked on your plan.'))
    month = resolver.month_start(month)
    if scope == EVERY_MONTH and month < timezone.localdate().replace(day=1):
        raise BudgetError('past_every_month',
                          _('"Every month" can only be set for the current or a future month.'))
    return month


def _write(user, category, month, amount, scope, actor):
    from .models import BudgetLimitVersion, BudgetMonthOverride

    name = normalize_category(category.name)
    keys = dict(user=user, category_name=name)
    try:
        with transaction.atomic():
            if scope == EVERY_MONTH:
                BudgetLimitVersion.objects.update_or_create(
                    **keys, effective_from=month,
                    defaults={'amount': amount, 'category': category, 'created_by': actor or user})
                BudgetMonthOverride.objects.filter(**keys, month=month).delete()
            else:
                BudgetMonthOverride.objects.update_or_create(
                    **keys, month=month, defaults={'amount': amount, 'category': category})
    except IntegrityError:  # lost a create race; the row exists now, so update it
        with transaction.atomic():
            if scope == EVERY_MONTH:
                BudgetLimitVersion.objects.filter(**keys, effective_from=month).update(amount=amount)
                BudgetMonthOverride.objects.filter(**keys, month=month).delete()
            else:
                BudgetMonthOverride.objects.filter(**keys, month=month).update(amount=amount)


def set_limit(user, category, month, amount, scope, actor=None):
    """Set (or with amount=None, remove) a category's limit for a month under a scope."""
    month = _check(user, category, month, scope)
    amount = _clean_amount(amount)
    _write(user, category, month, amount, scope, actor)
    _invalidate(user)
    return resolver.effective_limit(user, category, month)


def reset_override(user, category, month):
    """Drop the one-month override so the month follows the recurring limit again."""
    from .models import BudgetMonthOverride

    month = _check(user, category, month, THIS_MONTH)
    BudgetMonthOverride.objects.filter(
        user=user, category_name=normalize_category(category.name), month=month).delete()
    _invalidate(user)
    return resolver.effective_limit(user, category, month)


def rebalance(user, month, scope, moves, actor=None):
    """Move limit between categories atomically. `moves`: [{'from_category': Category,
    'to_category': Category, 'amount': Decimal}]. A source must have a limit, and no move may push a
    source below what it has already spent that month. Any failure rolls everything back."""
    month_ = resolver.month_start(month)
    if not moves:
        raise BudgetError('no_moves', _('Choose at least one move.'))
    with transaction.atomic():
        limits = resolver.effective_limits_by_name(user, month_)
        spend, _total = monthly_spend_by_category(user, month_.year, month_.month)
        new_limits: dict[int, tuple] = {}
        for mv in moves:
            src, dst = mv['from_category'], mv['to_category']
            move = _clean_amount(mv['amount'])
            if src.id == dst.id or not move or move <= 0:
                raise BudgetError('invalid_move', _('Each move needs two different categories and an amount above zero.'))
            for cat in (src, dst):
                _check(user, cat, month_, scope)
            cur_src = new_limits.get(src.id, (src, limits.get(normalize_category(src.name))))[1]
            if not has_limit(cur_src):
                raise BudgetError('source_no_limit', _('%(name)s has no limit to move from.') % {'name': src.name})
            lowered = cur_src - move
            if lowered < spend.get(normalize_category(src.name), Decimal('0')):
                raise BudgetError('below_spent', _('%(name)s has already spent more than its limit would be after this move.') % {'name': src.name})
            cur_dst = new_limits.get(dst.id, (dst, limits.get(normalize_category(dst.name))))[1]
            new_limits[src.id] = (src, lowered)
            new_limits[dst.id] = (dst, (cur_dst or Decimal('0')) + move)
        for cat, amount in new_limits.values():
            _write(user, cat, month_, amount, scope, actor)
    _invalidate(user)
    return {cat.id: amount for cat, amount in new_limits.values()}


def sync_legacy_limit(category):
    """Mirror a change to the legacy Category.limit column into the versioned tables as an
    "every month from this month" edit. No plan or past-month checks: this is bookkeeping for
    writers that still set the column (the category form, admin, demo data)."""
    from .models import BudgetLimitVersion

    amount = category.limit if category.limit and category.limit > 0 else None
    user = category.user
    name = normalize_category(category.name)
    if amount is None and not BudgetLimitVersion.objects.filter(user=user, category_name=name).exists():
        return
    _write(user, category, timezone.localdate().replace(day=1), amount, EVERY_MONTH, None)
    _invalidate(user)


def rename_category_history(user, old_name, new_name, category=None):
    """Re-key version/override rows when a category is renamed, so its history follows it."""
    from .models import BudgetLimitVersion, BudgetMonthOverride

    old_key, new_key = normalize_category(old_name), normalize_category(new_name)
    if old_key == new_key:
        return
    for model, field in ((BudgetLimitVersion, 'effective_from'), (BudgetMonthOverride, 'month')):
        taken = set(model.objects.filter(user=user, category_name=new_key).values_list(field, flat=True))
        rows = model.objects.filter(user=user, category_name=old_key)
        rows.filter(**{f'{field}__in': taken}).delete()  # target name's own rows win on a clash
        rows.update(category_name=new_key, **({'category': category} if category else {}))
    _invalidate(user)


def _invalidate(user):
    from django.core.cache import cache
    cache.delete_many([f'budget_dashboard_{user.id}', f'home_default_data_{user.id}',
                       f'analytics_default_data_{user.id}'])
