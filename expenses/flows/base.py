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
from django.utils.translation import gettext_lazy as _

from finance_tracker.plans import get_limit
from ..models import FinancialFlow, FlowCreatedObject


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

    def check_limits(self, user, steps: list[CreateStep]) -> list[str]:
        if not self.limit_map:
            return []

        profile = getattr(user, 'profile', None)
        tier = getattr(profile, 'active_tier', 'FREE') if profile else 'FREE'
        step_counts = self.count_steps_by_model(steps)
        warnings: list[str] = []

        limit_labels = {
            'accounts': _('You have reached your current account limit for this plan.'),
            'recurring_transactions': _('You have reached your current recurring transaction limit for this plan.'),
            'loans': _('You have reached your current loan limit for this plan.'),
            'savings_goals': _('You have reached your current savings goal limit for this plan.'),
        }

        for limit_key, model_cls in self.limit_map.items():
            steps_creating = step_counts.get(model_cls, 0)
            if steps_creating <= 0:
                continue

            limit = get_limit(tier, limit_key)
            if limit is None or limit == -1:
                continue

            current_count = self._get_current_model_count(user, model_cls, limit_key)
            if current_count + steps_creating > limit:
                msg = limit_labels.get(
                    limit_key,
                    _('You have reached your current %(limit_key)s limit for this plan.') % {'limit_key': limit_key.replace('_', ' ')}
                )
                warnings.append(str(msg))

        return warnings

    def is_configured(self, user) -> bool:
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

    def review_context(self, user, form, cleaned_data) -> dict:
        sections = []
        for step in self.get_wizard_steps():
            items = []
            for field_name in step.fields:
                field = form.fields.get(field_name)
                if not field:
                    continue
                value = cleaned_data.get(field_name)
                if value in (None, '', []):
                    continue
                items.append({
                    'label': field.label or field_name.replace('_', ' ').title(),
                    'value': self._format_review_value(field, value),
                })
            if items:
                sections.append({
                    'title': step.title,
                    'items': items,
                })

        profile = getattr(user, 'profile', None)
        return {
            'user_display': getattr(user, 'get_full_name', lambda: '')() or getattr(user, 'username', ''),
            'user_currency': getattr(profile, 'currency', None),
            'sections': sections,
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
    def _format_review_value(field, value):
        if hasattr(field, 'choices') and field.choices:
            choice_map = dict(field.choices)
            if value in choice_map:
                return choice_map[value]
        if isinstance(value, bool):
            return 'Yes' if value else 'No'
        if isinstance(value, date):
            return value.strftime('%d %b %Y')
        if isinstance(value, datetime):
            return value.strftime('%d %b %Y %H:%M')
        if hasattr(value, '__str__'):
            return str(value)
        return value