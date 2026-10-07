from __future__ import annotations

from decimal import Decimal

from django import forms
from django.utils.translation import gettext_lazy as _

from django.urls import reverse

from ..models import Account, AssetValuation, CapitalEvent, Holding, Loan, PhysicalAsset, RecurringTransaction
from .base import CreateStep, Flow, FlowSnapshot, FlowWizardStep
from .loan import NewLoanFlow
from .registry import register_flow


class CarFlowForm(forms.Form):
    name = forms.CharField(widget=forms.TextInput(attrs={'class': 'form-control'}))
    purchase_price = forms.DecimalField(min_value=Decimal('0.01'), max_digits=15, decimal_places=2, widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}))
    acquisition_date = forms.DateField(widget=forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}))
    from_account = forms.ModelChoiceField(queryset=Account.objects.none(), widget=forms.Select(attrs={'class': 'form-select'}))
    is_pinned = forms.BooleanField(required=False, initial=False, widget=forms.CheckboxInput(attrs={'class': 'form-check-input'}), help_text=_('Pin vehicle account.'))
    custom_note = forms.CharField(max_length=255, required=False, widget=forms.TextInput(attrs={'class': 'form-control'}), help_text=_('Optional custom note for vehicle purchase.'))
    financed = forms.BooleanField(required=False, widget=forms.CheckboxInput(attrs={'class': 'form-check-input'}))
    loan_name = forms.CharField(required=False, widget=forms.TextInput(attrs={'class': 'form-control'}))
    annual_rate = forms.DecimalField(required=False, min_value=Decimal('0.00'), max_digits=7, decimal_places=2, widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}))
    tenure_months = forms.IntegerField(required=False, min_value=1, widget=forms.NumberInput(attrs={'class': 'form-control'}))
    loan_start_date = forms.DateField(required=False, widget=forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}))

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['from_account'].required = False
        if user:
            accounts = Account.objects.filter(user=user, is_active=True).order_by('name')
            self.fields['from_account'].queryset = accounts
            self.fields['from_account'].initial = accounts.filter(name='Cash').first() or accounts.first()
            if not accounts.exists():
                self.fields['from_account'].help_text = _('No active accounts found. <a href="/accounts/add/" target="_blank" class="fw-semibold text-decoration-underline">Add an account</a> or leave blank.')

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
    is_pinned = forms.BooleanField(required=False, initial=False, widget=forms.CheckboxInput(attrs={'class': 'form-check-input'}), help_text=_('Pin gold account.'))

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['from_account'].required = False
        if user:
            accounts = Account.objects.filter(user=user, is_active=True).order_by('name')
            self.fields['from_account'].queryset = accounts
            self.fields['from_account'].initial = accounts.filter(name='Cash').first() or accounts.first()
            if not accounts.exists():
                self.fields['from_account'].help_text = _('No active accounts found. <a href="/accounts/add/" target="_blank" class="fw-semibold text-decoration-underline">Add an account</a> or leave blank.')


@register_flow
class CarFlow(Flow):
    key = 'car'
    label = _('Car')
    title = _('I bought a car')
    description = _('Cash or financed — either way it lands in your net worth.')
    category = 'assets'
    icon = 'bi-car-front'
    tags = [_('Vehicle'), _('Optional loan')]
    estimated_time = _('About 2 min')
    creates = [
        _('Vehicle asset record & account'),
        _('Loan + EMI, if financed'),
        _('One-time purchase entry'),
    ]
    limit_map = {
        'accounts': Account,
        'loans': Loan,
        'recurring_transactions': RecurringTransaction,
    }
    form_class = CarFlowForm
    wizard_steps = [
        FlowWizardStep('car_basics', _('Car Details'), ['name', 'purchase_price', 'acquisition_date', 'from_account', 'is_pinned', 'custom_note'], _('Basic vehicle information and purchase account.')),
        FlowWizardStep('car_financing', _('Financing'), ['financed', 'loan_name', 'annual_rate', 'tenure_months', 'loan_start_date'], _('Loan details if the car was financed.')),
    ]

    def is_configured(self, user, snapshot: FlowSnapshot | None = None) -> bool:
        if snapshot is None:
            snapshot = FlowSnapshot.for_user(user)
        return 'VEHICLE' in snapshot.asset_classes or 'VEHICLE' in snapshot.account_types

    def get_edit_url(self, user) -> str:
        return reverse('account-list')

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
                    Account,
                    {
                        'user': data['user'],
                        'name': loan_data['name'],
                        'account_type': 'VEHICLE_LOAN',
                        'balance': Decimal('0.00'),
                        'currency': data['currency'],
                        'linked_loan': '$loan',
                        'is_active': True,
                    },
                    key='loan_account',
                )
            )

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
                AssetValuation,
                {
                    'asset': '$asset',
                    'value': data['purchase_price'],
                    'as_of_date': data['acquisition_date'],
                    'source': 'Purchase',
                },
                key='valuation',
            )
        )
        steps.append(
            CreateStep(
                Account,
                {
                    'user': data['user'],
                    'name': data['name'],
                    'account_type': 'VEHICLE',
                    'balance': data['purchase_price'],
                    'currency': data['currency'],
                    'linked_physical_asset': '$asset',
                    'is_active': True,
                    'is_pinned': bool(data.get('is_pinned', False)),
                },
                key='vehicle_account',
            )
        )
        if not data.get('financed'):
            note = data.get('custom_note') or _('Car purchase')
            steps.append(
                CreateStep(
                    CapitalEvent,
                    {
                        'user': data['user'],
                        'amount': data['purchase_price'],
                        'date': data['acquisition_date'],
                        'subtype': 'large_purchase',
                        'note': note,
                        'account': data['from_account'],
                        'currency': data['currency'],
                    },
                    key='purchase',
                )
            )
        return steps

    def derive(self, cleaned_data) -> dict:
        from datetime import date
        data = dict(cleaned_data)
        data['purchase_price'] = Decimal(str(data.get('purchase_price') or 0))
        data['name'] = data.get('name') or str(_('Car'))
        data['acquisition_date'] = data.get('acquisition_date') or date.today()
        user = data.get('user')
        data['currency'] = (
            data['from_account'].currency
            if data.get('from_account')
            else (getattr(getattr(user, 'profile', None), 'currency', '₹') if user else '₹')
        )
        return data

    def preview(self, user, cleaned_data) -> dict:
        data = self.derive({**cleaned_data, 'user': user})
        steps = self.plan(data)
        warnings = self.check_limits(user, steps)
        headline = float(data['purchase_price'])
        return {'headline': headline, 'bullets': [_('Creates a vehicle asset')], 'warnings': warnings}


