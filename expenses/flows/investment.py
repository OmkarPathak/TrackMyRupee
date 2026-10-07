from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
import math


from django import forms
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from ..models import Account, CapitalEvent, Holding, RecurringTransaction
from ..services_recurring import RecurringService
from .base import CreateStep, Flow, FlowSnapshot, FlowWizardStep
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
        initial='MONTHLY',
        widget=forms.Select(attrs={'class': 'form-select'}),
        help_text=_('How often the contribution should repeat.'),
    )
    deposit_start_date = forms.DateField(
        required=False,
        label=_('Start date'),
        widget=forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
        help_text=_('Date the contribution or deposit plan begins.'),
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
    is_pinned = forms.BooleanField(
        required=False,
        initial=False,
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        help_text=_('Pin this investment account.'),
    )
    deposit_closed_date = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
        help_text=_('Optional actual close date if the deposit is closed early.'),
    )
    end_date = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
        help_text=_('Optional end date after which recurring contributions stop.'),
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
    is_pinned = forms.BooleanField(
        required=False,
        initial=False,
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        help_text=_('Pin this deposit account.'),
    )
    deposit_closed_date = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
        help_text=_('Optional actual close date if the deposit is broken early.'),
    )
    custom_note = forms.CharField(
        max_length=255,
        required=False,
        widget=forms.TextInput(attrs={'class': 'form-control'}),
        help_text=_('Optional custom note for the deposit investment.'),
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
        min_value=Decimal('0.00'),
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
    end_date = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
        help_text=_('Optional end date after which annual contributions stop.'),
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
    is_pinned = forms.BooleanField(
        required=False,
        initial=False,
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        help_text=_('Pin this scheme account.'),
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


from django.urls import reverse


@register_flow
class SipRdFlow(Flow):
    key = 'sip'
    label = _('SIP / RD')
    title = _('I started a SIP')
    description = _('Link it to a fund and a monthly debit date.')
    category = 'savings'
    icon = 'bi-graph-up'
    tags = [_('Investment'), _('Monthly SIP')]
    estimated_time = _('About 1 min')
    creates = [
        _('Investment account'),
        _('Recurring monthly transfer'),
    ]
    limit_map = {
        'accounts': Account,
        'recurring_transactions': RecurringTransaction,
    }
    form_class = SipRdFlowForm
    wizard_steps = [
        FlowWizardStep('investment_basics', _('Investment Basics'), ['instrument_type', 'name', 'amount', 'frequency', 'deposit_start_date', 'end_date', 'from_account', 'is_pinned'], _('Tell us what you are investing in and how often it should repeat.')),
        FlowWizardStep('deposit_rd_details', _('Recurring Deposit Details'), ['deposit_principal', 'deposit_rate', 'deposit_compounding', 'deposit_maturity_date', 'deposit_closed_date', 'rd_installment_day', 'show_accrued_balance', 'record_maturity_income'], _('Configure interest rate and maturity terms for your recurring deposit.'), show_if="instrument_type === 'RD'"),
    ]

    def is_configured(self, user, snapshot: FlowSnapshot | None = None) -> bool:
        if snapshot is None:
            snapshot = FlowSnapshot.for_user(user)
        return bool(snapshot.account_types & {'MUTUAL_FUND', 'RD'})

    def get_edit_url(self, user) -> str:
        return reverse('account-list')

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
                    'deposit_closed_date': data.get('deposit_closed_date'),
                    'show_accrued_balance': data['show_accrued_balance'],
                    'record_maturity_income': data['record_maturity_income'],
                    'rd_installment_amount': data['amount'],
                    'rd_installment_day': data['rd_installment_day'],
                    'is_pinned': bool(data.get('is_pinned', False)),
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
                    'is_pinned': bool(data.get('is_pinned', False)),
                },
                key='account',
            )
            recurring_start = data.get('deposit_start_date') or timezone.localdate()

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
                'end_date': data.get('end_date'),
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
        data['currency'] = data.get('currency') or (data['from_account'].currency if data.get('from_account') else '₹')
        data['deposit_start_date'] = data.get('deposit_start_date') or timezone.localdate()
        return data

    def preview(self, user, cleaned_data) -> dict:
        data = self.derive({**cleaned_data, 'user': user})
        steps = self.plan(data)
        warnings = self.check_limits(user, steps)
        freq = str(data.get('frequency') or 'MONTHLY').upper()
        freq_label = _('every month') if freq == 'MONTHLY' else freq.lower()
        start_date = data.get('deposit_start_date')
        date_str = start_date.strftime('%d %b %Y') if hasattr(start_date, 'strftime') else str(start_date or '')
        from_acc = data.get('from_account')
        from_acc_name = from_acc.name if hasattr(from_acc, 'name') else str(from_acc or '')
        summary = str(_("%(type)s into %(name)s from %(acc)s, starting %(date)s.") % {
            'type': data.get('instrument_type', 'SIP'),
            'name': data.get('name', 'Mutual Funds'),
            'acc': from_acc_name,
            'date': date_str,
        }) if from_acc_name and date_str else ''

        return {
            'headline': float(data['amount']),
            'headline_suffix': freq_label,
            'summary': summary,
            'bullets': [_('Creates an investment account and contribution schedule')],
            'warnings': warnings,
        }


