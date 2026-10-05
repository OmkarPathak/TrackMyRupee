from __future__ import annotations

from decimal import Decimal

from django import forms
from django.utils.translation import gettext_lazy as _

from ..models import Account, CURRENCY_CHOICES, RecurringTransaction
from ..services_recurring import RecurringService
from .base import CreateStep, Flow, FlowWizardStep
from .registry import register_flow


class RentBillFlowForm(forms.Form):
    description = forms.CharField(widget=forms.TextInput(attrs={'class': 'form-control'}), help_text=_('Short description that appears on the recurring transaction.'))
    amount = forms.DecimalField(min_value=Decimal('0.01'), max_digits=15, decimal_places=2, widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}), help_text=_('The amount that repeats on each occurrence.'))
    currency = forms.ChoiceField(choices=CURRENCY_CHOICES, required=False, widget=forms.Select(attrs={'class': 'form-select'}), help_text=_('Currency used for the recurring entry.'))
    frequency = forms.ChoiceField(choices=RecurringTransaction.FREQUENCY_CHOICES, widget=forms.Select(attrs={'class': 'form-select'}), help_text=_('How often the bill or rent should repeat.'))
    account = forms.ModelChoiceField(queryset=Account.objects.none(), widget=forms.Select(attrs={'class': 'form-select'}), help_text=_('Account used to pay the recurring bill.'))
    start_date = forms.DateField(widget=forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}), help_text=_('Date the recurring schedule should start.'))

    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)
        if user:
            accounts = Account.objects.filter(user=user, is_active=True).order_by('name')
            self.fields['account'].queryset = accounts
            self.fields['account'].initial = accounts.filter(name='Cash').first() or accounts.first()
            self.fields['currency'].initial = user.profile.currency


@register_flow
class RentBillFlow(Flow):
    key = 'rentbill'
    label = _('Rent / Bill')
    category = 'bills'
    icon = 'bi-house'
    form_class = RentBillFlowForm
    wizard_steps = [
        FlowWizardStep('bill_basics', _('Bill Details'), ['description', 'amount', 'currency', 'account'], _('Basic bill information and the payment account.')),
        FlowWizardStep('bill_schedule', _('Schedule'), ['frequency', 'start_date'], _('How often it should repeat and when it starts.')),
    ]

    def derive(self, cleaned_data) -> dict:
        data = dict(cleaned_data)
        data['amount'] = Decimal(str(data.get('amount') or 0))
        data['currency'] = data.get('currency') or data['account'].currency
        return data

    def plan(self, data) -> list[CreateStep]:
        return [
            CreateStep(
                RecurringTransaction,
                {
                    'user': data['user'],
                    'transaction_type': 'EXPENSE',
                    'amount': data['amount'],
                    'currency': data['currency'],
                    'account': data['account'],
                    'category': 'Rent',
                    'description': data['description'],
                    'frequency': data['frequency'],
                    'start_date': data['start_date'],
                    'last_processed_date': RecurringService.last_due_before_today(data['start_date'], data['frequency']),
                    'is_active': True,
                },
                key='bill',
            )
        ]

    def preview(self, user, cleaned_data) -> dict:
        amount = Decimal(str(cleaned_data.get('amount') or 0))
        frequency = cleaned_data.get('frequency') or 'MONTHLY'
        multipliers = {'DAILY': 365, 'WEEKLY': 52, 'BIWEEKLY': 26, 'MONTHLY': 12, 'QUARTERLY': 4, 'SEMIANNUALLY': 2, 'YEARLY': 1}
        annual = float(amount) * multipliers.get(frequency, 12)
        return {'headline': annual, 'bullets': [_('Creates a recurring bill schedule')], 'warnings': []}
