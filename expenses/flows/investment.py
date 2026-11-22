from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
import math


from dateutil.relativedelta import relativedelta
from django import forms
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from ..models import Account, CapitalEvent, Holding, RecurringTransaction
from ..forms import MoneyInput
from ..services_recurring import RecurringService
from .base import CreateStep, Flow, FlowSnapshot, FlowWizardStep, UniqueAccountNameMixin, default_funding_account
from .registry import register_flow


def _validate_deposit_dates(form, cleaned, start_key, maturity_key=None, closed_key=None, end_key=None):
    start = cleaned.get(start_key)
    if not start:
        return
    checks = (
        (maturity_key, True, _('Maturity date must be after the start date.')),
        (closed_key, False, _('Closed date cannot be before the start date.')),
        (end_key, False, _('End date cannot be before the start date.')),
    )
    for key, strict, message in checks:
        value = cleaned.get(key) if key else None
        if value and (value <= start if strict else value < start):
            form.add_error(key, message)


class SipRdFlowForm(UniqueAccountNameMixin, forms.Form):
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
        widget=MoneyInput(attrs={'class': 'form-control', 'step': '0.01'}),
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
        widget=MoneyInput(attrs={'class': 'form-control', 'step': '0.01'}),
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
    create_historical_entries = forms.BooleanField(
        required=False,
        initial=False,
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        help_text=_('If start date is in the past, create past due entries immediately.'),
    )

    # JSON built by the page: [{"name", "scheme_code", "amount"}], the funds each instalment buys (SIP only).
    fund_splits = forms.CharField(required=False, label=_('Funds'), widget=forms.HiddenInput())

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        if user:
            accounts = Account.objects.filter(user=user, is_active=True).order_by('name')
            self.fields['from_account'].queryset = accounts
            if not self.is_bound:           # (a POST does not need the starting value)
                self.fields['from_account'].initial = default_funding_account(accounts)
            if not accounts.exists():
                self.fields['from_account'].help_text = _('No active accounts found. <a href="/accounts/add/" target="_blank" class="fw-semibold text-decoration-underline">Add an account</a>.')

    def _clean_fund_splits(self, cleaned_data):
        """Check the funds now, against an account of the kind the flow will create, so a mistake is shown on the form
        and not after the account exists."""
        from ..holdings_service import HoldingError, parse_fund_splits

        raw, from_account, amount = cleaned_data.get('fund_splits'), cleaned_data.get('from_account'), cleaned_data.get('amount')
        if not raw or not self.user or not from_account or amount is None:
            return
        stand_in = Account(user=self.user, account_type='MUTUAL_FUND', currency=from_account.currency)
        try:
            parse_fund_splits(raw, user=self.user, from_account=from_account, to_account=stand_in, amount=amount)
        except HoldingError as exc:
            self.add_error('fund_splits', str(exc))

    def clean(self):
        cleaned_data = super().clean()
        if cleaned_data.get('instrument_type') == 'SIP':
            self._clean_fund_splits(cleaned_data)
        else:
            cleaned_data['fund_splits'] = ''                 # funds belong to a SIP; an RD buys none
        if cleaned_data.get('instrument_type') == 'RD':
            if cleaned_data.get('deposit_principal') is None:
                self.add_error('deposit_principal', _('Deposit principal is required for recurring deposits.'))
            if cleaned_data.get('deposit_rate') is None:
                self.add_error('deposit_rate', _('Interest rate is required for recurring deposits.'))
            if not cleaned_data.get('deposit_start_date'):
                self.add_error('deposit_start_date', _('Start date is required for recurring deposits.'))
            if not cleaned_data.get('rd_installment_day'):
                self.add_error('rd_installment_day', _('Installment day is required for recurring deposits.'))
        _validate_deposit_dates(self, cleaned_data, 'deposit_start_date', 'deposit_maturity_date', 'deposit_closed_date', 'end_date')
        return cleaned_data


