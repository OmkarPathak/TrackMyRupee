import sys
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.db.models import CharField, Count, IntegerField, Q, Sum, Value
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.utils.translation import gettext as _
from django.utils.translation import ngettext
from django.views.decorators.http import require_POST
from django.views.generic import CreateView, DeleteView, ListView, UpdateView, View

from expenses.views.utils import get_safe_redirect_url
from finance_tracker.plans import get_limit

from ..account_types import STRATEGY, deposit_codes, holdings_codes, strategy_for
from ..account_valuation import (
    get_baseline,
    get_current,
    get_current_insurance_surrender,
    get_interest_summary,
    get_premiums_paid,
    is_protection_policy,
    premiums_paid_by_asset,
)
from ..forms import AccountForm, TransferForm
from ..ledger_read_service import LedgerReadService, _compute_deposit_value
from ..models import (
    Account,
    AccountReconciliation,
    CapitalEvent,
    Expense,
    GoalContribution,
    Holding,
    Income,
    LoanRepayment,
    RecurringTransaction,
    Transfer,
    _run_ledger_shadow,
)
from ..filters.definitions import (
    ACCOUNT_DETAIL_FILTERS,
    ACCOUNT_LIST_FILTERS,
    TRANSFER_FILTERS,
)
from ..filters.engine import apply_filter_config
from ..posthog_utils import ph_capture
from ..reconciliation import is_reconcilable
from ..utils import get_exchange_rate
from .mixins import (
    HtmxPartialTemplateMixin,
    RecurringTransactionMixin,
    UUIDOrIntLookupMixin,
)
from .utils import (
    apply_date_filters,
    get_object_by_uuid_or_pk,
    redirect_to_uuid_url_if_needed,
)


# Balances kept by hand go stale; these are the account kinds that are (see account_types.STRATEGY)
_MANUAL_VALUATION_STRATEGIES = frozenset({
    STRATEGY.DEPOSIT, STRATEGY.HOLDINGS, STRATEGY.PHYSICAL_VALUATION, STRATEGY.INSURANCE_SURRENDER,
})
STALE_BALANCE_DAYS = 30
BILL_WINDOW_DAYS = 10
MATURITY_WINDOW_DAYS = 180


def _group_id_for(account_type):
    """The `?type=` value of the account group that contains this account type."""
    import re
    for group_name, choices in Account.ACCOUNT_TYPES:
        if any(code == account_type for code, _label in choices):
            return re.sub(r'[^A-Z0-9_]', '_', group_name.upper())
    return account_type


