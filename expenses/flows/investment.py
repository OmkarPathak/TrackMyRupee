from __future__ import annotations

from decimal import Decimal
import math

from django import forms
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from ..models import Account, CapitalEvent, Holding, RecurringTransaction
from ..services_recurring import RecurringService
from .base import CreateStep, Flow, FlowWizardStep
from .registry import register_flow


class SipRdFlowForm(forms.Form):
    instrument_type = forms.ChoiceField(
        choices=[('SIP', 'SIP'), ('RD', 'RD')],
        widget=forms.Select(attrs={'class': 'form-select'}),
        help_text=_('Pick SIP for market-linked investing or RD for a recurring deposit.'),
    )
    name = forms.CharField(
        widget=forms.TextInput(attrs={'class': 'form-control'}),
        help_text=_('Name shown for the investment or deposit account.'),
    )
    amount = forms.DecimalField(
        min_value=Decimal('0.01'),
        max_digits=15,
        decimal_places=2,
        widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
        help_text=_('Monthly contribution amount. For RD, this is the installment amount.'),
    )
    frequency = forms.ChoiceField(
        choices=RecurringTransaction.FREQUENCY_CHOICES,
        widget=forms.Select(attrs={'class': 'form-select'}),
        help_text=_('How often the contribution should repeat.'),
    )
    deposit_principal = forms.DecimalField(
        required=False,
        min_value=Decimal('0.01'),
        max_digits=15,
        decimal_places=2,
        widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
        help_text=_('Principal used for deposit valuation and accrued-balance calculations.'),
    )
    deposit_rate = forms.DecimalField(
        required=False,
        min_value=Decimal('0.00'),
        max_digits=7,
        decimal_places=2,
        widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
        help_text=_('Annual interest rate for the deposit.'),
    )
    deposit_start_date = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
        help_text=_('Date the deposit or recurring plan starts.'),
    )
    deposit_compounding = forms.ChoiceField(
        choices=Account.COMPOUNDING_CHOICES,
        required=False,
        initial='QUARTERLY',
        widget=forms.Select(attrs={'class': 'form-select'}),
        help_text=_('How interest should compound for deposit-style accounts.'),
    )
    deposit_maturity_date = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
        help_text=_('Optional maturity date for the deposit.'),
    )
    rd_installment_day = forms.IntegerField(
        required=False,
        min_value=1,
        max_value=28,
        widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '1', 'min': '1', 'max': '28'}),
        help_text=_('Day of month the RD installment should be posted.'),
    )
    show_accrued_balance = forms.BooleanField(
        required=False,
        initial=True,
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        help_text=_('Show projected accrued balance instead of the ledger balance.'),
    )
    record_maturity_income = forms.BooleanField(
        required=False,
        initial=False,
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        help_text=_('Create maturity income when the deposit completes.'),
    )
    from_account = forms.ModelChoiceField(
        queryset=Account.objects.none(),
        widget=forms.Select(attrs={'class': 'form-select'}),
        help_text=_('Source account used to fund the contribution.'),
    )

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        if user:
            accounts = Account.objects.filter(user=user, is_active=True).order_by('name')
            self.fields['from_account'].queryset = accounts
            self.fields['from_account'].initial = accounts.filter(name='Cash').first() or accounts.first()

    def clean(self):
        cleaned_data = super().clean()
        if cleaned_data.get('instrument_type') == 'RD':
            if cleaned_data.get('deposit_principal') is None:
                self.add_error('deposit_principal', _('Deposit principal is required for recurring deposits.'))
            if cleaned_data.get('deposit_rate') is None:
                self.add_error('deposit_rate', _('Interest rate is required for recurring deposits.'))
            if not cleaned_data.get('deposit_start_date'):
                self.add_error('deposit_start_date', _('Start date is required for recurring deposits.'))
            if not cleaned_data.get('rd_installment_day'):
                self.add_error('rd_installment_day', _('Installment day is required for recurring deposits.'))
        return cleaned_data


