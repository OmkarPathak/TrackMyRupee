"""Server-side logic behind the New Expense composer.

Kept out of the views so the rules (visible accounts/categories, "Your usual",
keyword learning, idempotent save, monthly limit) are testable in isolation.
"""
import uuid
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Count, Max
from django.utils import timezone

from finance_tracker.plans import get_limit

from .forms import ExpenseForm
from .models import (
    CURRENCY_CHOICES,
    Account,
    Category,
    Expense,
    ExpenseKeywordHint,
)
from .parser import keyword_candidates, keyword_for_description
from .utils import format_indian_number

CURRENCY_CODES = {
    '₹': 'INR', '$': 'USD', '€': 'EUR', '£': 'GBP', '¥': 'JPY',
    'A$': 'AUD', 'C$': 'CAD', 'CHF': 'CHF', '元': 'CNY', '₩': 'KRW',
}

# Amounts at or above this (per currency symbol) ask "is that right?" before saving.
LARGE_AMOUNT_THRESHOLDS = {'₹': 100000, '¥': 1000000, '₩': 10000000, '元': 50000}
LARGE_AMOUNT_DEFAULT = 5000

UNDO_WINDOW = timedelta(minutes=10)
USUAL_WINDOW_DAYS = 60
USUAL_CACHE_SECONDS = 120


class ComposerError(Exception):
    """A user-facing save failure.  ``code`` lets the client react (e.g. limit -> pricing)."""

    def __init__(self, message, code='invalid', status=400, field_errors=None):
        super().__init__(message)
        self.message = message
        self.code = code
        self.status = status
        self.field_errors = field_errors or {}


# ── Visible accounts / categories (same tier rules as ExpenseForm) ──────────────

def visible_accounts(user):
    """Active accounts the user may post to (tier-unlocked), oldest first."""
    accounts = list(Account.objects.filter(user=user, is_active=True).order_by('created_at', 'id'))
    limit = get_limit(user.profile.active_tier, 'accounts')
    if limit is not None and limit != -1:
        accounts = accounts[:limit]
    return accounts


def visible_categories(user):
    categories = Category.objects.filter(user=user).order_by('id')
    limit = get_limit(user.profile.active_tier, 'budget_categories')
    if limit is not None and limit != -1:
        categories = categories[:limit]
    return list(categories.values_list('name', flat=True))


def account_info(accounts):
    return [{'id': a.id, 'name': a.name, 'type': a.account_type} for a in accounts]


# ── "Your usual" ────────────────────────────────────────────────────────────────

def usual_cache_key(user_id):
    return f'composer_usual:{user_id}'


def _format_chip_amount(amount):
    amount = Decimal(amount)
    return str(int(amount)) if amount == amount.to_integral_value() else f'{amount:.2f}'


def usual_expenses(user, limit=4):
    """Top recurring (description, amount) pairs of the last ~60 days; two cheap queries."""
    key = usual_cache_key(user.id)
    cached = cache.get(key)
    if cached is not None:
        return cached[:limit]

    since = timezone.localdate() - timedelta(days=USUAL_WINDOW_DAYS)
    pairs = list(
        Expense.objects.filter(user=user, date__gte=since)
        .exclude(description='')
        .values('description', 'amount')
        .annotate(n=Count('id'), last=Max('date'))
        .filter(n__gte=2)
        .order_by('-n', '-last')[:limit]
    )
    usuals = []
    if pairs:
        wanted = {(p['description'], p['amount']) for p in pairs}
        latest = {}
        rows = (
            Expense.objects.filter(
                user=user, date__gte=since,
                description__in=[p['description'] for p in pairs],
            )
            .order_by('-date', '-created_at')
            .values('description', 'amount', 'category', 'account_id', 'payment_method', 'currency')[:200]
        )
        for row in rows:
            k = (row['description'], row['amount'])
            if k in wanted and k not in latest:
                latest[k] = row
        for p in pairs:
            row = latest.get((p['description'], p['amount']))
            if not row:
                continue
            desc = p['description'].strip()
            usuals.append({
                'label': f"{desc.lower()[:24]} {_format_chip_amount(p['amount'])}",
                'description': desc,
                'amount': str(p['amount']),
                'currency': row['currency'],
                'category': row['category'],
                'account_id': row['account_id'],
                'payment_method': row['payment_method'],
            })
    cache.set(key, usuals, USUAL_CACHE_SECONDS)
    return usuals[:limit]


# ── Composer bootstrap payload ──────────────────────────────────────────────────

def _account_label(account):
    return str(account)  # "SBI Savings (₹35,000)"


def build_payload(user):
    profile = user.profile
    accounts = visible_accounts(user)
    categories = visible_categories(user)

    last = (
        Expense.objects.filter(user=user)
        .order_by('-date', '-created_at')
        .values('account_id', 'payment_method')
        .first()
    )
    account_ids = [a.id for a in accounts]
    default_account_id, account_source = None, None
    if last and last['account_id'] in account_ids:
        default_account_id, account_source = last['account_id'], 'last_used'
    elif accounts:
        default_account_id, account_source = accounts[0].id, 'first'

    return {
        'categories': categories,
        'accounts': [
            {
                'id': a.id,
                'name': a.name,
                'label': _account_label(a),
                'type': a.account_type,
                'currency': a.currency,
            }
            for a in accounts
        ],
        'today': timezone.localdate().isoformat(),
        'default_account_id': default_account_id,
        'default_account_source': account_source,
        'default_payment_method': (last or {}).get('payment_method') or 'Cash',
        'default_currency': profile.currency,
        'currencies': [
            {'symbol': sym, 'code': CURRENCY_CODES.get(sym, sym), 'label': str(label)}
            for sym, label in CURRENCY_CHOICES
        ],
        'large_amount_thresholds': {
            sym: LARGE_AMOUNT_THRESHOLDS.get(sym, LARGE_AMOUNT_DEFAULT) for sym, _label in CURRENCY_CHOICES
        },
        'usuals': usual_expenses(user),
    }