class AccountListView(HtmxPartialTemplateMixin, LoginRequiredMixin, ListView):
    model = Account
    template_name = 'expenses/account_list.html'
    htmx_template_name = 'expenses/partials/_account_list.html'
    context_object_name = 'accounts'

    def get_queryset(self):
        from .mixins import process_user_recurring_transactions
        process_user_recurring_transactions(self.request.user, max_catchup=2)
        status = self.request.GET.get('status', 'active')
        is_active = status != 'inactive'
        
        # Order by -is_pinned, created_at to ensure pinned accounts stay at top
        queryset = (
            Account.objects.select_related('user')
            .filter(user=self.request.user, is_active=is_active)
            .order_by('-is_pinned', 'created_at', 'id')
        )

        pinned = self.request.GET.get('pinned')
        if pinned == 'pinned':
            queryset = queryset.filter(is_pinned=True)

        selected_types = [t for t in self.request.GET.getlist('type') if t]
        if not selected_types and self.request.GET.get('type'):
            selected_types = [self.request.GET.get('type')]

        if selected_types:
            import re
            include_pinned = False
            matching_codes = []
            for t in selected_types:
                if t == 'PINNED':
                    include_pinned = True
                else:
                    group_codes = []
                    for g_name, choices in Account.ACCOUNT_TYPES:
                        g_id = re.sub(r'[^A-Z0-9_]', '_', g_name.upper())
                        if t == g_name or t == g_id:
                            group_codes = [c for c, _ in choices]
                            break
                    if group_codes:
                        matching_codes.extend(group_codes)
                    else:
                        matching_codes.append(t)

            type_q = Q()
            if matching_codes:
                type_q |= Q(account_type__in=matching_codes)
            if include_pinned:
                type_q |= Q(is_pinned=True)
            if type_q:
                queryset = queryset.filter(type_q)

        # Search by account name
        search_query = self.request.GET.get('search', '').strip()
        if search_query:
            queryset = queryset.filter(name__icontains=search_query)

        filtered_ids = list(queryset.values_list('id', flat=True))
        account_list = list(
            Account.objects.select_related('user', 'linked_physical_asset')
            .filter(user=self.request.user, is_active=is_active)
            .order_by('-is_pinned', 'created_at', 'id')
        )

        # Annotate locked status. The plan keeps the oldest accounts open, whatever the list is
        # currently sorted or filtered by, so the locked set comes from creation order alone.
        locked_ids = set()
        if self.request.user.is_authenticated:
            limit = get_limit(self.request.user.profile.active_tier, 'accounts')
            if limit != -1:
                oldest_first = Account.objects.filter(user=self.request.user, is_active=True).order_by('created_at', 'id')
                locked_ids = set(oldest_first.values_list('id', flat=True)[limit:])
        self._locked_ids = locked_ids
        for acc in account_list:
            acc.is_locked = acc.is_active and acc.id in locked_ids

        # The summary, tiles and tab counts always describe every account; the list itself is what the
        # search/filters leave. Both are the same objects, so each account is valued once.
        self._all_accounts = account_list
        by_id = {acc.id: acc for acc in account_list}
        return [by_id[pk] for pk in filtered_ids if pk in by_id]

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        accounts = context.get('accounts', [])
        user_currency = getattr(getattr(self.request.user, 'profile', None), 'currency', '₹')
        total_balance = Decimal('0.00')
        current_status = self.request.GET.get('status', 'active')
        display_balances = {}
        ledger_net_worth = None

        # Memoize exchange rate lookups within this request.
        # get_exchange_rate may hit an external API or DB for each unique currency pair;
        # with N accounts across K currencies this reduces calls from O(N×3) to O(K).
        _rate_cache: dict = {}

        def cached_rate(from_currency, to_currency):
            key = (from_currency, to_currency)
            if key not in _rate_cache:
                try:
                    _rate_cache[key] = get_exchange_rate(from_currency, to_currency)
                except Exception:
                    _rate_cache[key] = Decimal('1.0')
            return _rate_cache[key]

        if current_status == 'active':
            try:
                today_date = timezone.now().date()
                cache_key = f'account_net_worth_{self.request.user.id}'
                is_testing = getattr(settings, 'TESTING', False) or 'test' in sys.argv
                cached_res = cache.get(cache_key) if not is_testing else None
                if isinstance(cached_res, dict) and 'balances' in cached_res:
                    display_balances = cached_res['balances']
                    ledger_net_worth = cached_res.get('net_worth')
                else:
                    ledger_net_worth, display_balances = LedgerReadService.get_net_worth(self.request.user, as_of=today_date)
                    if not is_testing:
                        cache.set(cache_key, {'balances': display_balances, 'net_worth': ledger_net_worth}, 300)
            except Exception:
                display_balances = {}
                ledger_net_worth = None

        now = timezone.now()
        _deposit_codes = deposit_codes()

        # Newest statement check per account, in one query, for the "Reconciled" badge.
        all_accounts = getattr(self, '_all_accounts', accounts)
        shown_ids = {a.id for a in accounts}
        last_reconciliation = {}
        for rec in AccountReconciliation.objects.filter(account__in=all_accounts).order_by('-statement_date', '-created_at'):
            last_reconciliation.setdefault(rec.account_id, rec)

        for account in all_accounts:
            try:
                rate = cached_rate(account.currency, user_currency)
            except Exception:
                rate = Decimal('1.0')
            rate = Decimal(str(rate)) or Decimal('1.0')

            # display_balance is the account's balance in ITS OWN currency (that is what the row
            # shows next to the account's currency symbol); base_value is the same money in the
            # user's currency, signed (what you owe is negative), and is what every total adds up.
            if current_status == 'active' and account.id in display_balances:
                base_value = Decimal(str(display_balances[account.id]))
                if strategy_for(account.account_type) == STRATEGY.LOAN_OUTSTANDING:
                    base_value = -abs(base_value)  # the ledger reports a loan as the positive amount owed
                native = base_value if account.currency == user_currency else (base_value / rate).quantize(Decimal('0.01'))
            else:
                native = Decimal(str(account.balance))
                base_value = native * rate
            account.display_balance = native
            account.is_protection = False

            # Compute accrued value for fixed-income / deposit accounts
            if account.account_type in _deposit_codes:
                accrued = _compute_deposit_value(account, Decimal(str(account.display_balance)))
                deposit_principal = account.deposit_principal if account.deposit_principal is not None else Decimal(str(account.display_balance))
                if getattr(account, 'show_accrued_balance', True) and (accrued != deposit_principal or account.deposit_rate is not None):
                    account.accrued_value = accrued
                    account.effective_principal = deposit_principal
                    account.has_accrued_value = True
                    base_value = Decimal(str(accrued)) * rate
                else:
                    account.has_accrued_value = False

                if account.deposit_maturity_date and account.deposit_rate is not None:
                    from ..account_valuation import get_current_deposit
                    account.maturity_value = get_current_deposit(account, ledger_balance=Decimal(str(account.display_balance)), today=account.deposit_maturity_date)
                    account.has_maturity_value = True
                else:
                    account.has_maturity_value = False
            else:
                account.has_accrued_value = False
                account.has_maturity_value = False

            delta = now - account.updated_at
            account.days_since_update = delta.days
            # Balances kept by hand (deposits, holdings, property, insurance) go stale; cash and card
            # balances move with every transaction, so their age says nothing.
            account.update_target = 'detail' if strategy_for(account.account_type) == STRATEGY.HOLDINGS else 'edit'
            account.needs_update = bool(
                account.is_active and not account.is_locked
                and strategy_for(account.account_type) in _MANUAL_VALUATION_STRATEGIES
                and account.days_since_update >= STALE_BALANCE_DAYS
            )

            account.base_value = base_value
            if account.id in shown_ids:
                total_balance += base_value

            account.can_reconcile = account.is_active and not account.is_locked and is_reconcilable(account)
            account.last_reconciliation = last_reconciliation.get(account.id)

        # Group accounts using nested ACCOUNT_TYPES groups
        grouped_accounts = []
        flat_labels = {}
        for group_name, choices in Account.ACCOUNT_TYPES:
            for val, label in choices:
                flat_labels[val] = label

        seen_category_account_ids = set()
        import re

        # Surface Pinned Accounts section at top if any accounts are pinned
        pinned_accs = [a for a in accounts if getattr(a, 'is_pinned', False)]
        selected_type = self.request.GET.get('type', '')
        if pinned_accs and selected_type != 'PINNED':
            pinned_total = sum((acc.base_value for acc in pinned_accs), Decimal('0.00'))

            grouped_accounts.append({
                'type': 'PINNED',
                'label': _('Pinned Accounts'),
                'accounts': pinned_accs,
                'count': len(pinned_accs),
                'total': pinned_total.quantize(Decimal('0.01')),
                'is_open': True,
            })

        has_pinned = bool(pinned_accs) and selected_type != 'PINNED'

        for group_name, choices in Account.ACCOUNT_TYPES:
            group_codes = [val for val, _ in choices]
            group_accs = [a for a in accounts if a.account_type in group_codes and a.id not in seen_category_account_ids]
            if not group_accs:
                continue

            for a in group_accs:
                seen_category_account_ids.add(a.id)

            # Calculate group total balance in user currency
            group_total = sum((acc.base_value for acc in group_accs), Decimal('0.00'))

            group_type_id = re.sub(r'[^A-Z0-9_]', '_', group_name.upper())
            is_cash_or_bank = ('CASH' in group_type_id or 'BANK' in group_type_id)
            section_is_open = False if has_pinned else is_cash_or_bank

            grouped_accounts.append({
                'type': group_type_id,
                'label': group_name,
                'accounts': group_accs,
                'count': len(group_accs),
                'total': group_total.quantize(Decimal('0.01')),
                'is_open': section_is_open,
            })

        # Calculate category percentages, colors, icons, and short formatted totals for breakdown banner
        category_palette = [
            {'color': '#3b82f6', 'dot_class': 'bg-primary', 'icon': 'bi-bank', 'icon_bg': 'bg-primary-subtle text-primary'},
            {'color': '#d97706', 'dot_class': 'bg-warning', 'icon': 'bi-briefcase-fill', 'icon_bg': 'bg-warning-subtle text-warning-emphasis'},
            {'color': '#8b5cf6', 'dot_class': 'bg-purple', 'icon': 'bi-graph-up-arrow', 'icon_bg': 'bg-info-subtle text-info-emphasis'},
            {'color': '#ec4899', 'dot_class': 'bg-danger', 'icon': 'bi-credit-card', 'icon_bg': 'bg-danger-subtle text-danger'},
            {'color': '#ef4444', 'dot_class': 'bg-danger', 'icon': 'bi-cash-stack', 'icon_bg': 'bg-danger-subtle text-danger'},
        ]

        def _get_short_amount(amount):
            val = float(amount)
            if val >= 10000000:
                return f"{user_currency}{val/10000000:.2f}Cr"
            elif val >= 100000:
                return f"{user_currency}{val/100000:.2f}L"
            elif val >= 1000:
                return f"{user_currency}{val/1000:.1f}k"
            else:
                return f"{user_currency}{val:.0f}"

        # Shares of the bar are of what you hold; cards and loans (negative groups) get no segment.
        tb_float = float(sum((g['total'] for g in grouped_accounts if g['type'] != 'PINNED' and g['total'] > 0), Decimal('0.00')))
        if len(grouped_accounts) == 1 or selected_type:
            for g in grouped_accounts:
                g['is_open'] = True

        for idx, group in enumerate(grouped_accounts):
            gt_float = float(group['total'])
            group['pct'] = round((gt_float / tb_float * 100), 1) if tb_float > 0 and gt_float > 0 else 0
            group['owed'] = abs(group['total'])
            group['pct_int'] = int(round(group['pct']))
            group['short_total'] = _get_short_amount(group['total'])

            gtype = group['type']
            if gtype == 'PINNED':
                p = {'color': '#f59e0b', 'dot_class': 'bg-warning', 'icon': 'bi-pin-angle-fill', 'icon_bg': 'bg-warning-subtle text-warning-emphasis'}
            elif 'CASH' in gtype or 'BANK' in gtype:
                p = category_palette[0]
            elif 'DEPOSIT' in gtype or 'FIXED' in gtype:
                p = category_palette[1]
            elif 'INVEST' in gtype or 'MARKET' in gtype or 'EQUITY' in gtype:
                p = category_palette[2]
            elif 'CREDIT' in gtype:
                p = category_palette[3]
            elif 'LOAN' in gtype or 'LIABIL' in gtype:
                p = category_palette[4]
            else:
                p = category_palette[idx % len(category_palette)]

            group['color'] = p['color']
            group['dot_class'] = p['dot_class']
            group['icon'] = p['icon']
            group['icon_bg'] = p['icon_bg']

        # Apply sort to grouped accounts
        sort_by = self.request.GET.get('sort', 'attention')
        for group in grouped_accounts:
            if sort_by == 'balance_asc':
                group['accounts'] = sorted(group['accounts'], key=lambda a: float(a.base_value))
            elif sort_by == 'balance_desc':
                group['accounts'] = sorted(group['accounts'], key=lambda a: float(a.base_value), reverse=True)
            elif sort_by == 'name_asc':
                group['accounts'] = sorted(group['accounts'], key=lambda a: a.name.lower())
            elif sort_by == 'name_desc':
                group['accounts'] = sorted(group['accounts'], key=lambda a: a.name.lower(), reverse=True)

        context['grouped_accounts'] = grouped_accounts
        self._add_overview_context(
            context, accounts, grouped_accounts, current_status, user_currency, total_balance,
            display_balances, ledger_net_worth, cached_rate, sort_by,
        )
        context['account_types'] = Account.ACCOUNT_TYPES
        context['account_count'] = len(accounts)
        selected_type = self.request.GET.get('type', '')
        selected_label = flat_labels.get(selected_type, '')
        if not selected_label and selected_type:
            for g_name, choices in Account.ACCOUNT_TYPES:
                import re
                g_id = re.sub(r'[^A-Z0-9_]', '_', g_name.upper())
                if selected_type == g_name or selected_type == g_id:
                    selected_label = g_name
                    break
        context['selected_type'] = selected_type
        context['selected_type_label'] = selected_label
        context['current_status'] = current_status
        context['total_balance'] = total_balance.quantize(Decimal('0.01'))
        # Rows saved before the account became mandatory: they never moved a balance, so they are
        # the reason an account's balance can differ from what the transactions add up to.
        if current_status == 'active' and not self.request.GET.get('search') and not self.request.GET.get('type'):
            from ..models import CapitalEvent, Expense, Income
            context['unaccounted'] = {
                'expenses': Expense.objects.filter(user=self.request.user, account__isnull=True).count(),
                'income': Income.objects.filter(user=self.request.user, account__isnull=True).count(),
                'capital_events': CapitalEvent.objects.filter(user=self.request.user, account__isnull=True).count(),
            }
        context['total_balance_currency'] = user_currency

        selected_types = [t for t in self.request.GET.getlist('type') if t]
        if not selected_types and self.request.GET.get('type'):
            selected_types = [self.request.GET.get('type')]
        pinned = self.request.GET.get('pinned')
        search_query = self.request.GET.get('search', '').strip()

        applied_filters = {}
        if selected_types:
            applied_filters['type'] = selected_types
        if current_status != 'active':
            applied_filters['status'] = [current_status]
        if pinned == 'pinned':
            applied_filters['pinned'] = [pinned]
        if self.request.GET.get('needs_update') == '1':
            applied_filters['needs_update'] = ['1']

        context['filter_config'] = ACCOUNT_LIST_FILTERS
        context['applied_state'] = {
            'search': search_query,
            'sort': sort_by,
            'filters': applied_filters,
        }
        context['search_query'] = search_query
        context['sort_by'] = sort_by

        context['interest_summary'] = get_interest_summary(self.request.user)
        return context

    # --- overview (summary card, attention cards, tiles, tabs, flat list) -------------------------

    def _query_url(self, **changes):
        """Current list URL with the given query params replaced ('' or None removes one).

        `sort` and `status` are always written out, so the filter toolbar treats the link as an
        explicit state instead of restoring a stale one from the browser's storage.
        """
        params = self.request.GET.copy()
        params.pop('page', None)
        params['sort'] = params.get('sort') or 'attention'
        if (params.get('status') or 'active') == 'active':
            params.pop('status', None)
        for key, value in changes.items():
            if value in (None, ''):
                params.pop(key, None)
            else:
                params.setlist(key, value if isinstance(value, list) else [value])
        return f"{reverse('account-list')}?{params.urlencode()}"

    def _net_worth_change(self, ledger_net_worth, today):
        """Net worth now minus a month ago, from the ledger (None when there is nothing to compare)."""
        if ledger_net_worth is None:
            return None
        user = self.request.user
        key = f'account_net_worth_prev_{user.id}_{today.isoformat()}'
        is_testing = getattr(settings, 'TESTING', False) or 'test' in sys.argv
        previous = cache.get(key) if not is_testing else None
        if previous is None:
            try:
                month_ago = today - timedelta(days=30)
                previous, _balances = LedgerReadService.get_net_worth(user, as_of=month_ago)
                if not is_testing:
                    cache.set(key, previous, 600)
            except Exception:
                return None
        previous = Decimal(str(previous))
        current = Decimal(str(ledger_net_worth))
        if previous == 0 and current == 0:
            return None
        delta = (current - previous).quantize(Decimal('0.01'))
        pct = (delta / abs(previous) * 100).quantize(Decimal('0.1')) if previous != 0 else None
        return {'amount': abs(delta), 'pct': abs(pct) if pct is not None else None,
                'direction': 'up' if delta > 0 else ('down' if delta < 0 else 'flat')}

    def _attention_cards(self, today, user_currency, display_balances, cached_rate):
        """What needs a look soon: card bills, maturing deposits, balances that went stale.

        Always built from every active account, so a search or filter never hides a due bill.
        """
        user = self.request.user
        cards = []
        candidates = list(
            Account.objects.filter(user=user, is_active=True)
            .filter(Q(credit_card_billing_day__isnull=False) | Q(deposit_maturity_date__isnull=False))
        )

        def native_of(account):
            if account.id in display_balances:
                base_value = Decimal(str(display_balances[account.id]))
                rate = Decimal(str(cached_rate(account.currency, user_currency))) or Decimal('1')
                return base_value if account.currency == user_currency else (base_value / rate)
            return Decimal(str(account.balance))

        # Bills due within the next 10 days (cards that actually owe something)
        bills = []
        for acc in candidates:
            due = acc.next_billing_date
            if due is None:
                continue
            days = (due - today).days
            owed = -native_of(acc) if native_of(acc) < 0 else Decimal('0')
            if 0 <= days <= BILL_WINDOW_DAYS and owed > 0:
                bills.append((due, acc, owed.quantize(Decimal('0.01')), days))
        if bills:
            bills.sort(key=lambda b: (b[0], -b[2]))
            due, acc, owed, days = bills[0]
            cards.append({
                'kind': 'bill', 'badge': _('Bill due soon'), 'tone': 'warning',
                'title': acc.name, 'amount': owed, 'currency': acc.currency, 'date': due, 'days': days,
                'more': len(bills) - 1,
                'url': reverse('account-detail', args=[acc.uuid]),
            })

        # Deposits maturing within the next 6 months: the nearest date, with everything that lands on it
        maturing = []
        deposit_set = deposit_codes()
        for acc in candidates:
            if acc.account_type not in deposit_set or not acc.deposit_maturity_date:
                continue
            if acc.deposit_closed_date or not (today <= acc.deposit_maturity_date <= today + timedelta(days=MATURITY_WINDOW_DAYS)):
                continue
            native = native_of(acc)
            value = native
            if acc.deposit_rate is not None:
                try:
                    from ..account_valuation import get_current_deposit
                    value = get_current_deposit(acc, ledger_balance=native, today=acc.deposit_maturity_date)
                except Exception:
                    value = native
            maturing.append((acc.deposit_maturity_date, acc, Decimal(str(value))))
        if maturing:
            first_date = min(m[0] for m in maturing)
            same_day = [m for m in maturing if m[0] == first_date]
            currencies = {m[1].currency for m in same_day}
            all_fd = all(m[1].account_type in ('FD', 'FIXED_DEPOSIT') for m in same_day)
            cards.append({
                'kind': 'maturity', 'badge': _('Maturing'), 'tone': 'info',
                'count': len(same_day), 'is_fd': all_fd,
                'amount': sum((m[2] for m in same_day), Decimal('0')).quantize(Decimal('0.01')),
                'currency': currencies.pop() if len(currencies) == 1 else user_currency,
                'date': first_date, 'days': (first_date - today).days,
                'more': len(maturing) - len(same_day),
                'url': (reverse('account-detail', args=[same_day[0][1].uuid]) if len(same_day) == 1
                        else self._query_url(type=_group_id_for(same_day[0][1].account_type), pinned='', needs_update='')),
            })
        return cards

    def _stale_balance_count(self):
        """How many hand-kept balances are 30+ days old, over every active account (not just the filtered ones)."""
        cutoff = timezone.now() - timedelta(days=STALE_BALANCE_DAYS)
        locked = getattr(self, '_locked_ids', set())
        rows = Account.objects.filter(user=self.request.user, is_active=True, updated_at__lte=cutoff).values_list('id', 'account_type')
        return sum(1 for pk, code in rows if pk not in locked and strategy_for(code) in _MANUAL_VALUATION_STRATEGIES)

    @staticmethod
    def _overview_groups(all_accounts):
        """Account groups with totals in the user's currency, share of what is held, and a colour."""
        import re
        palette = {
            'CASH': '#3b82f6', 'BANK': '#3b82f6', 'DEPOSIT': '#d97706', 'FIXED': '#d97706',
            'INVEST': '#8b5cf6', 'MARKET': '#8b5cf6', 'EQUITY': '#8b5cf6', 'CREDIT': '#ec4899',
            'LOAN': '#ef4444', 'LIABIL': '#ef4444', 'PHYSICAL': '#14b8a6', 'INSURANCE': '#64748b',
        }
        fallback = ['#3b82f6', '#d97706', '#8b5cf6', '#14b8a6', '#64748b']
        groups, seen = [], set()
        for group_name, choices in Account.ACCOUNT_TYPES:
            codes = {val for val, _label in choices}
            members = [a for a in all_accounts if a.account_type in codes and a.id not in seen]
            if not members:
                continue
            seen.update(a.id for a in members)
            gtype_for_rows = re.sub(r'[^A-Z0-9_]', '_', group_name.upper())
            for a in members:
                a.group_type = gtype_for_rows
            total = sum((a.base_value for a in members), Decimal('0.00')).quantize(Decimal('0.01'))
            gtype = re.sub(r'[^A-Z0-9_]', '_', group_name.upper())
            color = next((c for token, c in palette.items() if token in gtype), fallback[len(groups) % len(fallback)])
            groups.append({'type': gtype, 'label': group_name, 'accounts': members, 'count': len(members),
                           'total': total, 'owed': abs(total), 'color': color})
        held = float(sum((g['total'] for g in groups if g['total'] > 0), Decimal('0.00')))
        for g in groups:
            g['pct'] = round(float(g['total']) / held * 100, 1) if held > 0 and g['total'] > 0 else 0
            g['pct_int'] = int(round(g['pct']))
        return groups

    def _add_overview_context(self, context, accounts, grouped_accounts, current_status, user_currency,
                              total_balance, display_balances, ledger_net_worth, cached_rate, sort_by):
        today = timezone.localdate()
        request = self.request

        selected_type = request.GET.get('type', '')
        needs_update_only = request.GET.get('needs_update') == '1'
        pinned_only = request.GET.get('pinned') == 'pinned'

        all_accounts = getattr(self, '_all_accounts', accounts)
        groups = self._overview_groups(all_accounts)
        context['overview_groups'] = groups
        context['overall_total'] = sum((a.base_value for a in all_accounts), Decimal('0.00')).quantize(Decimal('0.01'))
        context['overall_count'] = len(all_accounts)

        # Tiles: one per group (what you hold, then what you owe); a click filters the list to it
        tiles = []
        for group in groups:
            is_liability = any(token in group['type'] for token in ('CREDIT', 'LOAN'))
            if 'CREDIT' in group['type']:
                tile_label = _('Owed on cards')
                owing = sum(1 for a in group['accounts'] if a.base_value < 0)
                note = _('%(owing)s of %(count)s cards') % {'owing': owing, 'count': group['count']}
            elif 'LOAN' in group['type']:
                tile_label = _('Owed on loans')
                note = ngettext('%(count)s account', '%(count)s accounts', group['count']) % {'count': group['count']}
            else:
                tile_label = group['label']
                note = ngettext('%(count)s account', '%(count)s accounts', group['count']) % {'count': group['count']}
                if group['pct_int']:
                    note = f"{note} · {group['pct_int']}%"
            tiles.append({
                'type': group['type'], 'label': tile_label, 'note': note, 'total': group['total'],
                'owed': group['owed'], 'is_liability': is_liability, 'color': group['color'],
                'selected': selected_type == group['type'],
                'url': self._query_url(type='' if selected_type == group['type'] else group['type'],
                                       needs_update='', pinned=''),
            })
        context['tiles'] = tiles

        # Allocation summary: what is held vs. what is owed
        assets_total = sum((g['total'] for g in groups if g['total'] > 0), Decimal('0.00'))
        owed_total = sum((g['owed'] for g in groups if g['total'] < 0), Decimal('0.00'))
        context['assets_total'] = assets_total.quantize(Decimal('0.01'))
        context['owed_total'] = owed_total.quantize(Decimal('0.01'))
        context['owed_label'] = _('cards and loans') if any('LOAN' in g['type'] and g['total'] < 0 for g in groups) \
            else _('cards')

        if current_status == 'active':
            context['net_worth_change'] = self._net_worth_change(ledger_net_worth, today)
            context['attention_cards'] = self._attention_cards(today, user_currency, display_balances, cached_rate)
            context['stale_count'] = self._stale_balance_count()
            context['stale_url'] = self._query_url(pinned='', needs_update='1', type='')
        else:
            context['net_worth_change'] = None
            context['attention_cards'] = []
            context['stale_count'] = 0

        # Tabs. The counts ignore the tab itself but follow type/search, so they match what a click shows.
        base = list(all_accounts)
        context['tab_counts'] = {
            'all': len(all_accounts),
            'pinned': sum(1 for a in base if a.is_pinned),
            'needs_update': sum(1 for a in base if a.needs_update),
        }
        if not pinned_only and not needs_update_only:
            context['active_tab'] = 'all'
        else:
            context['active_tab'] = 'needs_update' if needs_update_only else 'pinned'
        context['tab_urls'] = {
            'all': self._query_url(pinned='', needs_update=''),
            'pinned': self._query_url(pinned='pinned', needs_update=''),
            'needs_update': self._query_url(pinned='', needs_update='1'),
        }

        # The flat list: needs-update first, then balance, unless another order was picked
        # Every account is rendered; the ones outside the current view are only hidden. That lets the
        # tiles and tabs switch the list instantly in the browser instead of asking the server again.
        shown_ids = {a.id for a in accounts}
        rows = list(all_accounts)
        if sort_by == 'attention':
            rows.sort(key=lambda a: (not a.needs_update, -float(a.base_value)))
        elif sort_by == 'balance_asc':
            rows.sort(key=lambda a: float(a.base_value))
        elif sort_by == 'balance_desc':
            rows.sort(key=lambda a: float(a.base_value), reverse=True)
        elif sort_by == 'name_asc':
            rows.sort(key=lambda a: a.name.lower())
        elif sort_by == 'name_desc':
            rows.sort(key=lambda a: a.name.lower(), reverse=True)
        position = 0
        for row in rows:
            row.in_view = row.id in shown_ids and (row.needs_update or not needs_update_only)
            row.view_position = position if row.in_view else None
            if row.in_view:
                position += 1
        for row in rows:
            row.is_last_in_view = row.in_view and row.view_position == position - 1
        protection = [a for a in rows if is_protection_policy(a)]
        if protection:
            ledger = LedgerReadService.get_account_balances(protection)
            by_asset = premiums_paid_by_asset([a.linked_physical_asset_id for a in protection])
            for a in protection:
                a.is_protection = True
                a.premiums_paid = get_premiums_paid(a, ledger.get(a.id, a.balance), by_asset=by_asset)
        context['display_accounts'] = rows
        context['show_policy_column'] = any(
            getattr(r.linked_physical_asset, 'policy_number', None) for r in rows
        )
        context['display_count'] = position
        context['view_type'] = selected_type if selected_type in {t['type'] for t in tiles} else ''
        context['sort_label'] = {
            'attention': _('Sorted by needs update first, then balance'),
            'balance_desc': _('Sorted by balance, highest first'),
            'balance_asc': _('Sorted by balance, lowest first'),
            'name_asc': _('Sorted by name, A to Z'),
            'name_desc': _('Sorted by name, Z to A'),
        }.get(sort_by, '')
        # The tiles and tabs already show a group / pinned / needs-update choice; the full filter panel
        # opens by itself only for what they cannot express.
        tile_types = {t['type'] for t in tiles}
        type_values = [t for t in request.GET.getlist('type') if t]
        custom_type = bool(type_values) and not (len(type_values) == 1 and type_values[0] in tile_types)
        context['filters_active'] = bool(custom_type or current_status != 'active' or sort_by != 'attention')



