"""
Flow base classes.

How to add a new scenario
-------------------------
1. Create a new file under expenses/flows/ (e.g. my_scenario.py).
2. Subclass Flow; set key, label, category, icon, form_class, and addons.
3. Implement derive(), plan(), and optionally check_limits() / preview().
4. Decorate the class with @register_flow.
5. Import the module in expenses/flows/__init__.py.

That is all. No changes to base.py, registry.py, views, or templates are needed.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from dataclasses import dataclass, field
from typing import Any, ClassVar

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models.signals import post_save
from django.utils.translation import gettext_lazy as _

from finance_tracker.plans import get_limit
from ..models import (
    Account,
    CapitalEvent,
    FinancialFlow,
    FlowCreatedObject,
    Loan,
    PhysicalAsset,
    RecurringTransaction,
)
from ..cache_utils import suppress_dashboard_cache_invalidation
from ..signals import _DASHBOARD_CACHE_MODELS, invalidate_dashboard_cache


@dataclass(frozen=True)
class FlowSnapshot:
    account_types: set[str]
    asset_classes: set[str]
    recurring_signatures: set[tuple[str, str | None]]
    has_active_loan: bool
    has_savings_goal: bool

    @classmethod
    def for_user(cls, user) -> FlowSnapshot:
        account_types = set(
            Account.objects.filter(user=user, is_active=True)
            .values_list('account_type', flat=True)
            .distinct()
        )
        asset_classes = set(
            PhysicalAsset.objects.filter(user=user, is_active=True)
            .values_list('asset_class', flat=True)
            .distinct()
        )
        recurring_signatures = set(
            RecurringTransaction.objects.filter(user=user, is_active=True)
            .values_list('transaction_type', 'category')
            .distinct()
        )
        has_active_loan = Loan.objects.filter(user=user, is_active=True).exists()
        has_savings_goal = user.savings_goals.exists()
        return cls(
            account_types=account_types,
            asset_classes=asset_classes,
            recurring_signatures=recurring_signatures,
            has_active_loan=has_active_loan,
            has_savings_goal=has_savings_goal,
        )


@dataclass
class FlowAddon:
    key: str
    label: str
    fields: list[str] = field(default_factory=list)


@dataclass
class FlowWizardStep:
    key: str
    title: str
    fields: list[str]
    help_text: str = ''
    show_if: str | None = None


@dataclass
class CreateStep:
    """One object to create in a flow plan.

    Field values may be literals or "$<step_key>" strings — the engine resolves
    "$<step_key>" to the object created by that earlier step, so no Flow subclass
    needs to wire foreign keys or sort steps by hand.
    """

    model: type
    fields: dict[str, Any]
    key: str = ""
    condition: bool = True
    depends_on: list[str] = field(default_factory=list)


@dataclass
class FlowResult:
    flow_id: int
    idempotency_key: uuid.UUID
    created: list[Any]


class Flow:
    """Abstract base for all TMR Flows."""

    key: ClassVar[str] = ""
    label: ClassVar[str] = ""
    title: ClassVar[str] = ""
    description: ClassVar[str] = ""
    category: ClassVar[str] = "debt"
    icon: ClassVar[str] = "bi-magic"
    tags: ClassVar[list[str]] = []
    estimated_time: ClassVar[str] = ""
    creates: ClassVar[list[str]] = []
    limit_map: ClassVar[dict[str, type]] = {}
    addons: ClassVar[dict[str, FlowAddon]] = {}
    wizard_steps: ClassVar[list[FlowWizardStep]] = []
    form_class: ClassVar[type | None] = None

    def derive(self, cleaned_data) -> dict:
        return dict(cleaned_data)

    def plan(self, data) -> list[CreateStep]:
        raise NotImplementedError

    @staticmethod
    def count_steps_by_model(steps: list[CreateStep]) -> dict[type, int]:
        counts: dict[type, int] = {}
        for step in steps:
            if not step.condition:
                continue
            if step.fields.get('pk') is not None or step.fields.get('id') is not None:
                continue
            counts[step.model] = counts.get(step.model, 0) + 1
        return counts

    @staticmethod
    def _get_current_model_count(user, model_cls, limit_key: str) -> int:
        if limit_key == 'accounts':
            return user.accounts.filter(is_active=True).count()
        if limit_key == 'recurring_transactions':
            return user.recurringtransaction_set.filter(is_active=True).count()
        if limit_key == 'loans':
            return user.loans.filter(is_active=True).count()
        if limit_key == 'savings_goals':
            return user.savings_goals.count()

        qs = model_cls.objects.filter(user=user)
        if hasattr(model_cls, 'is_active'):
            qs = qs.filter(is_active=True)
        return qs.count()

    @classmethod
    def _format_limit_warning(
        cls,
        tier: str,
        tier_display: str,
        limit_key: str,
        limit: int,
        current_count: int,
        steps_creating: int,
    ) -> str:
        resource_names = {
            'accounts': {'plural': _('accounts'), 'singular': _('account')},
            'recurring_transactions': {'plural': _('recurring transactions'), 'singular': _('recurring transaction')},
            'loans': {'plural': _('loans'), 'singular': _('loan')},
            'savings_goals': {'plural': _('savings goals'), 'singular': _('savings goal')},
        }
        res = resource_names.get(limit_key, {
            'plural': limit_key.replace('_', ' '),
            'singular': limit_key.replace('_', ' '),
        })
        res_label = res['singular'] if limit == 1 else res['plural']
        upgrade_target = 'Plus or Pro' if tier == 'FREE' else ('Pro' if tier == 'PLUS' else None)

        if limit == 0:
            if upgrade_target:
                return str(_('Your %(tier)s plan does not allow %(resource)s (limit: 0, currently %(current)s). Upgrade to %(target)s to track %(resource)s.') % {
                    'tier': tier_display,
                    'resource': res['plural'],
                    'current': current_count,
                    'target': upgrade_target,
                })
            return str(_('Your %(tier)s plan does not allow %(resource)s (limit: 0).') % {
                'tier': tier_display,
                'resource': res['plural'],
            })

        if current_count >= limit:
            if upgrade_target:
                return str(_('You have reached your %(tier)s plan limit of %(limit)s %(resource)s (currently using %(current)s of %(limit)s). Upgrade to %(target)s for higher limits, or remove unused %(resource)s.') % {
                    'tier': tier_display,
                    'limit': limit,
                    'resource': res_label,
                    'current': current_count,
                    'target': upgrade_target,
                })
            return str(_('You have reached your %(tier)s plan limit of %(limit)s %(resource)s (currently using %(current)s of %(limit)s).') % {
                'tier': tier_display,
                'limit': limit,
                'resource': res_label,
                'current': current_count,
            })

        # current_count < limit, but current_count + steps_creating > limit
        if upgrade_target:
            return str(_('Adding %(creating)s %(resource)s would exceed your %(tier)s plan limit of %(limit)s (currently using %(current)s of %(limit)s). Upgrade to %(target)s for higher limits, or remove unused %(resource)s.') % {
                'creating': steps_creating,
                'resource': res['plural'],
                'tier': tier_display,
                'limit': limit,
                'current': current_count,
                'target': upgrade_target,
            })
        return str(_('Adding %(creating)s %(resource)s would exceed your %(tier)s plan limit of %(limit)s (currently using %(current)s of %(limit)s).') % {
            'creating': steps_creating,
            'resource': res['plural'],
            'tier': tier_display,
            'limit': limit,
            'current': current_count,
        })

    def check_limits(self, user, steps: list[CreateStep]) -> list[str]:
        if not self.limit_map:
            return []

        profile = getattr(user, 'profile', None)
        tier = getattr(profile, 'active_tier', 'FREE') if profile else 'FREE'
        tier_display = getattr(profile, 'active_tier_display', tier.title()) if profile else 'Free'
        step_counts = self.count_steps_by_model(steps)
        warnings: list[str] = []

        for limit_key, model_cls in self.limit_map.items():
            steps_creating = step_counts.get(model_cls, 0)
            if steps_creating <= 0:
                continue

            limit = get_limit(tier, limit_key)
            if limit is None or limit == -1:
                continue

            current_count = self._get_current_model_count(user, model_cls, limit_key)
            if current_count + steps_creating > limit:
                msg = self._format_limit_warning(
                    tier=tier,
                    tier_display=tier_display,
                    limit_key=limit_key,
                    limit=limit,
                    current_count=current_count,
                    steps_creating=steps_creating,
                )
                warnings.append(str(msg))

        return warnings

    def is_configured(self, user, snapshot: FlowSnapshot | None = None) -> bool:
        return False

    def get_edit_url(self, user) -> str:
        return ""

    def preview(self, user, cleaned_data) -> dict:
        return {}

    def get_wizard_steps(self) -> list[FlowWizardStep]:
        if self.wizard_steps:
            return self.wizard_steps
        if not self.form_class:
            return []
        return [FlowWizardStep(key='main', title='Details', fields=[field.name for field in self.form_class().visible_fields()])]

    def get_what_we_create(self, user, cleaned_data) -> list[dict]:
        cards = []
        profile = getattr(user, 'profile', None)
        currency = getattr(profile, 'currency', '₹') or '₹'
        from ..utils import format_indian_number

        if self.key == 'sip':
            name = cleaned_data.get('name') or _('Investment')
            inv_type = cleaned_data.get('investment_type') or 'SIP'
            amount = cleaned_data.get('amount') or 0
            freq = str(cleaned_data.get('frequency') or 'monthly').lower()
            start_date = cleaned_data.get('deposit_start_date')
            date_str = start_date.strftime('%d %b %Y') if hasattr(start_date, 'strftime') else str(start_date or '')
            cards.append({
                'icon': 'bi-graph-up-arrow',
                'title': _('Investment account'),
                'description': _('%(name)s, tracked as a %(type)s') % {'name': name, 'type': inv_type},
            })
            cards.append({
                'icon': 'bi-calendar-check',
                'title': _('Contribution schedule'),
                'description': f"{currency}{format_indian_number(amount)} {freq} from {date_str}".strip(),
            })
        elif self.key == 'loan':
            name = cleaned_data.get('name') or _('Loan')
            loan_type = str(cleaned_data.get('loan_type', 'Loan')).title()
            principal = cleaned_data.get('principal') or 0
            annual_rate = cleaned_data.get('annual_rate') or 0
            tenure = int(cleaned_data.get('tenure_months') or 1)
            from ..services import LoanService
            emi = LoanService.calculate_emi(Decimal(str(principal)), Decimal(str(annual_rate)), tenure)
            cards.append({
                'icon': 'bi-bank',
                'title': _('Loan account'),
                'description': f"{name} ({loan_type})",
            })
            if cleaned_data.get('create_repayment_schedule', True):
                cards.append({
                    'icon': 'bi-calendar-check',
                    'title': _('Repayment schedule'),
                    'description': f"EMI: {currency}{format_indian_number(emi)} monthly for {tenure} months",
                })
        elif self.key == 'creditcard':
            name = cleaned_data.get('name') or _('Credit Card')
            limit = cleaned_data.get('credit_limit') or 0
            b_day = cleaned_data.get('billing_day') or 1
            cards.append({
                'icon': 'bi-credit-card',
                'title': _('Credit card account'),
                'description': f"{name} (Limit: {currency}{format_indian_number(limit)})",
            })
            cards.append({
                'icon': 'bi-calendar-event',
                'title': _('Statement reminder'),
                'description': _('Billing day: %(day)s of every month') % {'day': b_day},
            })
        elif self.key == 'salary':
            amount = cleaned_data.get('amount') or 0
            s_date = cleaned_data.get('salary_date') or 1
            acc = cleaned_data.get('account')
            acc_name = acc.name if hasattr(acc, 'name') else str(acc or '')
            cards.append({
                'icon': 'bi-cash-stack',
                'title': _('Income schedule'),
                'description': f"{currency}{format_indian_number(amount)} monthly on day {s_date}",
            })
            if acc_name:
                cards.append({
                    'icon': 'bi-wallet2',
                    'title': _('Deposit account'),
                    'description': _('Credited to %(acc)s') % {'acc': acc_name},
                })
        elif self.key == 'rentbill':
            name = cleaned_data.get('name') or _('Recurring bill')
            amount = cleaned_data.get('amount') or 0
            freq = str(cleaned_data.get('frequency') or 'monthly').lower()
            cards.append({
                'icon': 'bi-receipt',
                'title': _('Recurring bill schedule'),
                'description': f"{name} ({currency}{format_indian_number(amount)} {freq})",
            })
            acc = cleaned_data.get('account')
            if hasattr(acc, 'name'):
                cards.append({
                    'icon': 'bi-wallet2',
                    'title': _('Payment account'),
                    'description': _('Paid from %(acc)s') % {'acc': acc.name},
                })
        elif self.key == 'savingsgoal':
            name = cleaned_data.get('name') or _('Savings Goal')
            target = cleaned_data.get('target_amount') or 0
            cards.append({
                'icon': 'bi-bullseye',
                'title': _('Savings goal'),
                'description': f"{name} (Target: {currency}{format_indian_number(target)})",
            })
            target_date = cleaned_data.get('target_date')
            if target_date:
                d_str = target_date.strftime('%d %b %Y') if hasattr(target_date, 'strftime') else str(target_date)
                cards.append({
                    'icon': 'bi-calendar-check',
                    'title': _('Target date'),
                    'description': _('Target deadline: %(date)s') % {'date': d_str},
                })
        elif self.key == 'fd':
            name = cleaned_data.get('name') or _('Fixed Deposit')
            rate = cleaned_data.get('annual_rate') or 0
            cards.append({
                'icon': 'bi-bank',
                'title': _('Fixed deposit account'),
                'description': f"{name} ({rate}% interest)",
            })
            mat_date = cleaned_data.get('maturity_date')
            if mat_date:
                d_str = mat_date.strftime('%d %b %Y') if hasattr(mat_date, 'strftime') else str(mat_date)
                cards.append({
                    'icon': 'bi-calendar-check',
                    'title': _('Maturity date'),
                    'description': _('Matures on %(date)s') % {'date': d_str},
                })
        elif self.key == 'ppfepfnps':
            name = cleaned_data.get('name') or _('Retirement')
            st = str(cleaned_data.get('scheme_type') or 'PPF')
            cards.append({
                'icon': 'bi-shield-check',
                'title': _('Retirement account'),
                'description': f"{name} ({st})",
            })
        elif self.key == 'insurance':
            name = cleaned_data.get('name') or _('Insurance')
            premium = cleaned_data.get('premium_amount') or 0
            freq = str(cleaned_data.get('frequency') or 'annually').lower()
            cards.append({
                'icon': 'bi-shield-shaded',
                'title': _('Insurance policy'),
                'description': f"{name}",
            })
            cards.append({
                'icon': 'bi-receipt',
                'title': _('Premium schedule'),
                'description': f"{currency}{format_indian_number(premium)} {freq}",
            })
        elif self.key == 'car':
            name = cleaned_data.get('name') or _('Vehicle')
            cost = cleaned_data.get('purchase_price') or cleaned_data.get('cost') or 0
            cards.append({
                'icon': 'bi-car-front',
                'title': _('Physical asset'),
                'description': f"{name} ({currency}{format_indian_number(cost)})",
            })
            if cleaned_data.get('create_loan'):
                loan_amt = cleaned_data.get('loan_amount') or 0
                cards.append({
                    'icon': 'bi-bank',
                    'title': _('Vehicle loan'),
                    'description': f"{currency}{format_indian_number(loan_amt)} financed",
                })
        elif self.key == 'gold':
            name = cleaned_data.get('name') or _('Gold')
            grams = cleaned_data.get('weight_in_grams') or 0
            cards.append({
                'icon': 'bi-gem',
                'title': _('Physical asset'),
                'description': f"{name} ({grams}g)",
            })

        if not cards:
            for item in self.creates:
                cards.append({
                    'icon': 'bi-check2-circle',
                    'title': str(item),
                    'description': _('Will be automatically tracked in your account.'),
                })
        return cards

    def get_review_summary(self, user, cleaned_data) -> str:
        if self.key == 'sip':
            inv_type = cleaned_data.get('investment_type', 'SIP')
            name = cleaned_data.get('name', 'Mutual Funds')
            from_acc = cleaned_data.get('from_account')
            from_acc_name = from_acc.name if hasattr(from_acc, 'name') else str(from_acc or '')
            start_date = cleaned_data.get('deposit_start_date')
            date_str = start_date.strftime('%d %b %Y') if hasattr(start_date, 'strftime') else str(start_date or '')
            if from_acc_name and date_str:
                return str(_('%(type)s into %(name)s from %(acc)s, starting %(date)s.') % {
                    'type': inv_type,
                    'name': name,
                    'acc': from_acc_name,
                    'date': date_str,
                })
            elif from_acc_name:
                return str(_('%(type)s into %(name)s from %(acc)s.') % {
                    'type': inv_type,
                    'name': name,
                    'acc': from_acc_name,
                })
        elif self.key == 'loan':
            name = cleaned_data.get('name', 'Loan')
            rate = cleaned_data.get('annual_rate') or 0
            tenure = cleaned_data.get('tenure_months') or 1
            return str(_('%(name)s loan at %(rate)s%% for %(tenure)s months.') % {
                'name': name,
                'rate': rate,
                'tenure': tenure,
            })
        elif self.key == 'salary':
            s_date = cleaned_data.get('salary_date') or 1
            acc = cleaned_data.get('account')
            acc_name = acc.name if hasattr(acc, 'name') else str(acc or '')
            if acc_name:
                return str(_('Monthly salary credited to %(acc)s on day %(day)s.') % {
                    'acc': acc_name,
                    'day': s_date,
                })
        elif self.key == 'creditcard':
            name = cleaned_data.get('name', 'Card')
            b_day = cleaned_data.get('billing_day') or 1
            return str(_('%(name)s billing on day %(day)s of each month.') % {
                'name': name,
                'day': b_day,
            })
        return ""

    def get_headline_suffix(self, cleaned_data) -> str:
        if self.key in ('sip', 'salary'):
            freq = str(cleaned_data.get('frequency') or 'MONTHLY').upper()
            if freq == 'MONTHLY':
                return str(_('every month'))
            return freq.lower()
        if self.key == 'loan':
            return str(_('monthly EMI'))
        if self.key == 'rentbill':
            freq = str(cleaned_data.get('frequency') or 'MONTHLY').upper()
            if freq == 'MONTHLY':
                return str(_('every month'))
            return freq.lower()
        if self.key == 'creditcard':
            return str(_('balance'))
        if self.key == 'savingsgoal':
            return str(_('target'))
        return ""

    def review_context(self, user, form, cleaned_data) -> dict:
        profile = getattr(user, 'profile', None)
        currency = getattr(profile, 'currency', '₹') or '₹'
        sections = []
        for index, step in enumerate(self.get_wizard_steps(), start=1):
            items = []
            for field_name in step.fields:
                field = form.fields.get(field_name)
                if not field:
                    continue
                value = cleaned_data.get(field_name)
                if value in (None, '', []):
                    continue

                subvalue = None
                if hasattr(value, 'formatted_balance') and hasattr(value, 'name'):
                    val_str = str(value.name)
                    acc_cur = getattr(value, 'currency', currency)
                    subvalue = f"{_('Balance')} {acc_cur}{value.formatted_balance}"
                elif field_name in ('annual_rate', 'interest_rate'):
                    val_str = f"{value}%"
                elif field_name in ('amount', 'principal', 'balance', 'credit_limit', 'target_amount', 'acquisition_cost', 'premium', 'down_payment', 'loan_amount', 'purchase_price', 'cost'):
                    from ..utils import format_indian_number
                    val_str = f"{currency}{format_indian_number(value)}"
                else:
                    val_str = self._format_review_value(field, value, currency)

                items.append({
                    'label': field.label or field_name.replace('_', ' ').title(),
                    'value': val_str,
                    'subvalue': subvalue,
                })
            if items:
                sections.append({
                    'title': step.title,
                    'step_number': index,
                    'items': items,
                })

        what_we_create = self.get_what_we_create(user, cleaned_data)
        summary = self.get_review_summary(user, cleaned_data)
        headline_suffix = self.get_headline_suffix(cleaned_data)

        return {
            'user_display': getattr(user, 'get_full_name', lambda: '')() or getattr(user, 'username', ''),
            'user_currency': currency,
            'sections': sections,
            'what_we_create': what_we_create,
            'summary': summary,
            'headline_suffix': headline_suffix,
        }

    def _existing_flow_result(self, existing: FinancialFlow, idempotency_key: uuid.UUID) -> FlowResult:
        created = [
            obj.content_object
            for obj in existing.created_objects.select_related('content_type').prefetch_related('content_object').all()
            if obj.content_object is not None
        ]
        return FlowResult(flow_id=existing.id, idempotency_key=idempotency_key, created=created)

    def commit(self, user, cleaned_data, idempotency_key) -> FlowResult:
        existing = FinancialFlow.objects.filter(user=user, idempotency_key=idempotency_key).first()
        if existing:
            return self._existing_flow_result(existing, idempotency_key)

        seed_data = dict(cleaned_data)
        seed_data['user'] = user
        data = self.derive(seed_data)
        data['user'] = user
        steps = self.plan(data)
        warnings = self.check_limits(user, steps)
        if warnings:
            raise ValidationError(warnings)

        spec = self._serialize_value(data)

        created_objects: list[Any] = []
        try:
            with suppress_dashboard_cache_invalidation(user_id=user.id, invalidate_on_exit=True):
                with transaction.atomic():
                    flow = FinancialFlow.objects.create(
                        user=user,
                        flow_key=self.key,
                        spec=spec,
                        idempotency_key=idempotency_key,
                    )

                    created_by_key: dict[str, Any] = {}
                    for index, step in enumerate(steps):
                        if not step.condition:
                            continue

                        resolved_fields = {
                            field_name: self._resolve_value(field_value, created_by_key)
                            for field_name, field_value in step.fields.items()
                        }
                        instance = None
                        step_pk = resolved_fields.pop('pk', None) or resolved_fields.pop('id', None)
                        if step_pk is not None:
                            try:
                                instance = step.model.objects.get(pk=step_pk)
                            except step.model.DoesNotExist:
                                instance = None

                        if instance is None:
                            instance = step.model(**resolved_fields)
                        else:
                            for field_name, field_value in resolved_fields.items():
                                setattr(instance, field_name, field_value)

                        if getattr(instance, 'user_id', None) is None and hasattr(instance, 'user'):
                            instance.user = user

                        instance.full_clean()
                        instance.save()
                        created_objects.append(instance)

                        step_key = step.key or f'step_{index}'
                        created_by_key[step_key] = instance
                        FlowCreatedObject.objects.create(
                            flow=flow,
                            content_object=instance,
                            step_key=step_key,
                        )
        except IntegrityError:
            existing = FinancialFlow.objects.filter(user=user, idempotency_key=idempotency_key).first()
            if existing:
                return self._existing_flow_result(existing, idempotency_key)
            raise

        return FlowResult(flow_id=flow.id, idempotency_key=idempotency_key, created=created_objects)

    @staticmethod
    def _resolve_value(value, created_by_key):
        if isinstance(value, str) and value.startswith('$'):
            ref_key = value[1:]
            return created_by_key.get(ref_key)
        if isinstance(value, dict):
            return {key: Flow._resolve_value(item, created_by_key) for key, item in value.items()}
        if isinstance(value, list):
            return [Flow._resolve_value(item, created_by_key) for item in value]
        return value

    @staticmethod
    def _serialize_value(value):
        if hasattr(value, '_meta') and hasattr(value, 'pk'):
            return value.pk
        if isinstance(value, Decimal):
            return str(value)
        if isinstance(value, (date, datetime)):
            return value.isoformat()
        if isinstance(value, dict):
            return {key: Flow._serialize_value(item) for key, item in value.items()}
        if isinstance(value, list):
            return [Flow._serialize_value(item) for item in value]
        if isinstance(value, tuple):
            return [Flow._serialize_value(item) for item in value]
        if isinstance(value, uuid.UUID):
            return str(value)
        return value

    @staticmethod
    def _format_review_value(field, value, currency='₹'):
        if hasattr(field, 'choices') and field.choices:
            choice_map = dict(field.choices)
            if value in choice_map:
                return str(choice_map[value])
        if isinstance(value, bool):
            return _('Yes') if value else _('No')
        if isinstance(value, date):
            return value.strftime('%d %b %Y')
        if isinstance(value, datetime):
            return value.strftime('%d %b %Y %H:%M')
        if hasattr(value, 'name'):
            return str(value.name)
        if isinstance(value, (int, float, Decimal)):
            from ..utils import format_indian_number
            return f"{currency}{format_indian_number(value)}"
        if hasattr(value, '__str__'):
            return str(value)
        return str(value)