class FdFlowForm(forms.Form):
    name = forms.CharField(
        widget=forms.TextInput(attrs={'class': 'form-control'}),
        help_text=_('Display name for the fixed deposit account.'),
    )
    principal = forms.DecimalField(
        min_value=Decimal('0.01'),
        max_digits=15,
        decimal_places=2,
        widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
        help_text=_('Original amount placed into the deposit.'),
    )
    annual_rate = forms.DecimalField(
        min_value=Decimal('0.00'),
        max_digits=7,
        decimal_places=2,
        widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
        help_text=_('Annual interest rate used for accrual calculations.'),
    )
    deposit_start_date = forms.DateField(
        widget=forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
        help_text=_('Date the deposit was booked.'),
    )
    maturity_date = forms.DateField(
        widget=forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
        help_text=_('Planned maturity date.'),
    )
    deposit_compounding = forms.ChoiceField(
        choices=Account.COMPOUNDING_CHOICES,
        initial='QUARTERLY',
        widget=forms.Select(attrs={'class': 'form-select'}),
        help_text=_('How frequently interest should be compounded.'),
    )
    show_accrued_balance = forms.BooleanField(
        required=False,
        initial=True,
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        help_text=_('Show accrued balance rather than the raw ledger balance.'),
    )
    record_maturity_income = forms.BooleanField(
        required=False,
        initial=False,
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        help_text=_('Log the earned interest as income when the deposit matures.'),
    )
    deposit_closed_date = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
        help_text=_('Optional actual close date if the deposit is broken early.'),
    )
    from_account = forms.ModelChoiceField(
        queryset=Account.objects.none(),
        widget=forms.Select(attrs={'class': 'form-select'}),
        help_text=_('Account used to fund the deposit.'),
    )

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        if user:
            accounts = Account.objects.filter(user=user, is_active=True).order_by('name')
            self.fields['from_account'].queryset = accounts
            self.fields['from_account'].initial = accounts.filter(name='Cash').first() or accounts.first()


class PpfEpfNpsFlowForm(forms.Form):
    scheme_type = forms.ChoiceField(
        choices=[('PPF', 'PPF'), ('EPF', 'EPF'), ('NPS', 'NPS')],
        widget=forms.Select(attrs={'class': 'form-select'}),
        help_text=_('Select the retirement scheme you want to track.'),
    )
    name = forms.CharField(
        widget=forms.TextInput(attrs={'class': 'form-control'}),
        help_text=_('Name for the scheme account.'),
    )
    annual_amount = forms.DecimalField(
        min_value=Decimal('0.01'),
        max_digits=15,
        decimal_places=2,
        widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
        help_text=_('The recurring annual contribution amount.'),
    )
    deposit_principal = forms.DecimalField(
        min_value=Decimal('0.01'),
        max_digits=15,
        decimal_places=2,
        widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
        help_text=_('Opening principal for accrual tracking.'),
    )
    deposit_rate = forms.DecimalField(
        min_value=Decimal('0.00'),
        max_digits=7,
        decimal_places=2,
        widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
        help_text=_('Annual rate used for accrued balance calculations.'),
    )
    deposit_start_date = forms.DateField(
        widget=forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
        help_text=_('When the scheme tracking should begin.'),
    )
    deposit_compounding = forms.ChoiceField(
        choices=Account.COMPOUNDING_CHOICES,
        initial='QUARTERLY',
        widget=forms.Select(attrs={'class': 'form-select'}),
        help_text=_('How often interest compounds for valuation.'),
    )
    deposit_maturity_date = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
        help_text=_('Optional maturity date if the scheme has a fixed term.'),
    )
    deposit_closed_date = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
        help_text=_('Optional actual close date if the scheme is stopped early.'),
    )
    show_accrued_balance = forms.BooleanField(
        required=False,
        initial=True,
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        help_text=_('Show projected accrued balance in dashboards.'),
    )
    record_maturity_income = forms.BooleanField(
        required=False,
        initial=False,
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        help_text=_('Create maturity income when the scheme completes.'),
    )
    from_account = forms.ModelChoiceField(
        queryset=Account.objects.none(),
        widget=forms.Select(attrs={'class': 'form-select'}),
        help_text=_('Account used to pay each annual contribution.'),
    )

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        if user:
            accounts = Account.objects.filter(user=user, is_active=True).order_by('name')
            self.fields['from_account'].queryset = accounts
            self.fields['from_account'].initial = accounts.filter(name='Cash').first() or accounts.first()