class AccountCreateView(LoginRequiredMixin, CreateView):
    model = Account
    form_class = AccountForm
    template_name = 'expenses/account_form.html'
    success_url = reverse_lazy('account-list')

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['user'] = self.request.user
        return kwargs

    def form_valid(self, form):
        if not self.request.user.profile.can_add_account():
            limit = get_limit(self.request.user.profile.active_tier, 'accounts')
            messages.error(self.request, _("You have reached the limit of %(limit)s accounts for your current plan. Please upgrade to add more.") % {'limit': limit})
            return redirect('pricing')
        form.instance.user = self.request.user
        messages.success(self.request, _("Account created successfully!"))
        response = super().form_valid(form)
        if self.object:
            ph_capture(self.request.user, 'account_created', {'account_type': self.object.account_type, 'currency': self.object.currency, 'has_credit_limit': bool(getattr(self.object, 'credit_limit', None))})
        return response

class AccountUpdateView(LoginRequiredMixin, UUIDOrIntLookupMixin, UpdateView):
    model = Account
    form_class = AccountForm
    template_name = 'expenses/account_form.html'
    success_url = reverse_lazy('account-list')

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['user'] = self.request.user
        return kwargs

    def get_queryset(self):
        return Account.objects.filter(user=self.request.user)

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return super().dispatch(request, *args, **kwargs)
            
        account = self.get_object()
        if request.user.profile.is_account_locked(account):
            messages.error(request, _("This account is locked. Please upgrade your plan to modify it."))
            return redirect('pricing')
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        old_state = self.get_object()
        old_balance = old_state.balance
        messages.success(self.request, _("Account updated successfully!"))
        response = super().form_valid(form)
        ph_capture(self.request.user, 'account_updated', {'account_type': self.object.account_type})

        # Manual balance edits should treat the entered balance as source of truth.
        # Offset any existing drift by targeting the current ledger-derived balance.
        if getattr(settings, 'LEDGER_WRITE_ENABLED', False):
            from ..ledger_service import LedgerPostingService

            with transaction.atomic():
                locked_account = Account.objects.select_for_update().get(pk=self.object.pk)
                try:
                    ledger_balance_before_adjustment = LedgerReadService.get_account_ledger_delta(locked_account)
                except Exception:
                    ledger_balance_before_adjustment = old_balance

                target_balance = locked_account.balance.quantize(Decimal('0.01'))
                balance_delta = (target_balance - ledger_balance_before_adjustment).quantize(Decimal('0.01'))

                if balance_delta == Decimal('0.00'):
                    return response

                version_token = f"ACCOUNT_BALANCE_EDIT-{int(locked_account.updated_at.timestamp() * 1000000)}"

                def _post_shadow_entry():
                    LedgerPostingService.post_account_balance_adjustment(
                        account=locked_account,
                        delta=balance_delta,
                        version_token=version_token,
                        metadata={
                            'kind': 'MANUAL_BALANCE_EDIT',
                            'actor_user_id': self.request.user.id,
                            'old_balance': str(old_balance),
                            'new_balance': str(locked_account.balance),
                            'ledger_balance_before_adjustment': str(ledger_balance_before_adjustment),
                        },
                    )

                _run_ledger_shadow(
                    _post_shadow_entry,
                    source_type='ADJUSTMENT',
                    source_id=locked_account.id,
                    action='ACCOUNT_BALANCE_EDIT',
                    payload={
                        'handler': 'account_balance_edit',
                        'version_token': version_token,
                        'account': {
                            'account_id': locked_account.id,
                            'user_id': locked_account.user_id,
                            'currency': locked_account.currency,
                            'old_balance': str(old_balance),
                            'new_balance': str(locked_account.balance),
                            'ledger_balance_before_adjustment': str(ledger_balance_before_adjustment),
                            'delta': str(balance_delta),
                        },
                    },
                )

        return response