@register_flow
class GoldFlow(Flow):
    key = 'gold'
    label = _('Gold')
    title = _('I bought gold')
    description = _('Physical jewelry or digital/SGB — tracked either way.')
    category = 'assets'
    icon = 'bi-gem'
    tags = [_('Physical or digital'), _('Net worth')]
    estimated_time = _('Under 1 min')
    creates = [
        _('Gold holding & investment account'),
    ]
    limit_map = {'accounts': Account}
    form_class = GoldFlowForm
    wizard_steps = [
        FlowWizardStep('gold_basics', _('Gold Details'), ['route', 'name', 'amount', 'acquisition_date', 'from_account', 'is_pinned'], _('Route, cost, and payment account for your gold purchase.')),
    ]

    def is_configured(self, user, snapshot: FlowSnapshot | None = None) -> bool:
        if snapshot is None:
            snapshot = FlowSnapshot.for_user(user)
        return 'GOLD' in snapshot.asset_classes or bool(snapshot.account_types & {'GOLD', 'SGB'})

    def get_edit_url(self, user) -> str:
        return reverse('account-list')

    def plan(self, data) -> list[CreateStep]:
        steps = []
        if data['route'] == 'physical':
            steps.extend([
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
                ),
                CreateStep(
                    AssetValuation,
                    {
                        'asset': '$asset',
                        'value': data['amount'],
                        'as_of_date': data['acquisition_date'],
                        'source': 'Purchase',
                    },
                    key='valuation',
                ),
                CreateStep(
                    Account,
                    {
                        'user': data['user'],
                        'name': data['name'],
                        'account_type': 'GOLD',
                        'balance': data['amount'],
                        'currency': data['currency'],
                        'linked_physical_asset': '$asset',
                        'is_active': True,
                        'is_pinned': bool(data.get('is_pinned', False)),
                    },
                    key='account',
                ),
                CreateStep(
                    Holding,
                    {
                        'account': '$account',
                        'instrument_name': data['name'],
                        'instrument_type': 'OTHER',
                        'units': Decimal('1.000000'),
                        'avg_cost': data['amount'],
                        'currency': data['currency'],
                    },
                    key='holding',
                ),
            ])
        else:
            steps.extend([
                CreateStep(
                    Account,
                    {
                        'user': data['user'],
                        'name': data['name'],
                        'account_type': 'SGB',
                        'balance': Decimal('0.00'),
                        'currency': data['currency'],
                        'is_pinned': bool(data.get('is_pinned', False)),
                    },
                    key='account',
                ),
                CreateStep(
                    Holding,
                    {
                        'account': '$account',
                        'instrument_name': data['name'],
                        'instrument_type': 'OTHER',
                        'units': Decimal('1.000000'),
                        'avg_cost': data['amount'],
                        'currency': data['currency'],
                    },
                    key='holding',
                ),
            ])

        if data.get('from_account'):
            steps.append(
                CreateStep(
                    CapitalEvent,
                    {
                        'user': data['user'],
                        'amount': data['amount'],
                        'date': data['acquisition_date'],
                        'subtype': 'investment_lump_sum',
                        'note': _('Gold purchase: %(name)s') % {'name': data['name']},
                        'account': data['from_account'],
                        'currency': data['currency'],
                    },
                    key='purchase',
                )
            )

        return steps

    def derive(self, cleaned_data) -> dict:
        from datetime import date
        data = dict(cleaned_data)
        data['amount'] = Decimal(str(data.get('amount') or 0))
        data['route'] = data.get('route') or 'physical'
        data['name'] = data.get('name') or str(_('Gold'))
        data['acquisition_date'] = data.get('acquisition_date') or date.today()
        user = data.get('user')
        data['currency'] = (
            data['from_account'].currency
            if data.get('from_account')
            else (getattr(getattr(user, 'profile', None), 'currency', '₹') if user else '₹')
        )
        return data

    def preview(self, user, cleaned_data) -> dict:
        data = self.derive({**cleaned_data, 'user': user})
        steps = self.plan(data)
        warnings = self.check_limits(user, steps)
        return {'headline': float(data['amount']), 'bullets': [_('Creates a gold asset or SGB holding')], 'warnings': warnings}


