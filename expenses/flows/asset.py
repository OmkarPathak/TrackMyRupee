from __future__ import annotations

from decimal import Decimal

from django import forms
from django.utils.translation import gettext_lazy as _

from ..models import Account, CapitalEvent, Holding, PhysicalAsset
from .base import CreateStep, Flow
from .loan import NewLoanFlow
from .registry import register_flow


class CarFlowForm(forms.Form):
    name = forms.CharField(widget=forms.TextInput(attrs={'class': 'form-control'}))
    purchase_price = forms.DecimalField(min_value=Decimal('0.01'), max_digits=15, decimal_places=2, widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}))
    acquisition_date = forms.DateField(widget=forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}))
    from_account = forms.ModelChoiceField(queryset=Account.objects.none(), widget=forms.Select(attrs={'class': 'form-select'}))
    financed = forms.BooleanField(required=False, widget=forms.CheckboxInput(attrs={'class': 'form-check-input'}))
    loan_name = forms.CharField(required=False, widget=forms.TextInput(attrs={'class': 'form-control'}))
    annual_rate = forms.DecimalField(required=False, min_value=Decimal('0.00'), max_digits=7, decimal_places=2, widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}))
    tenure_months = forms.IntegerField(required=False, min_value=1, widget=forms.NumberInput(attrs={'class': 'form-control'}))
    loan_start_date = forms.DateField(required=False, widget=forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}))

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        if user:
            accounts = Account.objects.filter(user=user, is_active=True).order_by('name')
            self.fields['from_account'].queryset = accounts
            self.fields['from_account'].initial = accounts.filter(name='Cash').first() or accounts.first()

    def clean(self):
        cleaned = super().clean()
        if cleaned.get('financed'):
            if not cleaned.get('annual_rate'):
                self.add_error('annual_rate', _('Annual interest rate is required for a financed car.'))
            if not cleaned.get('tenure_months'):
                self.add_error('tenure_months', _('Loan tenure is required for a financed car.'))
        return cleaned


class GoldFlowForm(forms.Form):
    route = forms.ChoiceField(choices=[('physical', _('Physical')), ('digital', _('Digital SGB'))], widget=forms.Select(attrs={'class': 'form-select'}))
    name = forms.CharField(widget=forms.TextInput(attrs={'class': 'form-control'}))
    amount = forms.DecimalField(min_value=Decimal('0.01'), max_digits=15, decimal_places=2, widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}))
    acquisition_date = forms.DateField(widget=forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}))
    from_account = forms.ModelChoiceField(queryset=Account.objects.none(), required=False, widget=forms.Select(attrs={'class': 'form-select'}))

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        if user:
            accounts = Account.objects.filter(user=user, is_active=True).order_by('name')
            self.fields['from_account'].queryset = accounts
            self.fields['from_account'].initial = accounts.filter(name='Cash').first() or accounts.first()


@register_flow
class CarFlow(Flow):
    key = 'car'
    label = _('Car')
    category = 'assets'
    icon = 'bi-car-front'
    form_class = CarFlowForm

    def plan(self, data) -> list[CreateStep]:
        steps = []
        if data.get('financed'):
            loan_data = {
                'user': data['user'],
                'name': data.get('loan_name') or f"{data['name']} Loan",
                'loan_type': 'CAR',
                'principal': data['purchase_price'],
                'annual_rate': data.get('annual_rate') or Decimal('0.00'),
                'tenure_months': data.get('tenure_months') or 1,
                'start_date': data.get('loan_start_date') or data['acquisition_date'],
                'currency': data['currency'],
                'payment_account': data['from_account'],
                'include_down_payment': False,
                'mid_tenure': False,
            }
            steps.extend(NewLoanFlow().plan(loan_data))

        steps.append(
            CreateStep(
                PhysicalAsset,
                {
                    'user': data['user'],
                    'name': data['name'],
                    'asset_class': 'VEHICLE',
                    'acquisition_cost': data['purchase_price'],
                    'acquisition_date': data['acquisition_date'],
                    'currency': data['currency'],
                },
                key='asset',
            )
        )
        steps.append(
            CreateStep(
                CapitalEvent,
                {
                    'user': data['user'],
                    'amount': data['purchase_price'],
                    'date': data['acquisition_date'],
                    'subtype': 'large_purchase',
                    'note': _('Car purchase'),
                    'account': data['from_account'],
                    'currency': data['currency'],
                },
                key='purchase',
            )
        )
        return steps

    def derive(self, cleaned_data) -> dict:
        data = dict(cleaned_data)
        data['purchase_price'] = Decimal(str(data.get('purchase_price') or 0))
        data['currency'] = data['from_account'].currency if data.get('from_account') else data['user'].profile.currency
        return data

    def preview(self, user, cleaned_data) -> dict:
        data = self.derive({**cleaned_data, 'user': user})
        headline = float(data['purchase_price'])
        return {'headline': headline, 'bullets': [_('Creates a vehicle asset')], 'warnings': []}


@register_flow
class GoldFlow(Flow):
    key = 'gold'
    label = _('Gold')
    category = 'assets'
    icon = 'bi-gem'
    form_class = GoldFlowForm

    def plan(self, data) -> list[CreateStep]:
        if data['route'] == 'physical':
            return [
                CreateStep(
                    PhysicalAsset,
                    {
                        'user': data['user'],
                        'name': data['name'],
                        'asset_class': 'GOLD',
                        'acquisition_cost': data['amount'],
                        'acquisition_date': data['acquisition_date'],
                        'currency': data['currency'],
                    },
                    key='asset',
                )
            ]

        return [
            CreateStep(
                Account,
                {
                    'user': data['user'],
                    'name': data['name'],
                    'account_type': 'SGB',
                    'balance': Decimal('0.00'),
                    'currency': data['currency'],
                },
                key='account',
            ),
            CreateStep(
                Holding,
                {
                    'account': '$account',
                    'instrument_name': data['name'],
                    'instrument_type': 'OTHER',
                    'avg_cost': data['amount'],
                    'currency': data['currency'],
                },
                key='holding',
            ),
        ]

    def derive(self, cleaned_data) -> dict:
        data = dict(cleaned_data)
        data['amount'] = Decimal(str(data.get('amount') or 0))
        data['currency'] = data['from_account'].currency if data.get('from_account') else data['user'].profile.currency
        return data

    def preview(self, user, cleaned_data) -> dict:
        data = self.derive({**cleaned_data, 'user': user})
        return {'headline': float(data['amount']), 'bullets': [_('Creates a gold asset or SGB holding')], 'warnings': []}

