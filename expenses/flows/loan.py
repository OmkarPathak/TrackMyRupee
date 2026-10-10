from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from django import forms
from django.core.exceptions import ValidationError
from django.db.models import Sum
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from finance_tracker.plans import get_limit

from ..models import Account, CapitalEvent, Loan, LoanInterestRate, RecurringTransaction
from ..services import LoanService
from ..services_recurring import RecurringService
from .base import CreateStep, Flow, FlowAddon, FlowSnapshot, FlowWizardStep
from .registry import register_flow


class NewLoanFlowForm(forms.Form):
    loan_type = forms.ChoiceField(choices=Loan.LOAN_TYPES, required=False, initial='PERSONAL', widget=forms.Select(attrs={'class': 'form-select'}), help_text=_('Choose the loan category so reports and reminders stay consistent.'))
    repayment_type = forms.ChoiceField(choices=Loan.REPAYMENT_TYPE_CHOICES, initial='EMI', widget=forms.Select(attrs={'class': 'form-select'}), help_text=_('Choose how loan repayments are calculated.'))
    name = forms.CharField(widget=forms.TextInput(attrs={'class': 'form-control'}), help_text=_('A clear label for this loan in dashboards and reports.'))
    principal = forms.DecimalField(min_value=Decimal('0.01'), max_digits=15, decimal_places=2, widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}), help_text=_('The original amount borrowed.'))
    annual_rate = forms.DecimalField(min_value=Decimal('0.00'), max_digits=7, decimal_places=2, widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}), help_text=_('Nominal annual interest rate for EMI estimation.'))
    tenure_months = forms.IntegerField(min_value=1, widget=forms.NumberInput(attrs={'class': 'form-control'}), help_text=_('How long the repayment runs in months.'))
    start_date = forms.DateField(widget=forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}), help_text=_('The date the loan tracking starts.'))
    create_repayment_schedule = forms.BooleanField(required=False, initial=True, widget=forms.CheckboxInput(attrs={'class': 'form-check-input'}), help_text=_('Create a recurring repayment schedule for this loan.'))
    payment_account = forms.ModelChoiceField(queryset=Account.objects.none(), required=False, widget=forms.Select(attrs={'class': 'form-select'}), help_text=_('The account that will be used for loan repayments.'))
    repayment_amount = forms.DecimalField(required=False, min_value=Decimal('0.01'), max_digits=15, decimal_places=2, widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}), help_text=_('Optional override amount per repayment. Leave blank to use calculated EMI.'))
    repayment_frequency = forms.ChoiceField(choices=RecurringTransaction.FREQUENCY_CHOICES, required=False, initial='MONTHLY', widget=forms.Select(attrs={'class': 'form-select'}), help_text=_('How often the repayment should be posted.'))
    repayment_start_date = forms.DateField(required=False, widget=forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}), help_text=_('Optional repayment start date. Defaults to first EMI date or loan start date.'))
    create_historical_entries = forms.BooleanField(required=False, initial=False, widget=forms.CheckboxInput(attrs={'class': 'form-check-input'}), help_text=_('If start date is in the past, create past due entries immediately.'))
    repayment_is_active = forms.BooleanField(required=False, initial=True, widget=forms.CheckboxInput(attrs={'class': 'form-check-input'}), help_text=_('Keep this schedule active for auto-posting.'))
    mid_tenure = forms.BooleanField(required=False, widget=forms.CheckboxInput(attrs={'class': 'form-check-input'}), help_text=_('Enable if this loan already had principal repaid before you start tracking.'))
    opening_paid_principal = forms.DecimalField(required=False, min_value=Decimal('0.00'), max_digits=15, decimal_places=2, widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}), help_text=_('Principal already paid before tracking begins.'))
    first_emi_date = forms.DateField(required=False, widget=forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}), help_text=_('Optional EMI start date if the first repayment is not on the loan start date.'))
    include_down_payment = forms.BooleanField(required=False, widget=forms.CheckboxInput(attrs={'class': 'form-check-input'}), help_text=_('Enable if you made a down payment at purchase time.'))
    down_payment_amount = forms.DecimalField(required=False, min_value=Decimal('0.00'), max_digits=15, decimal_places=2, widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}), help_text=_('Cash paid upfront toward the purchase.'))
    down_payment_account = forms.ModelChoiceField(queryset=Account.objects.none(), required=False, widget=forms.Select(attrs={'class': 'form-select'}), help_text=_('The account from which the down payment was made.'))
    custom_note = forms.CharField(max_length=255, required=False, widget=forms.TextInput(attrs={'class': 'form-control'}), help_text=_('Optional custom note for the down payment event.'))

    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)
        from ..models import Account

        if user:
            accounts = Account.objects.filter(user=user, is_active=True).order_by('name')
            self.fields['payment_account'].queryset = accounts
            self.fields['down_payment_account'].queryset = accounts
            cash_default = accounts.filter(name='Cash').first() or accounts.first()
            self.fields['payment_account'].initial = cash_default
            self.fields['down_payment_account'].initial = cash_default
            if not accounts.exists():
                self.fields['payment_account'].help_text = _('No active accounts found. <a href="/accounts/add/" target="_blank" class="fw-semibold text-decoration-underline">Add an account</a>.')
        else:
            self.fields['payment_account'].queryset = Account.objects.none()
            self.fields['down_payment_account'].queryset = Account.objects.none()

    def clean(self):
        cleaned = super().clean()
        if cleaned.get('mid_tenure') and not cleaned.get('opening_paid_principal'):
            self.add_error('opening_paid_principal', _('Enter the principal already paid before tracking started.'))
        if cleaned.get('include_down_payment') and not cleaned.get('down_payment_amount'):
            self.add_error('down_payment_amount', _('Enter the down payment amount.'))
        if cleaned.get('include_down_payment') and not cleaned.get('down_payment_account'):
            self.add_error('down_payment_account', _('Select the account the down payment was paid from.'))
        principal = cleaned.get('principal')
        opening_paid = cleaned.get('opening_paid_principal')
        if cleaned.get('mid_tenure') and principal is not None and opening_paid and opening_paid >= principal:
            self.add_error('opening_paid_principal', _('Principal already paid must be less than the loan principal.'))
        schedule_enabled = cleaned.get('create_repayment_schedule', True)
        if schedule_enabled and not cleaned.get('payment_account'):
            self.add_error('payment_account', _('Select the account used to pay the loan repayment.'))
        if (
            schedule_enabled
            and not cleaned.get('repayment_amount')
            and principal is not None
            and cleaned.get('annual_rate') is not None
            and cleaned.get('tenure_months')
            and LoanService.calculate_repayment(principal, cleaned['annual_rate'], cleaned['tenure_months'], cleaned.get('repayment_type') or 'EMI') <= 0
        ):
            self.add_error('repayment_amount', _('The calculated repayment is zero. Enter the repayment amount to schedule.'))
        return cleaned