class AccountDeleteView(LoginRequiredMixin, UUIDOrIntLookupMixin, DeleteView):
    model = Account
    template_name = 'expenses/account_delete_confirm.html'
    success_url = reverse_lazy('account-list')

    def get_queryset(self):
        return Account.objects.filter(user=self.request.user, is_active=True)

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return super().dispatch(request, *args, **kwargs)
            
        account = self.get_object()
        if request.user.profile.is_account_locked(account):
            messages.error(request, _("This account is locked. Please upgrade your plan to delete it."))
            return redirect('pricing')
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        success_url = self.get_success_url()
        self.object.is_active = False
        self.object.save()
        messages.success(self.request, _("Account deleted successfully."))
        ph_capture(self.request.user, 'account_deleted', {})
        return redirect(success_url)


class AccountRestoreView(LoginRequiredMixin, View):
    def post(self, request, pk):
        account = get_object_by_uuid_or_pk(Account, pk, user=request.user, is_active=False)
        redirect_response = redirect_to_uuid_url_if_needed(request, account)
        if redirect_response:
            return redirect_response
        if not request.user.profile.can_add_account():
            limit = get_limit(request.user.profile.active_tier, 'accounts')
            messages.error(request, _("You have reached the limit of %(limit)s accounts for your current plan. Please upgrade to restore this account.") % {'limit': limit})
            return redirect('pricing')
            
        if Account.objects.filter(user=request.user, name__iexact=account.name, is_active=True).exists():
            messages.error(request, _("An account named '%(name)s' already exists. Rename it before restoring this one.") % {'name': account.name})
            return redirect(f"{reverse_lazy('account-list')}?status=inactive")

        account.is_active = True
        account.save()
        messages.success(request, _("Account restored successfully!"))
        ph_capture(request.user, 'account_restored', {})
        return redirect('account-list')


class AccountPinToggleView(LoginRequiredMixin, View):
    """Toggle the pinned status of an account."""

    def post(self, request, pk):
        return self._toggle_pin(request, pk)

    def get(self, request, pk):
        return self._toggle_pin(request, pk)

    def _toggle_pin(self, request, pk):
        account = get_object_by_uuid_or_pk(Account, pk, user=request.user)
        redirect_response = redirect_to_uuid_url_if_needed(request, account)
        if redirect_response:
            return redirect_response

        account.is_pinned = not account.is_pinned
        account.save(update_fields=['is_pinned'])

        if account.is_pinned:
            messages.success(request, _("Account '%(name)s' pinned to top.") % {'name': account.name})
            ph_capture(request.user, 'account_pinned', {'account_id': str(account.uuid)})
        else:
            messages.success(request, _("Account '%(name)s' unpinned.") % {'name': account.name})
            ph_capture(request.user, 'account_unpinned', {'account_id': str(account.uuid)})

        is_htmx = (
            str(request.headers.get('HX-Request', '')).lower() == 'true' or
            str(request.META.get('HTTP_HX_REQUEST', '')).lower() == 'true'
        )
        if is_htmx:
            if not request.GET and request.POST:
                request.GET = request.POST
            request.method = 'GET'
            view = AccountListView.as_view()
            return view(request)

        return redirect(request.META.get('HTTP_REFERER') or 'account-list')


class AccountQuickCreateView(LoginRequiredMixin, View):
    """AJAX endpoint for creating an account from a modal and returning JSON."""

    def post(self, request):
        if not request.user.profile.can_add_account():
            return JsonResponse({
                'success': False, 
                'errors': {'__all__': [_("Account limit reached. Please upgrade to add more.")]}
            }, status=403)
            
        form = AccountForm(request.POST, user=request.user)
        if form.is_valid():
            account = form.save(commit=False)
            account.user = request.user
            account.save()
            ph_capture(request.user, 'account_quick_created', {'account_type': account.account_type})
            return JsonResponse({
                'success': True,
                'id': account.pk,
                'name': str(account),
            })
        return JsonResponse({'success': False, 'errors': form.errors}, status=400)


class TransferAllocationMixin:
    """"Allocate to funds" on the transfer form: how much of a transfer into an investment account went to
    which fund. The purchases are saved with the transfer, or not at all."""

    def get_context_data(self, **kwargs):
        from ..account_types import holdings_codes
        from ..models import Holding

        ctx = super().get_context_data(**kwargs)
        accounts = {}
        for h in (Holding.objects.filter(account__user=self.request.user, is_active=True,
                                         account__account_type__in=holdings_codes(), account__is_active=True)
                  .select_related('account').order_by('instrument_name')):
            entry = accounts.setdefault(str(h.account_id), {'currency': h.account.currency, 'holdings': []})
            entry['holdings'].append({'id': h.pk, 'name': h.instrument_name})
        for a in Account.objects.filter(user=self.request.user, is_active=True,
                                        account_type__in=holdings_codes()):
            accounts.setdefault(str(a.pk), {'currency': a.currency, 'holdings': []})
        ctx['allocation_data'] = accounts
        ctx['existing_allocations'] = (
            list(self.object.holding_allocations.select_related('holding').order_by('date', 'id'))
            if getattr(self, 'object', None) and self.object.pk else []
        )
        return ctx

    def save_with_allocations(self, form, save):
        """Run ``save`` (the parent's form_valid) and record the allocation rows in one transaction."""
        from .. import holdings_service as hs
        from ..nav_provider import NAVFetchService

        with transaction.atomic():
            response = save()
            new_holdings = []
            if form.allocation_rows:
                _purchases, new_holdings = hs.allocate_transfer(self.object, form.allocation_rows)
        for holding in new_holdings:
            if holding.scheme_code:
                NAVFetchService().fetch_scheme(holding.scheme_code, force=True)
        if form.allocation_rows:
            messages.success(self.request, ngettext(
                "%(n)d fund updated from this transfer.", "%(n)d funds updated from this transfer.",
                len(form.allocation_rows)) % {'n': len(form.allocation_rows)})
            ph_capture(self.request.user, 'transfer_allocated', {'funds': len(form.allocation_rows)})
        return response


class TransferCreateView(TransferAllocationMixin, LoginRequiredMixin, CreateView):
    model = Transfer
    form_class = TransferForm
    template_name = 'expenses/transfer_form.html'
    success_url = reverse_lazy('transfer-list')

    def get_success_url(self):
        next_url = self.request.GET.get('next')
        if next_url:
            return get_safe_redirect_url(self.request, next_url, reverse_lazy('transfer-list'))
        return super().get_success_url()

    def dispatch(self, request, *args, **kwargs):
        if request.user.username == 'demo':
            messages.warning(request, _("Inter-account transfers are disabled in the demo to keep things simple. Please use Goal Contributions instead!"))
            return redirect('home')
        return super().dispatch(request, *args, **kwargs)

    def get_initial(self):
        initial = super().get_initial()
        from_account_val = self.request.GET.get('from_account')
        if from_account_val:
            import uuid
            try:
                # Try to parse as UUID first
                uuid.UUID(from_account_val)
                account = Account.objects.get(uuid=from_account_val, user=self.request.user)
                initial['from_account'] = account.pk
            except (ValueError, TypeError, Account.DoesNotExist):
                # Fallback to pk just in case
                try:
                    account = Account.objects.get(pk=int(from_account_val), user=self.request.user)
                    initial['from_account'] = account.pk
                except (ValueError, TypeError, Account.DoesNotExist):
                    pass
        return initial

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['user'] = self.request.user
        return kwargs

    def form_valid(self, form):
        form.instance.user = self.request.user

        from ..holdings_service import HoldingError
        try:
            response = self.save_with_allocations(form, lambda: super(TransferCreateView, self).form_valid(form))
            messages.success(self.request, _("Transfer completed successfully!"))
            ph_capture(self.request.user, 'transfer_created', {'currency': getattr(self.object, 'currency', ''), 'is_cross_currency': bool(getattr(self.object, 'exchange_rate', None) and self.object.exchange_rate != 1)})
            return response
        except IntegrityError:
            messages.error(self.request, _("Duplicate transfer found! This transfer has already been completed."))
            return self.form_invalid(form)
        except HoldingError as exc:
            messages.error(self.request, str(exc))
            return self.form_invalid(form)
        except (RuntimeError, ValidationError):
            messages.error(self.request, _("Unable to complete transfer because currency conversion failed or transfer data is invalid."))
            return self.form_invalid(form)

class TransferListView(HtmxPartialTemplateMixin, LoginRequiredMixin, RecurringTransactionMixin, ListView):
    model = Transfer
    template_name = 'expenses/transfer_list.html'
    htmx_template_name = 'expenses/partials/_transfer_list.html'
    context_object_name = 'transfers'
    paginate_by = 20

    def get_queryset(self):
        base_qs = Transfer.objects.filter(user=self.request.user).select_related('from_account', 'to_account').prefetch_related('holding_allocations__holding')
        queryset, self.applied_state = apply_filter_config(base_qs, self.request, TRANSFER_FILTERS)
        return queryset

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        stats = self.object_list.aggregate(count=Count('id'), moved=Sum('converted_amount'))
        ctx['filtered_count'] = stats['count']
        ctx['filtered_amount'] = stats['moved'] or Decimal('0.00')
        ctx['filter_config'] = TRANSFER_FILTERS
        ctx['applied_state'] = self.applied_state
        ctx['search_query'] = self.applied_state.get('search', '')
        return ctx

class TransferUpdateView(TransferAllocationMixin, LoginRequiredMixin, UUIDOrIntLookupMixin, UpdateView):
    model = Transfer
    form_class = TransferForm
    template_name = 'expenses/transfer_form.html'
    success_url = reverse_lazy('transfer-list')

    def get_success_url(self):
        next_url = self.request.GET.get('next')
        if next_url:
            return get_safe_redirect_url(self.request, next_url, reverse_lazy('transfer-list'))
        return super().get_success_url()

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['user'] = self.request.user
        return kwargs

    def get_queryset(self):
        return Transfer.objects.filter(user=self.request.user)

    def form_valid(self, form):
        from ..holdings_service import HoldingError
        try:
            response = self.save_with_allocations(form, lambda: super(TransferUpdateView, self).form_valid(form))
            messages.success(self.request, _("Transfer updated successfully!"))
            ph_capture(self.request.user, 'transfer_updated', {})
            return response
        except HoldingError as exc:
            messages.error(self.request, str(exc))
            return self.form_invalid(form)
        except (RuntimeError, ValidationError):
            messages.error(self.request, _("Unable to update transfer because currency conversion failed or transfer data is invalid."))
            return self.form_invalid(form)

