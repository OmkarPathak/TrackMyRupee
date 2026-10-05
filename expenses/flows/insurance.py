from __future__ import annotations

from decimal import Decimal

from django import forms
from django.utils.translation import gettext_lazy as _

from ..models import Account, PhysicalAsset, RecurringTransaction
from ..services_recurring import RecurringService
from .base import CreateStep, Flow, FlowWizardStep
from .registry import register_flow


class InsuranceFlowForm(forms.Form):
    name = forms.CharField(widget=forms.TextInput(attrs={'class': 'form-control'}), help_text=_('Policy or plan name.'))
    premium_amount = forms.DecimalField(min_value=Decimal('0.01'), max_digits=15, decimal_places=2, widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}), help_text=_('Amount of each premium payment.'))
    premium_frequency = forms.ChoiceField(choices=PhysicalAsset.PREMIUM_FREQUENCY_CHOICES, widget=forms.Select(attrs={'class': 'form-select'}), help_text=_('How often the premium is paid.'))
    policy_number = forms.CharField(required=False, widget=forms.TextInput(attrs={'class': 'form-control'}), help_text=_('Optional policy reference number.'))
    sum_assured = forms.DecimalField(required=False, min_value=Decimal('0.00'), max_digits=15, decimal_places=2, widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}), help_text=_('Optional coverage / sum assured amount.'))
    premium_payment_account = forms.ModelChoiceField(queryset=Account.objects.none(), widget=forms.Select(attrs={'class': 'form-select'}), help_text=_('Account used to pay premiums.'))
    start_date = forms.DateField(widget=forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}), help_text=_('Date the policy tracking starts.'))

    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)
        if user:
            accounts = Account.objects.filter(user=user, is_active=True).order_by('name')
            self.fields['premium_payment_account'].queryset = accounts
            self.fields['premium_payment_account'].initial = accounts.filter(name='Cash').first() or accounts.first()


from django.urls import reverse


@register_flow
class InsuranceFlow(Flow):
    key = 'insurance'
    label = _('Insurance')
    title = _('I bought insurance')
    description = _('Policy details and the premium that renews on its own.')
    category = 'bills'
    icon = 'bi-shield-check'
    tags = [_('Policy'), _('Premium reminder')]
    estimated_time = _('About 1 min')
    creates = [
        _('Policy asset record'),
        _('Recurring premium payment'),
    ]
    limit_map = {
        'accounts': Account,
        'recurring_transactions': RecurringTransaction,
    }
    form_class = InsuranceFlowForm
    wizard_steps = [
        FlowWizardStep('policy_details', _('Policy Details'), ['name', 'policy_number', 'sum_assured'], _('Basic policy information for your records.')),
        FlowWizardStep('premium_schedule', _('Premium Schedule'), ['premium_amount', 'premium_frequency', 'premium_payment_account', 'start_date'], _('How and when premiums should be tracked.')),
    ]

    def is_configured(self, user) -> bool:
        return (
            PhysicalAsset.objects.filter(user=user, is_active=True, asset_class='INSURANCE').exists()
            or Account.objects.filter(user=user, is_active=True, account_type='LIFE_INSURANCE').exists()
        )

    def get_edit_url(self, user) -> str:
        return reverse('account-list')

    def derive(self, cleaned_data) -> dict:
        data = dict(cleaned_data)
        data['premium_amount'] = Decimal(str(data.get('premium_amount') or 0))
        data['currency'] = data['premium_payment_account'].currency if data.get('premium_payment_account') else data['user'].profile.currency
        return data

    def plan(self, data) -> list[CreateStep]:
        asset = CreateStep(
            PhysicalAsset,
            {
                'user': data['user'],
                'name': data['name'],
                'asset_class': 'INSURANCE',
                'policy_number': data.get('policy_number') or '',
                'premium_amount': data.get('premium_amount'),
                'premium_frequency': data.get('premium_frequency'),
                'policy_start_date': data['start_date'],
                'sum_assured': data.get('sum_assured'),
                'currency': data['currency'],
            },
            key='asset',
        )
        account = CreateStep(
            Account,
            {
                'user': data['user'],
                'name': data['name'],
                'account_type': 'LIFE_INSURANCE',
                'balance': Decimal('0.00'),
                'currency': data['currency'],
                'linked_physical_asset': '$asset',
                'is_active': True,
            },
            key='insurance_account',
        )
        recurring = CreateStep(
            RecurringTransaction,
            {
                'user': data['user'],
                'transaction_type': 'INSURANCE_PREMIUM',
                'amount': data['premium_amount'],
                'currency': data['currency'],
                'account': data['premium_payment_account'],
                'physical_asset': '$asset',
                'frequency': _premium_to_recurring_frequency(data.get('premium_frequency')),
                'start_date': data['start_date'],
                'last_processed_date': RecurringService.last_due_before_today(data['start_date'], _premium_to_recurring_frequency(data.get('premium_frequency'))),
                'description': _('%(name)s premium') % {'name': data['name']},
                'is_active': True,
            },
            key='premium',
        )
        return [asset, account, recurring]

    def preview(self, user, cleaned_data) -> dict:
        data = self.derive({**cleaned_data, 'user': user})
        steps = self.plan(data)
        warnings = self.check_limits(user, steps)
        annual_factor = {
            'ANNUAL': 1,
            'SEMI_ANNUAL': 2,
            'QUARTERLY': 4,
            'MONTHLY': 12,
        }.get(data.get('premium_frequency'), 1)
        return {
            'headline': float(data['premium_amount']) * annual_factor,
            'bullets': [
                _('Creates an insurance policy, linked account, and premium schedule'),
            ],
            'warnings': warnings,
        }


def _premium_to_recurring_frequency(premium_frequency: str) -> str:
    return {
        'ANNUAL': 'YEARLY',
        'SEMI_ANNUAL': 'SEMIANNUALLY',
        'QUARTERLY': 'QUARTERLY',
        'MONTHLY': 'MONTHLY',
    }.get(premium_frequency or 'ANNUAL', 'YEARLY')