@register_flow
class SipRdFlow(Flow):
    key = 'sip'
    label = _('SIP / RD')
    category = 'savings'
    icon = 'bi-arrow-repeat'
    form_class = SipRdFlowForm
    wizard_steps = [
        FlowWizardStep('investment_basics', _('Investment Basics'), ['instrument_type', 'name', 'amount', 'frequency', 'from_account'], _('Tell us what you are investing in and how often it should repeat.')),
        FlowWizardStep('deposit_rd_details', _('Deposit / RD Details'), ['deposit_principal', 'deposit_rate', 'deposit_start_date', 'deposit_compounding', 'deposit_maturity_date', 'rd_installment_day', 'show_accrued_balance', 'record_maturity_income'], _('Only some fields apply depending on whether you select SIP or RD.')),
    ]

    def plan(self, data) -> list[CreateStep]:
        account_type = 'MUTUAL_FUND' if data['instrument_type'] == 'SIP' else 'RD'
        if data['instrument_type'] == 'RD':
            investment_account = CreateStep(
                Account,
                {
                    'user': data['user'],
                    'name': data['name'],
                    'account_type': account_type,
                    'balance': Decimal('0.00'),
                    'currency': data['currency'],
                    'deposit_principal': data['deposit_principal'],
                    'deposit_rate': data['deposit_rate'],
                    'deposit_start_date': data['deposit_start_date'],
                    'deposit_compounding': data['deposit_compounding'],
                    'deposit_maturity_date': data.get('deposit_maturity_date'),
                    'show_accrued_balance': data['show_accrued_balance'],
                    'record_maturity_income': data['record_maturity_income'],
                    'rd_installment_amount': data['amount'],
                    'rd_installment_day': data['rd_installment_day'],
                },
                key='account',
            )
            recurring_start = data['deposit_start_date']
        else:
            investment_account = CreateStep(
                Account,
                {
                    'user': data['user'],
                    'name': data['name'],
                    'account_type': account_type,
                    'balance': Decimal('0.00'),
                    'currency': data['currency'],
                },
                key='account',
            )
            recurring_start = data.get('deposit_start_date') or data['from_account'].created_at.date()

        recurring = CreateStep(
            RecurringTransaction,
            {
                'user': data['user'],
                'transaction_type': 'TRANSFER',
                'amount': data['amount'],
                'currency': data['currency'],
                'from_account': data['from_account'],
                'to_account': '$account',
                'frequency': data['frequency'],
                'start_date': recurring_start,
                'last_processed_date': RecurringService.last_due_before_today(recurring_start, data['frequency']),
                'description': _('Investment contribution: %(name)s') % {'name': data['name']},
                'is_active': True,
            },
            key='transfer',
        )
        return [investment_account, recurring]

    def derive(self, cleaned_data) -> dict:
        data = dict(cleaned_data)
        data['amount'] = Decimal(str(data.get('amount') or 0))
        data['currency'] = data.get('currency') or data['from_account'].currency
        data['deposit_start_date'] = data.get('deposit_start_date') or data['from_account'].created_at.date()
        return data

    def preview(self, user, cleaned_data) -> dict:
        data = self.derive(cleaned_data)
        return {'headline': float(data['amount']), 'bullets': [_('Creates an investment account and contribution schedule')], 'warnings': []}


