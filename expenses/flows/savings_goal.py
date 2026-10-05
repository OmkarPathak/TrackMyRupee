from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from django import forms
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from finance_tracker.plans import get_limit

from ..models import SavingsGoal
from .base import CreateStep, Flow, FlowWizardStep
from .registry import register_flow


class SavingsGoalFlowForm(forms.Form):
    name = forms.CharField(widget=forms.TextInput(attrs={'class': 'form-control'}), help_text=_('Name of the savings goal.'))
    target_amount = forms.DecimalField(min_value=Decimal('0.01'), max_digits=15, decimal_places=2, widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}), help_text=_('Total amount you want to save.'))
    target_months = forms.IntegerField(min_value=1, widget=forms.NumberInput(attrs={'class': 'form-control'}), help_text=_('How many months you want to reach the goal in.'))
    icon = forms.CharField(required=False, widget=forms.TextInput(attrs={'class': 'form-control'}), help_text=_('Optional icon or emoji to personalize the goal.'))
    color = forms.ChoiceField(choices=[('primary', _('Blue')), ('success', _('Green')), ('danger', _('Red')), ('warning', _('Yellow')), ('info', _('Light Blue'))], widget=forms.Select(attrs={'class': 'form-select'}), help_text=_('Visual color used for the goal card.'))
    def __init__(self, *args, user=None, **kwargs):
        # user is accepted to match the shared form instantiation interface
        # (FlowBaseView.get_form always passes user=) but unused here
        # because this form has no user-scoped querysets.
        super().__init__(*args, **kwargs)


@register_flow
class SavingsGoalFlow(Flow):
    key = 'savingsgoal'
    label = _('Savings Goal')
    category = 'savings'
    icon = 'bi-bullseye'
    form_class = SavingsGoalFlowForm
    wizard_steps = [
        FlowWizardStep('goal_details', _('Goal Details'), ['name', 'target_amount', 'target_months'], _('Set the goal name and target horizon.')),
        FlowWizardStep('goal_style', _('Goal Style'), ['icon', 'color'], _('Optional styling to make the goal easier to recognise.')),
    ]

    def derive(self, cleaned_data) -> dict:
        data = dict(cleaned_data)
        target_amount = Decimal(str(data.get('target_amount') or 0))
        target_months = int(data.get('target_months') or 1)
        data['monthly_suggestion'] = (target_amount / Decimal(target_months)).quantize(Decimal('0.01'))
        data['target_date'] = timezone.localdate() + timedelta(days=30 * target_months)
        data['currency'] = data['user'].profile.currency
        return data

    def plan(self, data) -> list[CreateStep]:
        return [
            CreateStep(
                SavingsGoal,
                {
                    'user': data['user'],
                    'name': data['name'],
                    'target_amount': data['target_amount'],
                    'target_date': data['target_date'],
                    'icon': data.get('icon') or '🎯',
                    'color': data.get('color') or 'primary',
                    'currency': data['currency'],
                },
                key='goal',
            )
        ]

    def preview(self, user, cleaned_data) -> dict:
        data = self.derive({**cleaned_data, 'user': user})
        return {'headline': data['monthly_suggestion'], 'bullets': [_('Creates a savings goal')], 'warnings': self.check_limits(user, [])}

    def check_limits(self, user, steps) -> list[str]:
        limit = get_limit(user.profile.active_tier, 'savings_goals')
        if limit == -1:
            return []
        if user.savings_goals.count() >= limit:
            return [_('You have reached your current savings goal limit for this plan.')]
        return []