from django.urls import reverse


@register_flow
class NewLoanFlow(Flow):
    key = 'loan'
    label = _('Loan')
    title = _('I took a loan')
    description = _('Home, car or personal. Add it once and EMIs track themselves.')
    category = 'debt'
    icon = 'bi-house'
    tags = [_('Loan'), _('Repayment plan'), _('Monthly EMI')]
    estimated_time = _('About 2 min')
    creates = [
        _('Loan account with balance and rate'),
        _('Repayment schedule'),
        _('Recurring EMI transaction'),
    ]
    limit_map = {
        'loans': Loan,
        'recurring_transactions': RecurringTransaction,
    }
    form_class = NewLoanFlowForm
    addons = {
        'mid_tenure': FlowAddon('mid_tenure', _('Mid-Tenure Entry'), ['opening_paid_principal', 'first_emi_date']),
        'down_payment': FlowAddon('down_payment', _('Down Payment'), ['down_payment_amount', 'down_payment_account', 'custom_note']),
    }
    wizard_steps = [
        FlowWizardStep('loan_basics', _('Loan Basics'), ['loan_type', 'repayment_type', 'name', 'principal', 'annual_rate', 'tenure_months', 'start_date'], _('Core loan details for principal, rate, and tenure.')),
        FlowWizardStep('loan_repayment', _('Repayment Schedule'), ['create_repayment_schedule', 'payment_account', 'repayment_amount', 'repayment_frequency', 'repayment_start_date', 'create_historical_entries', 'repayment_is_active'], _('Control if and how recurring loan repayments should be created.')),
        FlowWizardStep('loan_adjustments', _('Adjustments'), ['mid_tenure', 'opening_paid_principal', 'first_emi_date', 'include_down_payment', 'down_payment_amount', 'down_payment_account', 'custom_note'], _('Optional fields for already-started loans or upfront payments.')),
    ]

    def is_configured(self, user, snapshot: FlowSnapshot | None = None) -> bool:
        if snapshot is None:
            snapshot = FlowSnapshot.for_user(user)
        return snapshot.has_active_loan

    def get_edit_url(self, user) -> str:
        return reverse('loan-list')

    def derive(self, cleaned_data) -> dict:
        data = dict(cleaned_data)
        data['repayment_type'] = data.get('repayment_type') or 'EMI'
        data['annual_rate'] = Decimal(str(data.get('annual_rate') or 0))
        data['principal'] = Decimal(str(data.get('principal') or 0))
        data['tenure_months'] = int(data.get('tenure_months') or 0)
        data['start_date'] = data.get('start_date') or timezone.localdate()
        data['name'] = data.get('name') or _('Loan')
        data['payment_account'] = data.get('payment_account')
        data['create_repayment_schedule'] = data.get('create_repayment_schedule', True)
        if data.get('repayment_amount') is not None:
            data['repayment_amount'] = Decimal(str(data.get('repayment_amount') or 0))
        user = data.get('user')
        data['currency'] = (
            data['payment_account'].currency
            if data.get('payment_account')
            else (getattr(getattr(user, 'profile', None), 'currency', '₹') if user else '₹')
        )
        return data

    def plan(self, data) -> list[CreateStep]:
        start_date = data['start_date']
        repayment_start = data.get('repayment_start_date') or data.get('first_emi_date') or start_date
        repayment_frequency = data.get('repayment_frequency') or 'MONTHLY'
        repayment_type = data.get('repayment_type') or 'EMI'
        emi = Decimal(str(LoanService.calculate_repayment(data['principal'], data['annual_rate'], data['tenure_months'], repayment_type)))
        repayment_amount = data.get('repayment_amount') or emi
        create_historical = data.get('create_historical_entries', False)
        last_processed = None if create_historical else RecurringService.last_due_before_today(repayment_start, repayment_frequency)
        steps = [
            CreateStep(
                Loan,
                {
                    'user': data['user'],
                    'name': data['name'],
                    'loan_type': data.get('loan_type') or 'PERSONAL',
                    'repayment_type': repayment_type,
                    'initial_principal': data['principal'],
                    'opening_paid_principal': data.get('opening_paid_principal') or Decimal('0.00'),
                    'duration_months': data['tenure_months'],
                    'start_date': start_date,
                    'currency': data['currency'],
                },
                key='loan',
            ),
            CreateStep(
                LoanInterestRate,
                {
                    'loan': '$loan',
                    'interest_rate': data['annual_rate'],
                    'effective_date': start_date,
                },
                key='rate',
            ),
            CreateStep(
                RecurringTransaction,
                {
                    'user': data['user'],
                    'transaction_type': 'LOAN',
                    'amount': repayment_amount,
                    'currency': data['currency'],
                    'account': data['payment_account'],
                    'loan': '$loan',
                    'frequency': repayment_frequency,
                    'start_date': repayment_start,
                    'last_processed_date': last_processed,
                    'description': _('Loan EMI: %(name)s') % {'name': data['name']},
                    'is_active': data.get('repayment_is_active', True),
                },
                key='emi',
                condition=data.get('create_repayment_schedule', True),
            ),
        ]

        if data.get('include_down_payment') and data.get('down_payment_amount'):
            default_note = _('Loan down payment for %(name)s') % {'name': data['name']}
            note = data.get('custom_note') or default_note
            steps.append(
                CreateStep(
                    CapitalEvent,
                    {
                        'user': data['user'],
                        'amount': data['down_payment_amount'],
                        'date': start_date,
                        'subtype': 'loan_down_payment',
                        'note': note,
                        'linked_loan': '$loan',
                        'currency': data['currency'],
                        'account': data.get('down_payment_account'),
                    },
                    key='down_payment',
                )
            )

        return steps

    def preview(self, user, cleaned_data) -> dict:
        data = self.derive({**cleaned_data, 'user': user})
        steps = self.plan(data)
        warnings = self.check_limits(user, steps)
        principal = Decimal(str(data.get('principal') or 0))
        annual_rate = Decimal(str(data.get('annual_rate') or 0))
        tenure = int(data.get('tenure_months') or 1)
        repayment_type = data.get('repayment_type') or 'EMI'
        headline = LoanService.calculate_repayment(principal, annual_rate, tenure, repayment_type)
        schedule_enabled = cleaned_data.get('create_repayment_schedule', True)
        return {
            'headline': headline,
            'bullets': [
                _('Creates a loan account and interest rate'),
                _('Creates a recurring EMI schedule') if schedule_enabled else _('Skips recurring schedule creation'),
            ],
            'warnings': warnings,
        }