class TransferDeleteView(LoginRequiredMixin, UUIDOrIntLookupMixin, DeleteView):
    model = Transfer
    template_name = 'expenses/transfer_confirm_delete.html'
    success_url = reverse_lazy('transfer-list')

    def get_queryset(self):
        return Transfer.objects.filter(user=self.request.user)

    def form_valid(self, form):
        messages.success(self.request, _("Transfer deleted successfully!"))
        response = super().form_valid(form)
        ph_capture(self.request.user, 'transfer_deleted', {})
        return response

    def get_success_url(self):
        next_url = self.request.GET.get('next') or self.request.POST.get('next')
        if next_url:
            return get_safe_redirect_url(self.request, next_url, reverse_lazy('transfer-list'))
        return reverse_lazy('transfer-list')

class AccountDetailView(LoginRequiredMixin, View):
    template_name = 'expenses/account_detail.html'

    def _build_ledger_source(self, queryset, ledger_type, ledger_rank):
        return (
            queryset.annotate(
                ledger_type=Value(ledger_type, output_field=CharField()),
                ledger_rank=Value(ledger_rank, output_field=IntegerField()),
            )
            .values('date', 'ledger_type', 'ledger_rank', 'id', 'amount')
            .order_by()
        )

    def _hydrate_ledger_page(self, ledger_rows, account, user):
        rows_by_type = defaultdict(list)
        for row in ledger_rows:
            rows_by_type[row['ledger_type']].append(row)

        hydrated_by_type = {}

        expense_rows = rows_by_type.get('EXPENSE', [])
        if expense_rows:
            hydrated_by_type['EXPENSE'] = {
                item.id: item
                for item in Expense.objects.filter(
                    user=user,
                    account=account,
                    pk__in=[row['id'] for row in expense_rows],
                ).select_related('category_fk')
            }

        income_rows = rows_by_type.get('INCOME', [])
        if income_rows:
            hydrated_by_type['INCOME'] = {
                item.id: item
                for item in Income.objects.filter(
                    user=user,
                    account=account,
                    pk__in=[row['id'] for row in income_rows],
                ).select_related('source_fk')
            }

        transfer_out_rows = rows_by_type.get('TRANSFER_OUT', [])
        if transfer_out_rows:
            hydrated_by_type['TRANSFER_OUT'] = {
                item.id: item
                for item in Transfer.objects.filter(
                    user=user,
                    from_account=account,
                    pk__in=[row['id'] for row in transfer_out_rows],
                ).select_related('to_account')
            }

        transfer_in_rows = rows_by_type.get('TRANSFER_IN', [])
        if transfer_in_rows:
            hydrated_by_type['TRANSFER_IN'] = {
                item.id: item
                for item in Transfer.objects.filter(
                    user=user,
                    to_account=account,
                    pk__in=[row['id'] for row in transfer_in_rows],
                ).select_related('from_account')
            }

        savings_rows = rows_by_type.get('SAVINGS', [])
        if savings_rows:
            hydrated_by_type['SAVINGS'] = {
                item.id: item
                for item in GoalContribution.objects.filter(
                    goal__user=user,
                    account=account,
                    pk__in=[row['id'] for row in savings_rows],
                ).select_related('goal')
            }

        loan_rows = rows_by_type.get('LOAN_REPAYMENT', [])
        if loan_rows:
            hydrated_by_type['LOAN_REPAYMENT'] = {
                item.id: item
                for item in LoanRepayment.objects.filter(
                    loan__user=user,
                    from_account=account,
                    pk__in=[row['id'] for row in loan_rows],
                ).select_related('loan')
            }

        capital_rows = rows_by_type.get('CAPITAL_EVENT', [])
        if capital_rows:
            hydrated_by_type['CAPITAL_EVENT'] = {
                item.id: item
                for item in CapitalEvent.objects.filter(
                    user=user,
                    account=account,
                    pk__in=[row['id'] for row in capital_rows],
                ).select_related('linked_loan')
            }

        hydrated = []
        for row in ledger_rows:
            item = hydrated_by_type[row['ledger_type']][row['id']]
            item.__ledger_type = row['ledger_type']
            hydrated.append(item)
        return hydrated
        
    def get(self, request, pk):
        from .mixins import process_user_recurring_transactions
        if request.user.is_authenticated:
            process_user_recurring_transactions(request.user, max_catchup=2)
            
        account = get_object_by_uuid_or_pk(
            Account.objects.prefetch_related('holdings'),
            pk,
            user=request.user
        )
        redirect_response = redirect_to_uuid_url_if_needed(request, account)
        if redirect_response:
            return redirect_response
        if request.user.is_authenticated and request.user.profile.is_account_locked(account):
            messages.error(request, _("This account is locked. Please upgrade your plan to view its history."))
            return redirect('pricing')

        if account.is_active:
            try:
                account.display_balance = LedgerReadService.get_account_balance(account)
            except Exception:
                account.display_balance = account.balance
        else:
            account.display_balance = account.balance

        if account.account_type in deposit_codes():
            accrued = _compute_deposit_value(account, Decimal(str(account.display_balance)))
            deposit_principal = account.deposit_principal if account.deposit_principal is not None else Decimal(str(account.display_balance))
            if getattr(account, 'show_accrued_balance', True) and (accrued != deposit_principal or account.deposit_rate is not None):
                account.accrued_value = accrued
                account.effective_principal = deposit_principal
                account.has_accrued_value = True
            else:
                account.has_accrued_value = False

            if account.deposit_maturity_date and account.deposit_rate is not None:
                from ..account_valuation import get_current_deposit
                account.maturity_value = get_current_deposit(account, ledger_balance=Decimal(str(account.display_balance)), today=account.deposit_maturity_date)
                account.has_maturity_value = True
            else:
                account.has_maturity_value = False
        else:
            account.has_accrued_value = False
            account.has_maturity_value = False

        if strategy_for(account.account_type) == STRATEGY.INSURANCE_SURRENDER and account.is_active:
            ledger_balance = Decimal(str(account.display_balance))
            account.is_protection = is_protection_policy(account)
            by_asset = premiums_paid_by_asset([account.linked_physical_asset_id])
            account.premiums_paid = get_premiums_paid(account, ledger_balance, by_asset=by_asset)
            account.display_balance = get_current_insurance_surrender(account, ledger_balance=ledger_balance, by_asset=by_asset)

        search_query = request.GET.get('search') or request.GET.get('q', '').strip()
        time_period = request.GET.get('time_period', 'all')
        start_date = request.GET.get('start_date') or ''
        end_date = request.GET.get('end_date') or ''
        sort_by = request.GET.get('sort', 'date_desc')

        selected_tx_types = [t for t in request.GET.getlist('tx_type') if t]
        if not selected_tx_types and request.GET.get('tx_type'):
            selected_tx_types = [request.GET.get('tx_type')]

        selected_categories = [c for c in request.GET.getlist('category') if c]
        if not selected_categories and request.GET.get('category'):
            selected_categories = [request.GET.get('category')]

        selected_amounts = [a for a in request.GET.getlist('amount_range') if a]
        if not selected_amounts and request.GET.get('amount_range'):
            selected_amounts = [request.GET.get('amount_range')]

        # Get all expenses, incomes, and transfers for this account
        expenses = Expense.objects.filter(user=request.user, account=account).select_related('category_fk').order_by('-date')
        incomes = Income.objects.filter(user=request.user, account=account).select_related('source_fk').order_by('-date')
        transfers_from = Transfer.objects.filter(user=request.user, from_account=account).select_related('to_account').order_by('-date')
        transfers_to = Transfer.objects.filter(user=request.user, to_account=account).select_related('from_account').order_by('-date')
        contributions = GoalContribution.objects.filter(goal__user=request.user, account=account).select_related('goal').order_by('-date')
        loan_repayments = LoanRepayment.objects.filter(loan__user=request.user, from_account=account).select_related('loan').order_by('-date')
        capital_events = CapitalEvent.objects.filter(user=request.user, account=account).select_related('linked_loan').order_by('-date')

        if selected_tx_types:
            if 'EXPENSE' not in selected_tx_types:
                expenses = Expense.objects.none()
            if 'INCOME' not in selected_tx_types:
                incomes = Income.objects.none()
            if 'TRANSFER' not in selected_tx_types:
                transfers_from = Transfer.objects.none()
                transfers_to = Transfer.objects.none()
            if 'SAVINGS' not in selected_tx_types:
                contributions = GoalContribution.objects.none()
            if 'LOAN' not in selected_tx_types:
                loan_repayments = LoanRepayment.objects.none()
            if 'CAPITAL' not in selected_tx_types:
                capital_events = CapitalEvent.objects.none()

        if selected_categories:
            expenses = expenses.filter(category__in=selected_categories)
            incomes = incomes.filter(Q(source_type__in=selected_categories) | Q(source__in=selected_categories))
            transfers_from = Transfer.objects.none()
            transfers_to = Transfer.objects.none()
            contributions = GoalContribution.objects.none()
            loan_repayments = LoanRepayment.objects.none()
            capital_events = CapitalEvent.objects.none()

        if selected_amounts:
            from ..filters.definitions import filter_amount_range
            expenses = filter_amount_range(expenses, selected_amounts)
            incomes = filter_amount_range(incomes, selected_amounts)
            transfers_from = filter_amount_range(transfers_from, selected_amounts)
            transfers_to = filter_amount_range(transfers_to, selected_amounts)
            contributions = filter_amount_range(contributions, selected_amounts)
            loan_repayments = filter_amount_range(loan_repayments, selected_amounts)
            capital_events = filter_amount_range(capital_events, selected_amounts)

        if time_period != 'all':
            expenses = apply_date_filters(expenses, request)
            incomes = apply_date_filters(incomes, request)
            transfers_from = apply_date_filters(transfers_from, request)
            transfers_to = apply_date_filters(transfers_to, request)
            contributions = apply_date_filters(contributions, request)
            loan_repayments = apply_date_filters(loan_repayments, request)
            capital_events = apply_date_filters(capital_events, request)

        if search_query:
            expenses = expenses.filter(Q(description__icontains=search_query) | Q(category__icontains=search_query))
            incomes = incomes.filter(Q(description__icontains=search_query) | Q(source__icontains=search_query) | Q(source_type__icontains=search_query))
            transfers_from = transfers_from.filter(description__icontains=search_query)
            transfers_to = transfers_to.filter(description__icontains=search_query)
            contributions = contributions.filter(Q(goal__name__icontains=search_query))
            loan_repayments = loan_repayments.filter(Q(loan__name__icontains=search_query))
            capital_events = capital_events.filter(Q(note__icontains=search_query) | Q(subtype__icontains=search_query))

        base_currency = request.user.profile.currency if hasattr(request.user, 'profile') else '₹'
        
        # Calculate Net Total for Filtered Items (In Account's Currency)
        from django.db.models import Sum

        def _get_total(qs, field='amount'):
            res = qs.aggregate(total=Sum(field))['total']
            return res if res is not None else Decimal('0.00')

        # Memoized FX lookup dictionary to eliminate N+1 queries in loops below
        _account_fx_cache = {}
        def _get_account_fx(from_curr, to_curr):
            pair = (from_curr, to_curr)
            if pair not in _account_fx_cache:
                try:
                    _account_fx_cache[pair] = get_exchange_rate(from_curr, to_curr)
                except Exception:
                    _account_fx_cache[pair] = Decimal('1.0')
            return _account_fx_cache[pair]

        if expenses.filter(~Q(currency=account.currency)).exists():
            exp_total = Decimal('0.00')
            for e in expenses:
                if e.currency != account.currency:
                    rate = _get_account_fx(e.currency, account.currency)
                    exp_total += (e.amount * rate).quantize(Decimal('0.01'))
                else:
                    exp_total += e.amount
        else:
            exp_total = _get_total(expenses)

        if incomes.filter(~Q(currency=account.currency)).exists():
            inc_total = Decimal('0.00')
            for i in incomes:
                if i.currency != account.currency:
                    rate = _get_account_fx(i.currency, account.currency)
                    inc_total += (i.amount * rate).quantize(Decimal('0.01'))
                else:
                    inc_total += i.amount
        else:
            inc_total = _get_total(incomes)
        
        out_total = _get_total(transfers_from)
                
        if transfers_to.filter(~Q(from_account__currency=account.currency)).exists():
            in_total = Decimal('0.00')
            for t in transfers_to.select_related('from_account'):
                if t.from_account.currency != account.currency:
                    rate = _get_account_fx(t.from_account.currency, account.currency)
                    in_total += (t.amount * rate).quantize(Decimal('0.01'))
                else:
                    in_total += t.amount
        else:
            in_total = _get_total(transfers_to)
        
        if contributions.filter(~Q(goal__currency=account.currency)).exists():
            sav_total = Decimal('0.00')
            for c in contributions.select_related('goal'):
                if c.goal.currency != account.currency:
                    rate = _get_account_fx(c.goal.currency, account.currency)
                    sav_total += (c.amount * rate).quantize(Decimal('0.01'))
                else:
                    sav_total += c.amount
        else:
            sav_total = _get_total(contributions)
        
        if loan_repayments.filter(~Q(loan__currency=account.currency)).exists():
            loan_total = Decimal('0.00')
            for lr in loan_repayments.select_related('loan'):
                if lr.loan.currency != account.currency:
                    rate = _get_account_fx(lr.loan.currency, account.currency)
                    loan_total += (lr.amount * rate).quantize(Decimal('0.01'))
                else:
                    loan_total += lr.amount
        else:
            loan_total = _get_total(loan_repayments)

        # Capital events are in the event's currency
        cap_total = Decimal('0.00')
        for ce in capital_events:
            if ce.include_in_net_worth:
                if ce.currency != account.currency:
                    rate = _get_account_fx(ce.currency, account.currency)
                    cap_total += (ce.amount * rate).quantize(Decimal('0.01'))
                else:
                    cap_total += ce.amount

        filtered_net_total = inc_total + in_total - exp_total - out_total - sav_total - loan_total - cap_total

        # Build a lightweight combined ledger first so the database handles
        # the global ordering/counting, then hydrate only the rows on the
        # current page slice.
        ledger = (
            self._build_ledger_source(expenses, 'EXPENSE', 0)
            .union(
                self._build_ledger_source(incomes, 'INCOME', 1),
                self._build_ledger_source(transfers_from, 'TRANSFER_OUT', 2),
                self._build_ledger_source(transfers_to, 'TRANSFER_IN', 3),
                self._build_ledger_source(contributions, 'SAVINGS', 4),
                self._build_ledger_source(loan_repayments, 'LOAN_REPAYMENT', 5),
                self._build_ledger_source(capital_events, 'CAPITAL_EVENT', 6),
                all=True,
            )
        )
        if sort_by == 'date_asc':
            ledger = ledger.order_by('date', 'ledger_rank', 'id')
        elif sort_by == 'amount_desc':
            ledger = ledger.order_by('-amount', '-date', 'ledger_rank', '-id')
        elif sort_by == 'amount_asc':
            ledger = ledger.order_by('amount', '-date', 'ledger_rank', '-id')
        else:
            ledger = ledger.order_by('-date', 'ledger_rank', '-id')

        # Pagination
        paginator = Paginator(ledger, 20)
        page_number = request.GET.get('page')
        page_obj = paginator.get_page(page_number)
        page_rows = list(page_obj.object_list)
        page_obj.object_list = self._hydrate_ledger_page(page_rows, account, request.user)

        # Inject display properties ONLY on the current page slice (~20 items)
        for item in page_obj:
            tag = item.__ledger_type
            if tag == 'EXPENSE':
                item.transaction_type = 'EXPENSE'
                item.display_currency = item.currency
                item.base_amount_display = item.base_amount if item.currency != account.currency else None
            elif tag == 'INCOME':
                item.transaction_type = 'INCOME'
                item.display_currency = item.currency
                item.base_amount_display = item.base_amount if item.currency != account.currency else None
            elif tag == 'TRANSFER_OUT':
                item.transaction_type = 'TRANSFER_OUT'
                item.display_amount = -item.amount
                item.display_currency = account.currency
                item.base_amount_display = None
            elif tag == 'TRANSFER_IN':
                item.transaction_type = 'TRANSFER_IN'
                item.display_amount = item.amount
                item.display_currency = item.from_account.currency
                if item.from_account.currency != account.currency:
                    rate = get_exchange_rate(item.from_account.currency, account.currency)
                    item.base_amount_display = (item.amount * rate).quantize(Decimal('0.01'))
                else:
                    item.base_amount_display = None
            elif tag == 'SAVINGS':
                item.transaction_type = 'SAVINGS'
                item.display_currency = item.goal.currency
                if item.goal.currency != account.currency:
                    rate = get_exchange_rate(item.goal.currency, account.currency)
                    item.base_amount_display = (item.amount * rate).quantize(Decimal('0.01'))
                else:
                    item.base_amount_display = None
                item.description = _("Savings: %(goal)s") % {'goal': item.goal.name}
            elif tag == 'LOAN_REPAYMENT':
                item.transaction_type = 'LOAN_REPAYMENT'
                item.display_currency = item.loan.currency
                if item.loan.currency != account.currency:
                    rate = get_exchange_rate(item.loan.currency, account.currency)
                    item.base_amount_display = (item.amount * rate).quantize(Decimal('0.01'))
                else:
                    item.base_amount_display = None
                item.description = _("Loan Repayment: %(loan)s") % {'loan': item.loan.name}
            elif tag == 'CAPITAL_EVENT':
                item.transaction_type = 'CAPITAL_EVENT'
                item.display_currency = item.currency
                item.base_amount_display = item.base_amount if item.currency != account.currency else None
                item.description = item.note if item.note else item.get_subtype_display()

        # One vocabulary for the shared table: the row's kind (pill colour), what the Category/Source
        # badge says, and whether money came in or went out of this account.
        for item in page_obj:
            self._decorate_row(item, account)

        is_filtered = bool(
            selected_tx_types or selected_categories or selected_amounts or search_query or time_period != 'all'
        )
        if is_filtered:
            total_transactions = (
                Expense.objects.filter(user=request.user, account=account).count()
                + Income.objects.filter(user=request.user, account=account).count()
                + Transfer.objects.filter(user=request.user).filter(Q(from_account=account) | Q(to_account=account)).count()
                + GoalContribution.objects.filter(goal__user=request.user, account=account).count()
                + LoanRepayment.objects.filter(loan__user=request.user, from_account=account).count()
                + CapitalEvent.objects.filter(user=request.user, account=account).count()
            )
        else:
            total_transactions = paginator.count

        money_in = inc_total + in_total
        money_out = exp_total + out_total + sav_total + loan_total + cap_total

        applied_filters = {}
        if selected_tx_types:
            applied_filters['tx_type'] = selected_tx_types
        if selected_categories:
            applied_filters['category'] = selected_categories
        if selected_amounts:
            applied_filters['amount_range'] = selected_amounts

        policy = account.linked_physical_asset if strategy_for(account.account_type) == STRATEGY.INSURANCE_SURRENDER else None
        premium_schedule = None
        if policy:
            premium_schedule = (
                RecurringTransaction.objects.filter(physical_asset=policy, transaction_type='INSURANCE_PREMIUM', is_active=True)
                .select_related('account').first()
            )

        context = {
            'account': account,
            'policy': policy,
            'premium_schedule': premium_schedule,
            'can_reconcile': account.is_active and is_reconcilable(account) and request.user.username != 'demo',
            'money_in': money_in,
            'money_out': money_out,
            'row_count': paginator.count,
            'total_transactions': total_transactions,
            'ledger': page_obj,
            'page_obj': page_obj,
            'is_paginated': paginator.num_pages > 1,
            'currency_symbol': account.currency,
            'base_currency_symbol': base_currency,
            'search_query': search_query,
            'selected_tx_type': selected_tx_types[0] if len(selected_tx_types) == 1 else '',
            'filtered_net_total': filtered_net_total,
            'trend_data': self.get_trend_data(account, request.user),
            'filter_config': ACCOUNT_DETAIL_FILTERS,
            'applied_state': {
                'search': search_query,
                'time_period': time_period,
                'start_date': start_date,
                'end_date': end_date,
                'sort': sort_by,
                'filters': applied_filters,
            },
        }
        from django.utils.cache import patch_vary_headers
        template_name = 'expenses/partials/_account_detail.html' if request.headers.get('HX-Request') == 'true' else self.template_name
        response = render(request, template_name, context)
        patch_vary_headers(response, ['HX-Request'])
        return response

    ROW_KINDS = {
        'EXPENSE': ('expense', _('Expense'), False),
        'INCOME': ('income', _('Income'), True),
        'TRANSFER_OUT': ('transfer', _('Transfer'), False),
        'TRANSFER_IN': ('transfer', _('Transfer'), True),
        'SAVINGS': ('savings', _('Savings'), False),
        'LOAN_REPAYMENT': ('loan', _('Loan Repayment'), False),
        'CAPITAL_EVENT': ('capital_event', _('Capital Event'), False),
    }

    def _decorate_row(self, item, account):
        kind, label, is_inflow = self.ROW_KINDS[item.transaction_type]
        item.row_kind = kind
        item.row_label = label
        item.row_is_inflow = is_inflow
        # A capital event that is left out of net worth never moved this account's balance.
        item.row_is_neutral = item.transaction_type == 'CAPITAL_EVENT' and not item.include_in_net_worth

        tag = item.transaction_type
        badge, variant = '', 'primary'
        if tag == 'EXPENSE':
            badge, variant = item.category, 'danger'
        elif tag == 'INCOME':
            badge, variant = getattr(item, 'category', '') or item.source_type, 'success'
        elif tag == 'TRANSFER_OUT':
            badge = _('To %(name)s') % {'name': item.to_account.name}
        elif tag == 'TRANSFER_IN':
            badge = _('From %(name)s') % {'name': item.from_account.name}
        elif tag == 'SAVINGS':
            badge, variant = item.goal.name, 'warning'
        elif tag == 'LOAN_REPAYMENT':
            badge, variant = item.loan.name, 'warning'
        elif tag == 'CAPITAL_EVENT':
            badge, variant = item.get_subtype_display(), 'danger'
        item.row_badge = badge
        item.row_badge_variant = variant

    def get_trend_data(self, account, user):
        
        today = date.today()
        
        # Determine range and frequency
        first_tx = self.get_all_transactions(account, user).order_by('date').first()
        earliest_date = first_tx['date'] if first_tx else today
        
        use_monthly = (today - earliest_date).days > 90
        
        if use_monthly:
            # Monthly for last 12 months
            labels = []
            values = []
            
            # Get all transactions for last 13 months (to get starting balance of 12th month)
            start_date = (today.replace(day=1) - timedelta(days=365)).replace(day=1)
            transactions = self.get_all_transactions(account, user, start_date)
            
            # Group by month
            monthly_diffs = defaultdict(Decimal)
            for tx in transactions:
                # Month key: '2026-04'
                key = tx['date'].strftime('%Y-%m')
                monthly_diffs[key] += tx['net_amount']
                
            current_bal = LedgerReadService.get_account_balance(account)
            check_date = today
            
            for i in range(13): # 12 months + start point
                labels.append(check_date.strftime('%b %y'))
                values.append(float(current_bal))
                
                key = check_date.strftime('%Y-%m')
                current_bal -= monthly_diffs[key]
                
                # Previous month
                check_date = (check_date.replace(day=1) - timedelta(days=1))
                
            labels.reverse()
            values.reverse()
            return {'labels': labels, 'values': values, 'type': 'monthly'}
        else:
            # Daily for last 30 days
            labels = []
            values = []
            
            start_date = today - timedelta(days=30)
            transactions = self.get_all_transactions(account, user, start_date)
            
            # Group by date
            daily_diffs = defaultdict(Decimal)
            for tx in transactions:
                daily_diffs[tx['date']] += tx['net_amount']
                
            current_bal = LedgerReadService.get_account_balance(account)
            
            for i in range(31): # 30 days + start point
                d = today - timedelta(days=i)
                labels.append(d.strftime('%d %b'))
                values.append(float(current_bal))
                
                current_bal -= daily_diffs.get(d, Decimal('0.00'))
                
            labels.reverse()
            values.reverse()
            return {'labels': labels, 'values': values, 'type': 'daily'}

    def get_all_transactions(self, account, user, start_date=None):
        """Returns a combined queryset-like of all transactions affecting account balance."""
        
        expenses = Expense.objects.filter(user=user, account=account).select_related('category_fk')
        incomes = Income.objects.filter(user=user, account=account).select_related('source_fk')
        transfers_out = Transfer.objects.filter(user=user, from_account=account)
        transfers_in = Transfer.objects.filter(user=user, to_account=account).select_related('from_account')
        contributions = GoalContribution.objects.filter(goal__user=user, account=account).select_related('goal')
        loan_repayments = LoanRepayment.objects.filter(loan__user=user, from_account=account).select_related('loan')
        capital_events = CapitalEvent.objects.filter(user=user, account=account, include_in_net_worth=True)
        
        if start_date:
            expenses = expenses.filter(date__gte=start_date)
            incomes = incomes.filter(date__gte=start_date)
            transfers_out = transfers_out.filter(date__gte=start_date)
            transfers_in = transfers_in.filter(date__gte=start_date)
            contributions = contributions.filter(date__gte=start_date)
            loan_repayments = loan_repayments.filter(date__gte=start_date)
            capital_events = capital_events.filter(date__gte=start_date)
            
        # For simplicity, we'll manually handle the currency conversion logic in Python 
        # because doing it in SQL with exchange rates is complex and might be slow for few records.
        # But we'll collect them all.
        
        all_tx = []
        for e in expenses.values('date', 'amount', 'currency'):
            amt = e['amount']
            if e['currency'] != account.currency:
                rate = get_exchange_rate(e['currency'], account.currency)
                amt = (amt * rate).quantize(Decimal('0.01'))
            all_tx.append({'date': e['date'], 'net_amount': -amt})
            
        for i in incomes.values('date', 'amount', 'currency'):
            amt = i['amount']
            if i['currency'] != account.currency:
                rate = get_exchange_rate(i['currency'], account.currency)
                amt = (amt * rate).quantize(Decimal('0.01'))
            all_tx.append({'date': i['date'], 'net_amount': amt})
            
        for t in transfers_out.values('date', 'amount'):
            all_tx.append({'date': t['date'], 'net_amount': -t['amount']})
            
        for t in transfers_in:
            amt = t.amount
            if t.from_account.currency != account.currency:
                rate = get_exchange_rate(t.from_account.currency, account.currency)
                amt = (amt * rate).quantize(Decimal('0.01'))
            all_tx.append({'date': t.date, 'net_amount': amt})
            
        for c in contributions:
            amt = c.amount
            if c.goal.currency != account.currency:
                rate = get_exchange_rate(c.goal.currency, account.currency)
                amt = (amt * rate).quantize(Decimal('0.01'))
            all_tx.append({'date': c.date, 'net_amount': -amt})

        for lr in loan_repayments:
            amt = lr.amount
            if lr.loan.currency != account.currency:
                rate = get_exchange_rate(lr.loan.currency, account.currency)
                amt = (amt * rate).quantize(Decimal('0.01'))
            all_tx.append({'date': lr.date, 'net_amount': -amt})

        for ce in capital_events.values('date', 'amount', 'currency'):
            amt = ce['amount']
            if ce['currency'] != account.currency:
                rate = get_exchange_rate(ce['currency'], account.currency)
                amt = (amt * rate).quantize(Decimal('0.01'))
            all_tx.append({'date': ce['date'], 'net_amount': -amt})
            
        # Return as a simple list of dicts
        # Sort it if needed, but the grouping logic handles it
        
        # Wrap in a way that allows .order_by('date').first() if no start_date
        class PseudoQS(list):
            def order_by(self, field):
                rev = field.startswith('-')
                f = field.lstrip('-')
                return PseudoQS(sorted(self, key=lambda x: x[f], reverse=rev))
            def first(self):
                return self[0] if self else None
                
        return PseudoQS(all_tx)