@register_flow
class FdFlow(Flow):
    key = 'fd'
    label = _('Fixed Deposit')
    category = 'savings'
    icon = 'bi-bank'
    form_class = FdFlowForm
    wizard_steps = [
        FlowWizardStep('fd_basics', _('Deposit Basics'), ['name', 'principal', 'annual_rate', 'from_account'], _('Core deposit details and funding account.')),
        FlowWizardStep('fd_terms', _('Deposit Terms'), ['deposit_start_date', 'maturity_date', 'deposit_compounding', 'deposit_closed_date'], _('When the deposit begins, ends, and how it compounds.')),
        FlowWizardStep('fd_reporting', _('Reporting'), ['show_accrued_balance', 'record_maturity_income'], _('Controls whether users see accruals and whether maturity interest is auto-recorded.')),
    ]

    def plan(self, data) -> list[CreateStep]:
        account = CreateStep(
            Account,
            {
                'user': data['user'],
                'name': data['name'],
                'account_type': 'FD',
                'balance': Decimal('0.00'),
                'currency': data['currency'],
                'deposit_principal': data['principal'],
                'deposit_rate': data['annual_rate'],
                'deposit_start_date': data['deposit_start_date'],
                'deposit_compounding': data['deposit_compounding'],
                'deposit_maturity_date': data['maturity_date'],
                'deposit_closed_date': data.get('deposit_closed_date'),
                'show_accrued_balance': data['show_accrued_balance'],
                'record_maturity_income': data['record_maturity_income'],
            },
            key='account',
        )
        capital = CreateStep(
            CapitalEvent,
            {
                'user': data['user'],
                'amount': data['principal'],
                'date': data['maturity_date'],
                'subtype': 'investment_lump_sum',
                'note': _('FD investment'),
                'account': data['from_account'],
                'currency': data['currency'],
            },
            key='capital',
        )
        return [account, capital]

    def derive(self, cleaned_data) -> dict:
        data = dict(cleaned_data)
        data['principal'] = Decimal(str(data.get('principal') or 0))
        data['annual_rate'] = Decimal(str(data.get('annual_rate') or 0))
        data['currency'] = data['from_account'].currency
        return data

    def preview(self, user, cleaned_data) -> dict:
        data = self.derive(cleaned_data)
        days = max((data['maturity_date'] - data['deposit_start_date']).days, 1)
        headline = float(data['principal'] + (data['principal'] * data['annual_rate'] * Decimal(days) / Decimal('36500')))
        return {'headline': headline, 'bullets': [_('Creates a lump-sum fixed deposit')], 'warnings': []}


