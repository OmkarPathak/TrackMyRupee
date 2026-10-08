from __future__ import annotations

import math
from decimal import Decimal

from django import forms
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from ..models import Account, CURRENCY_CHOICES
from .base import CreateStep, Flow, FlowSnapshot, FlowWizardStep
from .registry import register_flow


class CreditCardFlowForm(forms.Form):
    existing_account = forms.ModelChoiceField(queryset=Account.objects.none(), required=False, widget=forms.Select(attrs={'class': 'form-select'}), help_text=_('Choose an existing card to update, or leave blank to create a new one.'))
    name = forms.CharField(required=False, widget=forms.TextInput(attrs={'class': 'form-control'}), help_text=_('The card label shown in the app.'))
    balance = forms.DecimalField(min_value=Decimal('0.00'), max_digits=15, decimal_places=2, widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}), help_text=_('Current amount owed on the card.'))
    currency = forms.ChoiceField(choices=CURRENCY_CHOICES, widget=forms.Select(attrs={'class': 'form-select'}), help_text=_('Currency used for the card balance and limit.'))
    credit_limit = forms.DecimalField(min_value=Decimal('0.00'), max_digits=15, decimal_places=2, widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}), help_text=_('Maximum available credit line.'))
    billing_day = forms.IntegerField(min_value=1, max_value=31, widget=forms.NumberInput(attrs={'class': 'form-control'}), help_text=_('Day of month the statement is generated.'))
    is_pinned = forms.BooleanField(required=False, initial=False, widget=forms.CheckboxInput(attrs={'class': 'form-check-input'}), help_text=_('Pin this card account.'))

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        if user:
            accounts = Account.objects.filter(user=user, is_active=True).order_by('name')
            self.fields['existing_account'].queryset = accounts.filter(account_type='CREDIT_CARD')
            self.fields['currency'].initial = user.profile.currency

    def clean(self):
        cleaned = super().clean()
        if not cleaned.get('existing_account') and not cleaned.get('name'):
            self.add_error('name', _('Name is required when adding a new credit card.'))
        return cleaned


from django.urls import reverse


@register_flow
class CreditCardFlow(Flow):
    key = 'creditcard'
    label = _('Credit Card')
    title = _('I got a credit card')
    description = _('Balance, limit and the billing cycle in one pass.')
    category = 'debt'
    icon = 'bi-credit-card'
    tags = [_('Card'), _('Limit'), _('Due-date reminder')]
    estimated_time = _('About 1 min')
    creates = [
        _('Revolving credit account'),
        _('Billing cycle and due date'),
        # Keep this copy aligned with send_notifications.Command._process_credit_card_reminders().
        _('Billing-date reminder, 3 days before each statement'),
    ]
    limit_map = {'accounts': Account}
    form_class = CreditCardFlowForm
    wizard_steps = [
        FlowWizardStep('card_basics', _('Card Basics'), ['existing_account', 'name', 'balance', 'currency'], _('Use this step to choose whether you are updating an existing card or creating a new one.')),
        FlowWizardStep('card_limits', _('Card Limits'), ['credit_limit', 'billing_day', 'is_pinned'], _('Limit and billing-day settings that drive reminders and balance tracking.')),
    ]

    def is_configured(self, user, snapshot: FlowSnapshot | None = None) -> bool:
        if snapshot is None:
            snapshot = FlowSnapshot.for_user(user)
        return 'CREDIT_CARD' in snapshot.account_types

    def get_edit_url(self, user) -> str:
        return reverse('account-list')

    def plan(self, data) -> list[CreateStep]:
        account_name = (
            data.get('name')
            or (data['existing_account'].name if data.get('existing_account') else _('Credit Card'))
        )
        account_payload = {
            'user': data['user'],
            'name': account_name,
            'account_type': 'CREDIT_CARD',
            'balance': -data['balance'],
            'currency': data['currency'],
            'credit_limit': data['credit_limit'],
            'credit_card_billing_day': data['billing_day'],
            'is_pinned': bool(data.get('is_pinned', False)),
        }
        if data.get('existing_account'):
            account_payload['pk'] = data['existing_account'].pk

        return [CreateStep(Account, account_payload, key='card')]

    def derive(self, cleaned_data) -> dict:
        data = dict(cleaned_data)
        data['balance'] = Decimal(str(data.get('balance') or 0))
        data['credit_limit'] = Decimal(str(data.get('credit_limit') or 0))
        data['currency'] = data.get('currency') or data['user'].profile.currency
        return data

    def preview(self, user, cleaned_data) -> dict:
        data = self.derive({**cleaned_data, 'user': user})
        steps = self.plan(data)
        warnings = self.check_limits(user, steps)
        balance = float(data['balance'])
        available_credit = float(data['credit_limit']) - balance
        return {
            'headline': float(data['credit_limit']),
            'bullets': [
                _('Creates or updates a revolving credit account'),
                _('Stores credit limit and billing day for reminders'),
                _('Available credit: %(credit)s') % {'credit': available_credit},
            ],
            'warnings': warnings,
        }