class RecordMaturityIncomeView(LoginRequiredMixin, UUIDOrIntLookupMixin, View):
    """
    Action view to record accrued interest earned on an FD/RD account as an
    Income entry under 'Investment Returns'.
    """
    def post(self, request, *args, **kwargs):
        account = get_object_by_uuid_or_pk(Account, kwargs.get('pk') or kwargs.get('uuid'), user=request.user)

        with transaction.atomic():
            locked_account = Account.objects.select_for_update().get(pk=account.pk)
            today_val = locked_account.deposit_closed_date or locked_account.deposit_maturity_date or date.today()
            current_val = get_current(locked_account, today=today_val)
            baseline_val = get_baseline(locked_account, today=today_val) or Decimal('0.00')
            interest_earned = (current_val - baseline_val).quantize(Decimal('0.01'))

            if interest_earned <= Decimal('0.00'):
                messages.warning(request, _("No accrued interest earned to record for %(name)s.") % {'name': locked_account.name})
            else:
                existing_income = Income.objects.filter(
                    user=request.user,
                    account=locked_account,
                    source_type='Investment Returns',
                    date=today_val,
                    amount=interest_earned,
                ).exists()

                if existing_income:
                    messages.info(
                        request,
                        _("Maturity interest for %(name)s on %(date)s has already been recorded.") % {
                            'name': locked_account.name,
                            'date': today_val,
                        }
                    )
                else:
                    dedup_key = f"maturity_{locked_account.id}_{today_val}_{interest_earned}"
                    try:
                        Income.objects.create(
                            user=request.user,
                            date=today_val,
                            amount=interest_earned,
                            currency=locked_account.currency,
                            source_type='Investment Returns',
                            source=f"Interest from {locked_account.name}",
                            account=locked_account,
                            description=f"Accrued interest earned on deposit {locked_account.name}",
                            client_dedup_key=dedup_key,
                        )
                        messages.success(
                            request,
                            _("Recorded %(currency)s%(amount)s interest for %(name)s as Income under 'Investment Returns'.") % {
                                'currency': locked_account.currency,
                                'amount': interest_earned,
                                'name': locked_account.name,
                            }
                        )
                        ph_capture(request.user, 'maturity_income_recorded', {})
                    except IntegrityError:
                        messages.info(
                            request,
                            _("Maturity interest for %(name)s on %(date)s has already been recorded.") % {
                                'name': locked_account.name,
                                'date': today_val,
                            }
                        )

        redirect_url = request.META.get('HTTP_REFERER') or reverse_lazy('account-list')
        return redirect(redirect_url)


