"""The Family step of guided setup: start a family space, then invite people from the Family page.

Unlike the other flows this creates no account or schedule. It makes the household (through the same function the
Family page uses, so the rules and the audit entry are the same) and records the flow so a double-click cannot
create two. Nothing is shared and nobody is invited: that comes next, on the Family page."""
from __future__ import annotations

from django import forms
from django.core.exceptions import ValidationError
from django.db import transaction
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from ..family import family_enabled_for, households
from ..models import FinancialFlow
from .base import Flow, FlowResult, FlowSnapshot, FlowWizardStep
from .registry import register_flow


class FamilyFlowForm(forms.Form):
    name = forms.CharField(
        required=False, max_length=100, label=_('Family name'),
        widget=forms.TextInput(attrs={'class': 'form-control', 'autocomplete': 'off'}),
        help_text=_('Shown at the top of the Family page. Leave it blank to call it "Our family".'))

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user


@register_flow
class FamilyFlow(Flow):
    key = 'family'
    label = _('Family')
    title = _('I want to share the big picture with my family')
    description = _('Start a family, then invite the people you trust. Nothing is shared until you choose it.')
    category = 'family'
    icon = 'bi-people'
    tags = [_('Family'), _('Private by default'), _('Pro')]
    estimated_time = _('Under 1 min')
    creates = [
        _('A family, with you as the owner'),
        _('Nothing shared yet'),
    ]
    form_class = FamilyFlowForm
    wizard_steps = [
        FlowWizardStep('family_basics', _('Your family'), ['name'],
                       _('Give the family a name. You can invite people on the next page.')),
    ]

    def is_available(self, user) -> bool:
        return family_enabled_for(user)

    def is_configured(self, user, snapshot: FlowSnapshot | None = None) -> bool:
        return households.household_for(user) is not None

    def get_edit_url(self, user) -> str:
        return reverse('family-home')

    def get_success_url(self, result) -> str:
        return reverse('family-home')

    def _problems(self, user) -> list[str]:
        """Why this user cannot start a family right now, in words, or an empty list."""
        if households.household_for(user) is not None:
            return [str(_('You are already in a family.'))]
        if not user.profile.is_pro:
            return [str(_('Family sharing needs a Pro plan, and so does everyone in your family.'))]
        return []

    def preview(self, user, cleaned_data) -> dict:
        name = (cleaned_data.get('name') or '').strip() or str(_('Our family'))
        return {
            'headline_text': name,
            'summary': str(_('You will be the owner. Nothing is shared until you choose it.')),
            'bullets': [
                _('A family, with you as the owner'),
                _('Nothing shared yet'),
            ],
            'warnings': self._problems(user),
        }

    def get_what_we_create(self, user, cleaned_data) -> list[dict]:
        return [
            {'icon': 'bi-people', 'title': _('Your family'), 'description': _('You are the owner')},
            {'icon': 'bi-shield-lock', 'title': _('Nothing shared yet'), 'description': _('You choose what to share')},
            {'icon': 'bi-envelope', 'title': _('Invitations next'), 'description': _('From the Family page')},
        ]

    def get_review_summary(self, user, cleaned_data) -> str:
        return str(_('You will be the owner. Nothing is shared until you choose it.'))

    def _existing_flow_result(self, existing, idempotency_key) -> FlowResult:
        household = households.household_for(existing.user)
        return FlowResult(flow_id=existing.id, idempotency_key=idempotency_key,
                          created=[household] if household is not None else [])

    def commit(self, user, cleaned_data, idempotency_key) -> FlowResult:
        existing = FinancialFlow.objects.filter(user=user, idempotency_key=idempotency_key).first()
        if existing:
            if existing.flow_key != self.key:
                raise ValidationError(_('This submission key was already used for a different flow. Please reload the page and try again.'))
            return self._existing_flow_result(existing, idempotency_key)
        problems = self._problems(user)
        if problems:
            raise ValidationError(problems)
        name = (cleaned_data.get('name') or '').strip()
        with transaction.atomic():
            try:
                household = households.create_household(user, name)
            except households.FamilyError as error:
                raise ValidationError(error.message) from error
            flow = FinancialFlow.objects.create(
                user=user, flow_key=self.key, spec={'name': household.name}, idempotency_key=idempotency_key)
            # Deliberately NOT recorded as a FlowCreatedObject: FinancialFlow.delete() deletes everything it created,
            # and a family may only be deleted by its owner typing its name (households.delete_household).
        return FlowResult(flow_id=flow.id, idempotency_key=idempotency_key, created=[household])