@register_flow
class FdFlow(Flow):
    key = 'fd'
    label = _('Fixed Deposit')
    title = _('I booked an FD')
    description = _('Principal, rate and maturity, tracked till it matures.')
    category = 'savings'
    icon = 'bi-bank'
    tags = [_('Deposit'), _('Maturity date')]
    estimated_time = _('About 1 min')
    creates = [
        _('Fixed deposit account'),
        _('One-time funding transfer'),
        _('Maturity tracking'),
    ]
    limit_map = {'accounts': Account}
    form_class = FdFlowForm
    wizard_steps = [
        FlowWizardStep('fd_basics', _('Deposit Basics'), ['name', 'principal', 'annual_rate', 'from_account', 'custom_note'], _('Core deposit details and funding account.')),
        FlowWizardStep('fd_terms', _('Deposit Terms'), ['deposit_start_date', 'maturity_date', 'deposit_compounding', 'deposit_closed_date'], _('When the deposit begins, ends, and how it compounds.')),
        FlowWizardStep('fd_reporting', _('Reporting'), ['show_accrued_balance', 'record_maturity_income', 'is_pinned'], _('Controls whether users see accruals and whether maturity interest is auto-recorded.')),
    ]

    def is_configured(self, user, snapshot: FlowSnapshot | None = None) -> bool:
        if snapshot is None:
            snapshot = FlowSnapshot.for_user(user)
        return 'FD' in snapshot.account_types

    def get_edit_url(self, user) -> str:
        return reverse('account-list')

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
                'is_pinned': bool(data.get('is_pinned', False)),
            },
            key='account',
        )
        note = data.get('custom_note') or _('FD investment')
        capital = CreateStep(
            CapitalEvent,
            {
                'user': data['user'],
                'amount': data['principal'],
                'date': data['deposit_start_date'],
                'subtype': 'investment_lump_sum',
                'note': note,
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
        data['name'] = data.get('name') or str(_('Fixed Deposit'))
        data['deposit_start_date'] = data.get('deposit_start_date') or date.today()
        data['maturity_date'] = data.get('maturity_date') or (data['deposit_start_date'] + timedelta(days=365))
        data['deposit_compounding'] = data.get('deposit_compounding') or 'QUARTERLY'
        data['show_accrued_balance'] = bool(data.get('show_accrued_balance', True))
        data['record_maturity_income'] = bool(data.get('record_maturity_income', False))
        user = data.get('user')
        data['currency'] = data.get('currency') or (data['from_account'].currency if data.get('from_account') else (user.profile.currency if user and hasattr(user, 'profile') else '₹'))
        return data


    def preview(self, user, cleaned_data) -> dict:
        data = self.derive({**cleaned_data, 'user': user})
        steps = self.plan(data)
        warnings = self.check_limits(user, steps)
        days = max((data['maturity_date'] - data['deposit_start_date']).days, 1)
        interest = RecurringService.calculate_interest_for_days(data['principal'], data['annual_rate'], days)
        headline = float(data['principal'] + interest)
        return {'headline': headline, 'bullets': [_('Creates a lump-sum fixed deposit')], 'warnings': warnings}


@register_flow
class PpfEpfNpsFlow(Flow):
    key = 'ppfepfnps'
    label = _('PPF / EPF / NPS')
    title = _('I contribute to PPF / EPF / NPS')
    description = _('Yearly or monthly contributions to your retirement scheme.')
    category = 'savings'
    icon = 'bi-piggy-bank'
    tags = [_('Retirement'), _('Yearly contribution')]
    estimated_time = _('About 1 min')
    creates = [
        _('Scheme account'),
        _('Recurring contribution transfer'),
    ]
    limit_map = {
        'accounts': Account,
        'recurring_transactions': RecurringTransaction,
    }
    form_class = PpfEpfNpsFlowForm
    wizard_steps = [
        FlowWizardStep('scheme_basics', _('Scheme Basics'), ['scheme_type', 'name', 'annual_amount', 'end_date', 'from_account'], _('Choose the scheme and the yearly contribution account.')),
        FlowWizardStep('scheme_terms', _('Scheme Terms'), ['deposit_principal', 'deposit_rate', 'deposit_start_date', 'deposit_compounding', 'deposit_maturity_date', 'deposit_closed_date', 'show_accrued_balance', 'record_maturity_income', 'is_pinned'], _('Settings used for balance tracking and maturity handling.')),
    ]

    def is_configured(self, user, snapshot: FlowSnapshot | None = None) -> bool:
        if snapshot is None:
            snapshot = FlowSnapshot.for_user(user)
        return bool(snapshot.account_types & {'PPF', 'EPF', 'NPS'})

    def get_edit_url(self, user) -> str:
        return reverse('account-list')

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
                'is_pinned': bool(data.get('is_pinned', False)),
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
                'end_date': data.get('end_date'),
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
        data['name'] = data.get('name') or str(_('Government Scheme'))
        data['scheme_type'] = data.get('scheme_type') or 'PPF'
        data['deposit_principal'] = Decimal(str(data.get('deposit_principal') or data['annual_amount']))
        data['deposit_rate'] = Decimal(str(data.get('deposit_rate') or '7.1'))
        data['deposit_start_date'] = data.get('deposit_start_date') or timezone.localdate()
        data['deposit_compounding'] = data.get('deposit_compounding') or 'QUARTERLY'
        data['deposit_maturity_date'] = data.get('deposit_maturity_date') or (data['deposit_start_date'] + timedelta(days=5475))
        data['deposit_closed_date'] = data.get('deposit_closed_date')
        data['show_accrued_balance'] = bool(data.get('show_accrued_balance', True))
        data['record_maturity_income'] = bool(data.get('record_maturity_income', True))
        user = data.get('user')
        data['currency'] = data.get('currency') or (data['from_account'].currency if data.get('from_account') else (user.profile.currency if user and hasattr(user, 'profile') else '₹'))
        return data


    def preview(self, user, cleaned_data) -> dict:
        data = self.derive({**cleaned_data, 'user': user})
        steps = self.plan(data)
        warnings = self.check_limits(user, steps)
        return {'headline': float(data['annual_amount']), 'bullets': [_('Creates a retirement contribution schedule')], 'warnings': warnings}