class FdFlowForm(UniqueAccountNameMixin, forms.Form):
    name = forms.CharField(
        widget=forms.TextInput(attrs={'class': 'form-control'}),
        help_text=_('Display name for the fixed deposit account.'),
    )
    principal = forms.DecimalField(
        min_value=Decimal('0.01'),
        max_digits=15,
        decimal_places=2,
        widget=MoneyInput(attrs={'class': 'form-control', 'step': '0.01'}),
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
    interest_payout = forms.ChoiceField(
        required=False,
        choices=[('', _('At maturity (added to the deposit)'))] + list(Account.PAYOUT_CHOICES),
        widget=forms.Select(attrs={'class': 'form-select'}),
        label=_('Interest paid'),
        help_text=_('Choose how often the bank pays the interest out to you. The deposit then stays at its principal.'),
    )
    payout_account = forms.ModelChoiceField(
        queryset=Account.objects.none(),
        required=False,
        widget=forms.Select(attrs={'class': 'form-select'}),
        label=_('Payout goes to'),
        help_text=_('Optional. The account that receives the interest. Leave it empty to use the funding account.'),
    )
    payout_amount = forms.DecimalField(
        required=False,
        min_value=Decimal('0.01'),
        max_digits=15,
        decimal_places=2,
        widget=MoneyInput(attrs={'class': 'form-control', 'step': '0.01', 'inputmode': 'decimal'}),
        label=_('Amount each time'),
        help_text=_('Optional. Leave it empty to work it out from the rate. Enter your bank\'s figure if it differs.'),
    )
    create_historical_entries = forms.BooleanField(
        required=False,
        initial=False,
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        label=_('Record the payouts already received'),
        help_text=_('If the deposit started in the past, also record the payouts that fell due since. They are added to the account that receives the interest, so leave this off if its balance already includes them.'),
    )
    payout_to_rd = forms.BooleanField(
        required=False,
        initial=False,
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        label=_('Put each payout into a recurring deposit (RD)'),
        help_text=_('Each monthly payout is moved on the same day into a new RD, so the interest earns interest too.'),
    )
    rd_name = forms.CharField(
        required=False,
        max_length=100,
        widget=forms.TextInput(attrs={'class': 'form-control'}),
        label=_('RD name'),
        help_text=_('Optional. Leave it empty to name it after the deposit.'),
    )
    rd_rate = forms.DecimalField(
        required=False,
        min_value=Decimal('0.01'),
        max_digits=7,
        decimal_places=2,
        widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
        label=_('RD interest rate (% a year)'),
        help_text=_('The rate your bank gives on the RD.'),
    )
    rd_tenure_months = forms.IntegerField(
        required=False,
        min_value=1,
        max_value=120,
        widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '1'}),
        label=_('RD tenure (months)'),
        help_text=_('How many months the RD runs. Each payout is one instalment.'),
    )
    rd_compounding = forms.ChoiceField(
        required=False,
        choices=Account.COMPOUNDING_CHOICES,
        initial='QUARTERLY',
        widget=forms.Select(attrs={'class': 'form-select'}),
        label=_('RD compounding'),
        help_text=_('Banks compound RD interest every quarter.'),
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
        self.user = user
        if user:
            accounts = Account.objects.filter(user=user, is_active=True).order_by('name')
            self.fields['from_account'].queryset = accounts
            self.fields['payout_account'].queryset = accounts
            if not self.is_bound:           # (a POST does not need the starting value)
                self.fields['from_account'].initial = default_funding_account(accounts)
            if not accounts.exists():
                self.fields['from_account'].help_text = _('No active accounts found. <a href="/accounts/add/" target="_blank" class="fw-semibold text-decoration-underline">Add an account</a>.')


    def clean(self):
        cleaned_data = super().clean()
        _validate_deposit_dates(self, cleaned_data, 'deposit_start_date', 'maturity_date', 'deposit_closed_date')
        if cleaned_data.get('interest_payout'):
            rate = cleaned_data.get('annual_rate')
            if rate is not None and rate <= 0 and not cleaned_data.get('payout_amount'):
                self.add_error('annual_rate', _('Enter the interest rate, or the amount each payout, for a deposit that pays interest out.'))
        else:
            # Without a payout schedule the two payout fields mean nothing.
            cleaned_data['payout_account'] = None
            cleaned_data['payout_amount'] = None
            cleaned_data['create_historical_entries'] = False

        if cleaned_data.get('payout_to_rd') and cleaned_data.get('interest_payout') == 'MONTHLY':
            if not cleaned_data.get('rd_rate'):
                self.add_error('rd_rate', _('Enter the interest rate of the RD.'))
            if not cleaned_data.get('rd_tenure_months'):
                self.add_error('rd_tenure_months', _('Enter how many months the RD runs.'))
            name = (cleaned_data.get('rd_name') or '').strip()
            if name:
                if name.lower() == (cleaned_data.get('name') or '').strip().lower():
                    self.add_error('rd_name', _('Give the RD a different name from the deposit.'))
                elif self.user and Account.objects.filter(user=self.user, name__iexact=name).exists():
                    self.add_error('rd_name', _('You already have an account with this name.'))
        else:
            # The RD is for monthly payouts only: with any other choice its fields mean nothing.
            for field in ('payout_to_rd', 'rd_name', 'rd_rate', 'rd_tenure_months'):
                cleaned_data[field] = None if field != 'payout_to_rd' else False
        return cleaned_data


class PpfEpfNpsFlowForm(UniqueAccountNameMixin, forms.Form):
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
        widget=MoneyInput(attrs={'class': 'form-control', 'step': '0.01'}),
        help_text=_('The recurring annual contribution amount.'),
    )
    deposit_principal = forms.DecimalField(
        min_value=Decimal('0.00'),
        max_digits=15,
        decimal_places=2,
        widget=MoneyInput(attrs={'class': 'form-control', 'step': '0.01'}),
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
    create_historical_entries = forms.BooleanField(
        required=False,
        initial=False,
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        help_text=_('If start date is in the past, create past due entries immediately.'),
    )

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        if user:
            accounts = Account.objects.filter(user=user, is_active=True).order_by('name')
            self.fields['from_account'].queryset = accounts
            if not self.is_bound:           # (a POST does not need the starting value)
                self.fields['from_account'].initial = default_funding_account(accounts)
            if not accounts.exists():
                self.fields['from_account'].help_text = _('No active accounts found. <a href="/accounts/add/" target="_blank" class="fw-semibold text-decoration-underline">Add an account</a>.')


    def clean(self):
        cleaned_data = super().clean()
        _validate_deposit_dates(self, cleaned_data, 'deposit_start_date', 'deposit_maturity_date', 'deposit_closed_date', 'end_date')
        return cleaned_data


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
        FlowWizardStep('investment_basics', _('Investment Basics'), ['instrument_type', 'name', 'amount', 'frequency', 'deposit_start_date', 'end_date', 'from_account', 'create_historical_entries', 'is_pinned'], _('Tell us what you are investing in and how often it should repeat.')),
        FlowWizardStep('sip_funds', _('Funds'), ['fund_splits'], _('Optional. Choose the funds each instalment buys and how much goes to each. Whatever you leave out stays as cash in the account, and you can change this later by editing the schedule.'), show_if="instrument_type === 'SIP'"),
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

        create_historical = data.get('create_historical_entries', False)
        last_processed = None if create_historical else RecurringService.last_due_before_today(recurring_start, data['frequency'])

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
                'last_processed_date': last_processed,
                'description': _('Investment contribution: %(name)s') % {'name': data['name']},
                'is_active': True,
            },
            key='transfer',
        )
        return [investment_account, recurring]

    def derive(self, cleaned_data) -> dict:
        data = dict(cleaned_data)
        data['amount'] = Decimal(str(data.get('amount') or 0))
        user = data.get('user')
        data['currency'] = data.get('currency') or (data['from_account'].currency if data.get('from_account') else (user.profile.currency if user and hasattr(user, 'profile') else '₹'))
        data['deposit_start_date'] = data.get('deposit_start_date') or timezone.localdate()
        return data

    def after_create(self, user, data, created_by_key) -> None:
        """Attach the chosen funds to the new schedule, so every instalment buys them. The account is new, so each
        fund is created empty and gets its first purchase when the first instalment is posted."""
        from django.core.exceptions import ValidationError

        from ..holdings_service import HoldingError, apply_fund_splits, parse_fund_splits

        raw = data.get('fund_splits')
        if data.get('instrument_type') != 'SIP' or not raw:
            return
        try:
            rows = parse_fund_splits(raw, user=user, from_account=data['from_account'],
                                     to_account=created_by_key['account'], amount=data['amount'])
            apply_fund_splits(created_by_key['transfer'], rows)
        except HoldingError as exc:
            raise ValidationError(str(exc)) from exc

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