def search_amfi_schemes(request):
    """
    Search endpoint over local AMFIScheme search mirror with live MFapi fallback (SPEC §3a).
    Returns top 15 matching scheme names/codes.
    """
    if not request.user.is_authenticated:
        return JsonResponse({'results': []}, status=401)
    
    q = request.GET.get('q', '').strip()
    if not q or len(q) < 2:
        return JsonResponse({'results': []})
    
    from ..models import AMFIScheme
    results = []
    
    # 1. Try local AMFIScheme table first
    query_words = [w for w in q.split() if w]
    scheme_filter = Q()
    for w in query_words:
        scheme_filter &= Q(scheme_name__icontains=w)

    schemes = list(AMFIScheme.objects.filter(
        scheme_filter | Q(scheme_code__icontains=q)
    )[:40])
    
    if schemes:
        q_lower = q.lower()
        has_plan_preference = 'direct' in q_lower or 'regular' in q_lower
        has_opt_preference = 'growth' in q_lower or 'idcw' in q_lower or 'div' in q_lower

        def sort_key(s):
            name_lower = s.scheme_name.lower()
            plan_score = 0
            if not has_plan_preference:
                if 'direct' in name_lower:
                    plan_score = 0
                elif 'regular' in name_lower:
                    plan_score = 1
                else:
                    plan_score = 2
            
            opt_score = 0
            if not has_opt_preference:
                if 'growth' in name_lower:
                    opt_score = 0
                elif 'idcw' in name_lower or 'dividend' in name_lower:
                    opt_score = 1
                else:
                    opt_score = 2

            exact_score = 0 if q_lower in name_lower else 1
            return (exact_score, plan_score, opt_score, len(s.scheme_name))

        schemes.sort(key=sort_key)
        results = [
            {'scheme_code': s.scheme_code, 'scheme_name': s.scheme_name, 'isin': s.isin}
            for s in schemes[:15]
        ]
    else:
        # 2. Live fallback to MFapi.in search API
        try:
            import urllib.parse

            import requests
            encoded_q = urllib.parse.quote(q)
            url = f"https://api.mfapi.in/mf/search?q={encoded_q}"
            resp = requests.get(url, headers={'User-Agent': 'TrackMyRupee/1.0'}, timeout=5)
            if resp.status_code == 200:
                data = resp.json()
                for item in data[:15]:
                    code = str(item.get('schemeCode'))
                    name = item.get('schemeName')
                    if code and name:
                        results.append({'scheme_code': code, 'scheme_name': name, 'isin': None})
        except Exception:
            pass
            
    return JsonResponse({'results': results})


@require_POST
def refresh_holding_nav(request, pk):
    """
    Manual "Refresh Now" action on a holding (SPEC §3a point 3).
    Bypasses circuit-breaker backoff and forces immediate fetch.
    """
    if not request.user.is_authenticated:
        return redirect('account_login')
    
    from django.shortcuts import get_object_or_404

    from ..models import Holding
    from ..nav_provider import NAVFetchService

    holding = get_object_or_404(Holding, pk=pk, account__user=request.user)
    if not holding.scheme_code:
        messages.warning(request, _("No scheme code configured for this holding."))
    else:
        service = NAVFetchService()
        cache, success = service.fetch_scheme(holding.scheme_code, force=True)
        if success:
            messages.success(
                request,
                _("Successfully updated NAV for %(name)s (latest NAV: %(nav)s).") % {
                    'name': holding.instrument_name,
                    'nav': cache.latest_nav if cache else '',
                }
            )
            ph_capture(request.user, 'holding_nav_refreshed', {})
        else:
            messages.warning(
                request,
                _("Failed to fetch fresh NAV for %(name)s. Using cached valuation.") % {
                    'name': holding.instrument_name,
                }
            )
            
    return redirect('account-detail', pk=holding.account.pk)