@register_flow
class PpfEpfNpsFlow(Flow):
    key = 'ppfepfnps'
    label = _('PPF / EPF / NPS')
    category = 'savings'
    icon = 'bi-piggy-bank'
    form_class = PpfEpfNpsFlowForm
    wizard_steps = [
        FlowWizardStep('scheme_basics', _('Scheme Basics'), ['scheme_type', 'name', 'annual_amount', 'from_account'], _('Choose the scheme and the yearly contribution account.')),
        FlowWizardStep('scheme_terms', _('Scheme Terms'), ['deposit_principal', 'deposit_rate', 'deposit_start_date', 'deposit_compounding', 'deposit_maturity_date', 'deposit_closed_date', 'show_accrued_balance', 'record_maturity_income'], _('Settings used for balance tracking and maturity handling.')),
    ]

    def plan(self, data) -> list[CreateStep]:
        account = CreateStep(
            Account,
            {
                'user': data['user'],
                'name': data['name'],
                'account_type': data['scheme_type'],
                'balance': Decimal('0.00'),
                'currency': data['currency'],
                'deposit_principal': data['deposit_principal'],
                'deposit_rate': data.get('deposit_rate'),
                'deposit_start_date': data['deposit_start_date'],
                'deposit_compounding': data['deposit_compounding'],
                'deposit_maturity_date': data.get('deposit_maturity_date'),
                'deposit_closed_date': data.get('deposit_closed_date'),
                'show_accrued_balance': data['show_accrued_balance'],
                'record_maturity_income': data['record_maturity_income'],
            },
            key='account',
        )
        recurring = CreateStep(
            RecurringTransaction,
            {
                'user': data['user'],
                'transaction_type': 'TRANSFER',
                'amount': data['annual_amount'],
                'currency': data['currency'],
                'from_account': data['from_account'],
                'to_account': '$account',
                'frequency': 'YEARLY',
                'start_date': data['deposit_start_date'],
                'last_processed_date': RecurringService.last_due_before_today(data['deposit_start_date'], 'YEARLY'),
                'description': _('Annual investment contribution: %(name)s') % {'name': data['name']},
                'is_active': True,
            },
            key='transfer',
        )
        return [account, recurring]

    def derive(self, cleaned_data) -> dict:
        data = dict(cleaned_data)
        data['annual_amount'] = Decimal(str(data.get('annual_amount') or 0))
        data['currency'] = data['from_account'].currency
        data['deposit_start_date'] = data.get('deposit_start_date') or timezone.localdate()
        return data

    def preview(self, user, cleaned_data) -> dict:
        data = self.derive(cleaned_data)
        return {'headline': float(data['annual_amount']), 'bullets': [_('Creates a retirement contribution schedule')], 'warnings': []}

        account_type = 'MUTUAL_FUND' if data['instrument_type'] == 'SIP' else 'RD'
        if data['instrument_type'] == 'RD':
            investment_account = CreateStep(
                Account,
                {
                    'user': data['user'],
                    'name': data['name'],
                    'account_type': account_type,
                    'balance': Decimal('0.00'),
                    'currency': data['currency'],
                    'deposit_principal': data['deposit_principal'],
                    'deposit_rate': data['deposit_rate'],
                    'deposit_start_date': data['deposit_start_date'],
                    'deposit_compounding': data['deposit_compounding'],
                    'deposit_maturity_date': data.get('deposit_maturity_date'),
                    'show_accrued_balance': data['show_accrued_balance'],
                    'record_maturity_income': data['record_maturity_income'],
                    'rd_installment_amount': data['amount'],
                    'rd_installment_day': data['rd_installment_day'],
                },
                key='account',
            )
            recurring_start = data['deposit_start_date']
        else:
            investment_account = CreateStep(
                Account,
                {
                    'user': data['user'],
                    'name': data['name'],
                    'account_type': account_type,
                    'balance': Decimal('0.00'),
                    'currency': data['currency'],
                },
                key='account',
            )
            recurring_start = data.get('deposit_start_date') or data['from_account'].created_at.date()

        recurring = CreateStep(
            RecurringTransaction,
            {
                'user': data['user'],
                'transaction_type': 'TRANSFER',
                'amount': data['amount'],
                'currency': data['currency'],
                'from_account': data['from_account'],
                'to_account': '$account',
                'frequency': data['frequency'],
                'start_date': recurring_start,
                'last_processed_date': RecurringService.last_due_before_today(recurring_start, data['frequency']),
                'description': _('Investment contribution: %(name)s') % {'name': data['name']},
                'is_active': True,
            },
            key='transfer',
        )
        return [investment_account, recurring]

    def derive(self, cleaned_data) -> dict:
        data = dict(cleaned_data)
        data['amount'] = Decimal(str(data.get('amount') or 0))
        data['currency'] = data.get('currency') or data['from_account'].currency
        data['deposit_start_date'] = data.get('deposit_start_date') or data['from_account'].created_at.date()
        return data

    def preview(self, user, cleaned_data) -> dict:
        data = self.derive(cleaned_data)
        return {'headline': float(data['amount']), 'bullets': [_('Creates an investment account and contribution schedule')], 'warnings': []}