# ── Keyword learning ────────────────────────────────────────────────────────────

def fetch_hints(user, text):
    """Learned hints for the keywords in ``text`` (one indexed query)."""
    candidates = keyword_candidates(text)
    if not candidates:
        return {}
    rows = ExpenseKeywordHint.objects.filter(user=user, keyword__in=candidates).values(
        'keyword', 'category', 'account_id', 'payment_method', 'use_count')
    return {
        r['keyword']: {
            'category': r['category'],
            'account_id': r['account_id'],
            'payment_method': r['payment_method'],
            'count': r['use_count'],
        }
        for r in rows
    }


def learn_from_expense(user, description, category, account, payment_method):
    """Remember the final choices for this expense's keyword (latest choice wins)."""
    keyword = keyword_for_description(description)[:60]
    if not keyword:
        return
    now = timezone.now()
    hint, created = ExpenseKeywordHint.objects.get_or_create(
        user=user, keyword=keyword,
        defaults={
            'category': category or '',
            'account': account,
            'payment_method': payment_method or '',
            'last_used': now,
        },
    )
    if not created:
        hint.category = category or ''
        hint.account = account
        hint.payment_method = payment_method or ''
        hint.use_count += 1
        hint.last_used = now
        hint.save(update_fields=['category', 'account', 'payment_method', 'use_count', 'last_used'])
    else:
        _prune_hints(user)


def _prune_hints(user):
    cap = ExpenseKeywordHint.MAX_PER_USER
    total = ExpenseKeywordHint.objects.filter(user=user).count()
    if total <= cap:
        return
    stale = list(
        ExpenseKeywordHint.objects.filter(user=user)
        .order_by('use_count', 'last_used')
        .values_list('id', flat=True)[:total - cap]
    )
    ExpenseKeywordHint.objects.filter(id__in=stale).delete()


# ── Save ────────────────────────────────────────────────────────────────────────

def check_monthly_limit(user, expense_date):
    """Raise ComposerError(code='limit') when this add would exceed the plan's monthly cap."""
    limit = get_limit(user.profile.active_tier, 'expenses_per_month')
    if limit is None or limit == -1:
        return
    today = timezone.localdate()
    if expense_date.year != today.year or expense_date.month != today.month:
        return
    count = Expense.objects.filter(user=user, date__year=today.year, date__month=today.month).count()
    if count + 1 > limit:
        raise ComposerError(
            'limit', code='limit', status=403,
        )


def _parse_amount(raw):
    try:
        value = Decimal(str(raw).replace(',', '').strip())
    except (InvalidOperation, AttributeError):
        return None
    return value if value > 0 else None


def save_expense(user, payload):
    """Validate and save one expense.  Returns ``(expense, created)``.

    ``payload['key']`` is the idempotency key (stored as ``client_dedup_key``): replaying the
    same key returns the original expense instead of creating a second one.
    """
    key = str(payload.get('key') or '').strip()
    if not key:
        key = str(uuid.uuid4())
    key = key[:255]

    existing = Expense.objects.filter(user=user, client_dedup_key=key).first()
    if existing:
        return existing, False

    errors = {}
    amount = _parse_amount(payload.get('amount'))
    if amount is None:
        errors['amount'] = 'invalid'

    categories = visible_categories(user)
    wanted_cat = str(payload.get('category') or '').strip()
    category = next((c for c in categories if c.lower() == wanted_cat.lower()), None) if wanted_cat else None
    if not category:
        errors['category'] = 'invalid'

    if errors:
        raise ComposerError('invalid', code='invalid', field_errors=errors)

    description = str(payload.get('description') or '').strip() or category
    form = ExpenseForm({
        'date': payload.get('date'),
        'amount': str(amount),
        'currency': payload.get('currency') or user.profile.currency,
        'account': payload.get('account_id') or '',
        'description': description,
        'category': category,
        'payment_method': payload.get('payment_method') or 'Cash',
        'client_dedup_key': key,
    }, user=user)
    if not form.is_valid():
        raise ComposerError(
            'invalid', code='invalid',
            field_errors={f: [str(e) for e in errs] for f, errs in form.errors.items()},
        )

    check_monthly_limit(user, form.cleaned_data['date'])

    try:
        with transaction.atomic():
            expense = form.save(commit=False)
            expense.user = user
            expense.save()
    except IntegrityError:
        # A concurrent double submit won the race: hand back its expense.
        existing = Expense.objects.filter(user=user, client_dedup_key=key).first()
        if existing:
            return existing, False
        raise ComposerError('duplicate', code='duplicate')
    except (RuntimeError, ValidationError):
        raise ComposerError('currency', code='currency')

    learn_from_expense(user, expense.description, expense.category, expense.account, expense.payment_method)
    cache.delete(usual_cache_key(user.id))
    return expense, True


def display_amount(expense):
    """"₹450" / "₹450.50" / "$10.50" for toasts (Indian digit grouping for rupees)."""
    amount = expense.amount
    whole = amount == amount.to_integral_value()
    if expense.currency == '₹':
        text = format_indian_number(int(amount))
        if not whole:
            text += f"{amount - int(amount):.2f}"[1:]
        return f"₹{text}"
    return f"{expense.currency}{amount:,.0f}" if whole else f"{expense.currency}{amount:,.2f}"