def holding_create_view(request, pk=None):
    """
    Creates a new Holding (with its first purchase) under an account.
    """
    if not request.user.is_authenticated:
        return redirect('account_login')

    from .. import holdings_service as hs
    from ..nav_provider import NAVFetchService

    next_url = get_safe_redirect_url(
        request,
        request.POST.get('next') or request.META.get('HTTP_REFERER'),
        reverse_lazy('holding-list'),
    )
    account_id = pk or request.POST.get('account_id')

    if not account_id:
        messages.error(request, _("Please select an investment account."))
        return redirect(next_url)

    account = get_object_by_uuid_or_pk(Account, account_id, user=request.user)
    if request.method == 'POST':
        name = request.POST.get('instrument_name', '').strip()
        if not name:
            messages.error(request, _("Holding name is required."))
            return redirect(next_url)
        try:
            holding = hs.create_holding(
                account, name=name, units=request.POST.get('units'), price=request.POST.get('avg_cost'),
                date=parse_date(request.POST.get('date', '').strip() or ''),
                scheme_code=request.POST.get('scheme_code', ''), isin=request.POST.get('isin', ''),
            )
        except hs.HoldingError as exc:
            messages.error(request, str(exc))
            return redirect(next_url)

        if holding.scheme_code:
            NAVFetchService().fetch_scheme(holding.scheme_code, force=True)

        messages.success(request, _("Holding '%(name)s' added successfully.") % {'name': name})
        ph_capture(request.user, 'holding_created', {'has_scheme_code': bool(holding.scheme_code)})
    return redirect(next_url)


@require_POST
def holding_delete_view(request, pk):
    """
    Deactivates a Holding from an account.
    """
    if not request.user.is_authenticated:
        return redirect('account_login')
        
    from django.shortcuts import get_object_or_404

    from ..models import Holding

    holding = get_object_or_404(Holding, pk=pk, account__user=request.user)
    holding.is_active = False
    holding.save()
    messages.success(request, _("Holding '%(name)s' removed.") % {'name': holding.instrument_name})
    ph_capture(request.user, 'holding_deleted', {})
    
    next_url = get_safe_redirect_url(
        request,
        request.POST.get('next') or request.GET.get('next') or request.META.get('HTTP_REFERER'),
        reverse_lazy('holding-list'),
    )
    return redirect(next_url)


@require_POST
def holding_valuation_view(request, pk):
    """Record today's price for a holding that has no AMFI feed (a stock, gold, an ETF). The value follows the
    units held, like a NAV does, so topping up the units later does not leave the old total in place."""
    if not request.user.is_authenticated:
        return redirect('account_login')

    from decimal import InvalidOperation

    from django.shortcuts import get_object_or_404

    from ..models import Holding, Valuation

    holding = get_object_or_404(Holding, pk=pk, account__user=request.user, is_active=True)
    next_url = get_safe_redirect_url(
        request, request.POST.get('next') or request.META.get('HTTP_REFERER'), reverse_lazy('holding-list'),
    )
    if holding.scheme_code:
        messages.error(request, _("This holding is priced from the AMFI NAV feed. Use Refresh NAV instead."))
        return redirect(next_url)
    try:
        price = Decimal(request.POST.get('unit_price', '').strip())
        if not price.is_finite() or price <= 0 or price >= Decimal('10') ** 9:
            raise InvalidOperation
        price = price.quantize(Decimal('0.000001'))
    except (InvalidOperation, ValueError):
        messages.error(request, _("Enter a price greater than zero."))
        return redirect(next_url)
    today = timezone.localdate()
    as_of = parse_date(request.POST.get('as_of_date', '').strip() or '') or today
    if as_of > today:
        messages.error(request, _("The date cannot be in the future."))
        return redirect(next_url)
    units = holding.units or Decimal('0')
    Valuation.objects.update_or_create(
        holding=holding, as_of_date=as_of, source='MANUAL',
        defaults={'unit_nav': price, 'value': (units * price).quantize(Decimal('0.01'))},
    )
    messages.success(request, _("Value updated for '%(name)s'.") % {'name': holding.instrument_name})
    ph_capture(request.user, 'holding_valued_manually', {})
    return redirect(next_url)


def _holding_for(request, pk):
    from django.shortcuts import get_object_or_404

    from ..models import Holding

    return get_object_or_404(Holding.objects.select_related('account'), pk=pk, account__user=request.user,
                             is_active=True)


def _holding_detail_url(holding):
    return reverse('holding-detail', args=[holding.pk])


class HoldingDetailView(LoginRequiredMixin, View):
    """One holding: what it is worth, and every purchase that built it up."""

    def get(self, request, pk):
        from .. import holdings_service as hs
        from ..models import Transfer

        holding = _holding_for(request, pk)
        account = holding.account
        if holding.scheme_code and holding.transactions.filter(status='PENDING').exists():
            hs.resolve_pending(holding=holding)       # the NAV may have been published since
            holding.refresh_from_db()
        transactions = list(holding.transactions.select_related('transfer__from_account').order_by('-date', '-id'))
        pending = [tx for tx in transactions if tx.status == 'PENDING']
        latest = holding.valuations.first()
        units = holding.units or Decimal('0')
        value = latest.value_for(units) if latest else holding.cost_basis
        cost = holding.cost_basis
        gain = value - cost
        funding = []
        for t in (Transfer.objects.filter(user=request.user, to_account=account)
                  .select_related('from_account').order_by('-date', '-id')[:25]):
            if t.from_account.currency != account.currency:
                continue
            left = hs.unallocated_amount(t)
            if left > 0:
                funding.append({'transfer': t, 'left': left})
        return render(request, 'expenses/holding_detail.html', {
            'holding': holding, 'account': account, 'transactions': transactions,
            'opening_only': not transactions and units > 0,
            'valuation': latest, 'value': value, 'cost': cost, 'gain': gain,
            'gain_pct': ((gain / cost) * Decimal('100')).quantize(Decimal('0.1')) if cost > 0 else Decimal('0.0'),
            'funding': funding, 'today': timezone.localdate(),
            'has_feed': bool(holding.scheme_code),
            'pending_count': len(pending), 'pending_amount': sum((tx.amount for tx in pending), Decimal('0')),
            'has_estimates': any(tx.status == 'ESTIMATED' for tx in transactions),
        })


def _post_purchase_fields(request, keep_cutoff=False):
    cutoff = request.POST.get('cutoff')
    return dict(
        date=parse_date(request.POST.get('date', '').strip() or ''),
        units=request.POST.get('units'), price=request.POST.get('price'), amount=request.POST.get('amount'),
        before_cutoff=(None if keep_cutoff and cutoff is None else cutoff != 'after'),
    )


@require_POST
def holding_buy_view(request, pk):
    """Buy more of a holding, optionally paid for by a transfer into its account."""
    if not request.user.is_authenticated:
        return redirect('account_login')

    from .. import holdings_service as hs
    from ..models import Transfer

    holding = _holding_for(request, pk)
    transfer = None
    ref = request.POST.get('transfer', '').strip()
    if ref:
        transfer = get_object_by_uuid_or_pk(Transfer, ref, user=request.user)
    try:
        hs.add_purchase(holding, transfer=transfer, note=request.POST.get('note', '').strip(),
                        **_post_purchase_fields(request))
    except hs.HoldingError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, _("Purchase added to '%(name)s'.") % {'name': holding.instrument_name})
        ph_capture(request.user, 'holding_purchase_added', {'from_transfer': bool(transfer)})
    return redirect(_holding_detail_url(holding))


@require_POST
def holding_purchase_edit_view(request, pk):
    if not request.user.is_authenticated:
        return redirect('account_login')

    from django.shortcuts import get_object_or_404

    from .. import holdings_service as hs
    from ..models import HoldingTransaction

    tx = get_object_or_404(HoldingTransaction.objects.select_related('holding__account', 'transfer__from_account'),
                           pk=pk, holding__account__user=request.user, holding__is_active=True)
    try:
        hs.update_purchase(tx, note=request.POST.get('note', '').strip(), **_post_purchase_fields(request, keep_cutoff=True))
    except hs.HoldingError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, _("Purchase updated."))
    return redirect(_holding_detail_url(tx.holding))


@require_POST
def holding_purchase_delete_view(request, pk):
    if not request.user.is_authenticated:
        return redirect('account_login')

    from django.shortcuts import get_object_or_404

    from .. import holdings_service as hs
    from ..models import HoldingTransaction

    tx = get_object_or_404(HoldingTransaction.objects.select_related('holding__account'),
                           pk=pk, holding__account__user=request.user, holding__is_active=True)
    holding = tx.holding
    try:
        hs.delete_purchase(tx)
    except hs.HoldingError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, _("Purchase removed."))
    return redirect(_holding_detail_url(holding))


@require_POST
def holding_rename_view(request, pk):
    if not request.user.is_authenticated:
        return redirect('account_login')

    holding = _holding_for(request, pk)
    name = request.POST.get('instrument_name', '').strip()
    if not name:
        messages.error(request, _("Holding name is required."))
    else:
        holding.instrument_name = name[:255]
        holding.save(update_fields=['instrument_name', 'updated_at'])
        messages.success(request, _("Holding renamed."))
    return redirect(_holding_detail_url(holding))


class HoldingsListView(LoginRequiredMixin, ListView):
    """
    Dedicated Holdings & Investment Portfolio page for all active user holdings.
    """
    model = Holding
    template_name = 'expenses/holding_list.html'
    context_object_name = 'holdings'

    def get_queryset(self):
        return Holding.objects.filter(
            account__user=self.request.user,
            is_active=True,
        ).select_related('account').prefetch_related('valuations')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        holdings = context['holdings']
        
        total_valuation = Decimal('0.00')
        total_cost = Decimal('0.00')
        
        user_currency = self.request.user.profile.currency
        rates = {}

        def to_user_currency(amount, currency):
            if currency == user_currency:
                return amount
            if currency not in rates:
                try:
                    rates[currency] = get_exchange_rate(currency, user_currency)
                except Exception:
                    rates[currency] = Decimal('1.0')
            return (amount * rates[currency]).quantize(Decimal('0.01'))

        for h in holdings:
            val = h.valuations.first()
            total_valuation += to_user_currency(val.value_for(h.units) if val else h.cost_basis, h.currency)
            total_cost += to_user_currency(h.cost_basis, h.currency)
            
        unrealized_gain = total_valuation - total_cost
        gain_pct = ((unrealized_gain / total_cost) * Decimal('100')).quantize(Decimal('0.1')) if total_cost > 0 else Decimal('0.0')
        
        context['total_valuation'] = total_valuation
        context['total_cost'] = total_cost
        context['unrealized_gain'] = unrealized_gain
        context['gain_pct'] = gain_pct
        context['user_currency'] = self.request.user.profile.currency
        context['user_accounts'] = Account.objects.filter(
            user=self.request.user,
            is_active=True,
            account_type__in=holdings_codes(),
        ).order_by('name')
        return context