def payout_schedule_dates(data: dict, frequency: str):
    """(start date, last processed date) of the schedules that follow a deposit's payouts.

    A deposit booked on the 29th, 30th or 31st pays on that day of the month (the 30th or 28th in a short month, then back
    to the 31st). A schedule keeps the day of its start date, so these start on the booking day itself, with that day
    taken as already done; starting on the first payout (a clamped 28th) would pin every later payout to the 28th.
    Any other deposit starts on its first payout, as before. Payouts that fell due before today are recorded only when
    asked for; otherwise the schedule carries on from the next one due."""
    booking, first = data['deposit_start_date'], data['first_payout_date']
    historical = data.get('create_historical_entries')
    if booking.day > 28:
        start = booking
        done = booking if (historical or booking > timezone.localdate()) else RecurringService.last_due_before_today(booking, frequency)
        return start, done
    return first, (None if historical else RecurringService.last_due_before_today(first, frequency))


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
        _('Interest payout schedule (if the bank pays interest out)'),
        _('Recurring deposit funded by each payout (optional)'),
    ]
    limit_map = {'accounts': Account, 'recurring_transactions': RecurringTransaction}
    form_class = FdFlowForm
    wizard_steps = [
        FlowWizardStep('fd_basics', _('Deposit Basics'), ['name', 'principal', 'annual_rate', 'from_account', 'custom_note'], _('Core deposit details and funding account.')),
        FlowWizardStep('fd_terms', _('Deposit Terms'), ['deposit_start_date', 'maturity_date', 'deposit_compounding', 'deposit_closed_date'], _('When the deposit begins, ends, and how it compounds.')),
        FlowWizardStep('fd_payout', _('Interest payout'), ['interest_payout', 'payout_account', 'payout_amount', 'create_historical_entries'], _('Does the bank pay the interest out to you? If so, each payout is recorded as income and the deposit stays at its principal.')),
        FlowWizardStep('fd_rd', _('Recurring deposit'), ['payout_to_rd', 'rd_name', 'rd_rate', 'rd_tenure_months', 'rd_compounding'], _('Optional. Move each monthly payout into a recurring deposit so the interest earns interest too.')),
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
                'deposit_payout': data.get('interest_payout') or None,
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
        steps = [account, capital]
        payout = data.get('interest_payout')
        if payout:
            frequency = RecurringService.PAYOUT_FREQUENCY[payout]
            first = data['first_payout_date']
            payout_start, payout_done = payout_schedule_dates(data, frequency)
            steps.append(CreateStep(
                RecurringTransaction,
                {
                    'user': data['user'],
                    'transaction_type': 'INCOME',
                    'amount': data['payout_amount'],
                    'currency': data['currency'],
                    'account': data['payout_account'],
                    'payout_for': '$account',
                    'source': 'Interest Income',
                    'frequency': frequency,
                    'start_date': payout_start,
                    # (a deposit booked as already closed pays out until that day)
                    'end_date': data.get('deposit_closed_date') or data['maturity_date'],
                    'last_processed_date': payout_done,
                    'description': _('Interest payout: %(name)s') % {'name': data['name']},
                    'is_active': True,
                },
                key='payout',
            ))
            if data.get('payout_to_rd'):
                payout_end = data.get('deposit_closed_date') or data['maturity_date']
                transfer_start, transfer_done = payout_schedule_dates(data, 'MONTHLY')
                steps.append(CreateStep(
                    Account,
                    {
                        'user': data['user'],
                        'name': data['rd_name'],
                        'account_type': 'RD',
                        'balance': Decimal('0.00'),
                        'currency': data['currency'],
                        'deposit_rate': data['rd_rate'],
                        'deposit_start_date': first,
                        'deposit_compounding': data['rd_compounding'],
                        'deposit_maturity_date': data['rd_maturity_date'],
                        'show_accrued_balance': True,
                        'rd_installment_amount': data['payout_amount'],
                        'rd_installment_day': min(first.day, 28),
                    },
                    key='rd',
                ))
                steps.append(CreateStep(
                    RecurringTransaction,
                    {
                        'user': data['user'],
                        'transaction_type': 'TRANSFER',
                        'amount': data['payout_amount'],
                        'currency': data['currency'],
                        'from_account': data['payout_account'],
                        'to_account': '$rd',
                        'payout_for': '$account',
                        'frequency': 'MONTHLY',
                        'start_date': transfer_start,
                        # the last instalment falls before the RD matures, and the funding stops with the payouts
                        'end_date': min(payout_end, data['rd_maturity_date'] - timedelta(days=1)),
                        'last_processed_date': transfer_done,
                        'description': _('Interest into RD: %(name)s') % {'name': data['rd_name']},
                        'is_active': True,
                    },
                    key='rd_transfer',
                ))
        return steps

    def derive(self, cleaned_data) -> dict:
        data = dict(cleaned_data)
        data['principal'] = Decimal(str(data.get('principal') or 0))
        data['annual_rate'] = Decimal(str(data.get('annual_rate') or 0))
        data['name'] = data.get('name') or str(_('Fixed Deposit'))
        data['deposit_start_date'] = data.get('deposit_start_date') or timezone.localdate()
        data['maturity_date'] = data.get('maturity_date') or (data['deposit_start_date'] + timedelta(days=365))
        data['deposit_compounding'] = data.get('deposit_compounding') or 'QUARTERLY'
        data['show_accrued_balance'] = bool(data.get('show_accrued_balance', True))
        data['record_maturity_income'] = bool(data.get('record_maturity_income', False))
        user = data.get('user')
        data['currency'] = data.get('currency') or (data['from_account'].currency if data.get('from_account') else (user.profile.currency if user and hasattr(user, 'profile') else '₹'))
        payout = data.get('interest_payout') or None
        data['interest_payout'] = payout
        data['create_historical_entries'] = bool(payout and data.get('create_historical_entries'))
        if payout:
            data['payout_account'] = data.get('payout_account') or data.get('from_account')
            data['payout_amount'] = (Decimal(str(data['payout_amount'])) if data.get('payout_amount')
                                     else RecurringService.payout_amount(data['principal'], data['annual_rate'], payout))
            data['first_payout_date'] = RecurringService.first_payout_date(data['deposit_start_date'], payout)
        data['payout_to_rd'] = bool(payout == 'MONTHLY' and data.get('payout_to_rd'))
        if data['payout_to_rd']:
            data['rd_name'] = (data.get('rd_name') or '').strip() or str(_('%(name)s interest RD') % {'name': data['name']})[:100]
            data['rd_rate'] = Decimal(str(data.get('rd_rate') or 0))
            data['rd_tenure_months'] = int(data.get('rd_tenure_months') or 1)
            data['rd_compounding'] = data.get('rd_compounding') or 'QUARTERLY'
            data['rd_maturity_date'] = data['first_payout_date'] + relativedelta(months=data['rd_tenure_months'])
        return data


    def preview(self, user, cleaned_data) -> dict:
        data = self.derive({**cleaned_data, 'user': user})
        steps = self.plan(data)
        warnings = self.check_limits(user, steps)
        if data['interest_payout']:
            # The interest goes to another account as it falls due, so the deposit stays at its principal.
            account_name = data['payout_account'].name if data.get('payout_account') else ''
            every = {'MONTHLY': _('every month'), 'QUARTERLY': _('every 3 months'), 'ANNUAL': _('every year')}[data['interest_payout']]
            bullets = [
                _('Creates a lump-sum fixed deposit that stays at its principal'),
                _('Records %(amount)s of interest %(every)s in %(account)s until maturity') % {
                    'amount': f"{data['currency']}{data['payout_amount']:,.2f}", 'every': every, 'account': account_name},
            ]
            if data['payout_to_rd']:
                bullets.append(_('Moves each payout into a new recurring deposit, %(name)s, for %(months)s months') % {
                    'name': data['rd_name'], 'months': data['rd_tenure_months']})
            return {'headline': float(data['principal']), 'bullets': bullets, 'warnings': warnings}
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
        FlowWizardStep('scheme_basics', _('Scheme Basics'), ['scheme_type', 'name', 'annual_amount', 'end_date', 'from_account', 'create_historical_entries'], _('Choose the scheme and the yearly contribution account.')),
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
        create_historical = data.get('create_historical_entries', False)
        last_processed = None if create_historical else RecurringService.last_due_before_today(data['deposit_start_date'], 'YEARLY')

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
                'last_processed_date': last_processed,
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
        principal = data.get('deposit_principal')
        rate = data.get('deposit_rate')
        data['deposit_principal'] = Decimal(str(data['annual_amount'] if principal is None else principal))
        data['deposit_rate'] = Decimal(str('7.1' if rate is None else rate))
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