@register_flow
class FdFlow(Flow):
    key = 'fd'
    label = _('Fixed Deposit')
    category = 'savings'
    icon = 'bi-bank'
    form_class = FdFlowForm

    def plan(self, data) -> list[CreateStep]:
        account = CreateStep(
            Account,
            {
                'user': data['user'],
                'name': data['name'],
                'account_type': 'FD',
                'balance': Decimal('0.00'),
                'currency': data['currency'],
                'deposit_principal': data['principal'],
                'deposit_rate': data['annual_rate'],
                'deposit_start_date': data['deposit_start_date'],
                'deposit_compounding': data['deposit_compounding'],
                'deposit_maturity_date': data['maturity_date'],
                'deposit_closed_date': data.get('deposit_closed_date'),
                'show_accrued_balance': data['show_accrued_balance'],
                'record_maturity_income': data['record_maturity_income'],
            },
            key='account',
        )
        capital = CreateStep(
            CapitalEvent,
            {
                'user': data['user'],
                'amount': data['principal'],
                'date': data['maturity_date'],
                'subtype': 'investment_lump_sum',
                'note': _('FD investment'),
                'account': data['from_account'],
                'currency': data['currency'],
            },
            key='capital',
        )
        return [account, capital]

    def derive(self, cleaned_data) -> dict:
        data = dict(cleaned_data)
        data['principal'] = Decimal(str(data.get('principal') or 0))
        data['annual_rate'] = Decimal(str(data.get('annual_rate') or 0))
        data['currency'] = data['from_account'].currency
        return data

    def preview(self, user, cleaned_data) -> dict:
        data = self.derive(cleaned_data)
        days = max((data['maturity_date'] - data['deposit_start_date']).days, 1)
        headline = float(data['principal'] + (data['principal'] * data['annual_rate'] * Decimal(days) / Decimal('36500')))
        return {'headline': headline, 'bullets': [_('Creates a lump-sum fixed deposit')], 'warnings': []}


@register_flow
class PpfEpfNpsFlow(Flow):
    key = 'ppfepfnps'
    label = _('PPF / EPF / NPS')
    category = 'savings'
    icon = 'bi-piggy-bank'
    form_class = PpfEpfNpsFlowForm

    def plan(self, data) -> list[CreateStep]:
        account = CreateStep(
            Account,
            {
                'user': data['user'],
                'name': data['name'],
                'account_type': data['scheme_type'],
                'balance': Decimal('0.00'),
                'currency': data['currency'],
                'deposit_principal': data['deposit_principal'],
                'deposit_rate': data.get('deposit_rate'),
                'deposit_start_date': data['deposit_start_date'],
                'deposit_compounding': data['deposit_compounding'],
                'deposit_maturity_date': data.get('deposit_maturity_date'),
                'deposit_closed_date': data.get('deposit_closed_date'),
                'show_accrued_balance': data['show_accrued_balance'],
                'record_maturity_income': data['record_maturity_income'],
            },
            key='account',
        )
        recurring = CreateStep(
            RecurringTransaction,
            {
                'user': data['user'],
                'transaction_type': 'TRANSFER',
                'amount': data['annual_amount'],
                'currency': data['currency'],
                'from_account': data['from_account'],
                'to_account': '$account',
                'frequency': 'YEARLY',
                'start_date': data['deposit_start_date'],
                'last_processed_date': RecurringService.last_due_before_today(data['deposit_start_date'], 'YEARLY'),
                'description': _('Annual investment contribution: %(name)s') % {'name': data['name']},
                'is_active': True,
            },
            key='transfer',
        )
        return [account, recurring]

    def derive(self, cleaned_data) -> dict:
        data = dict(cleaned_data)
        data['annual_amount'] = Decimal(str(data.get('annual_amount') or 0))
        data['currency'] = data['from_account'].currency
        data['deposit_start_date'] = data.get('deposit_start_date') or timezone.localdate()
        return data

    def preview(self, user, cleaned_data) -> dict:
        data = self.derive(cleaned_data)
        return {'headline': float(data['annual_amount']), 'bullets': [_('Creates a retirement contribution schedule')], 'warnings': []}

