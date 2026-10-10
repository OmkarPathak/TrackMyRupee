from __future__ import annotations

from datetime import date
from decimal import Decimal


from django import forms
from django.utils.translation import gettext_lazy as _

from ..models import Account, CURRENCY_CHOICES, RecurringTransaction
from ..services_recurring import RecurringService
from .base import CreateStep, Flow, FlowSnapshot, FlowWizardStep
from .registry import register_flow


class RentBillFlowForm(forms.Form):
    description = forms.CharField(widget=forms.TextInput(attrs={'class': 'form-control'}), help_text=_('Short description that appears on the recurring transaction.'))
    amount = forms.DecimalField(min_value=Decimal('0.01'), max_digits=15, decimal_places=2, widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}), help_text=_('The amount that repeats on each occurrence.'))
    category = forms.CharField(max_length=255, required=False, initial='Rent', widget=forms.TextInput(attrs={'class': 'form-control'}), help_text=_('Category for this bill (e.g. Rent, Utilities).'))
    currency = forms.ChoiceField(choices=CURRENCY_CHOICES, required=False, widget=forms.Select(attrs={'class': 'form-select'}), help_text=_('Currency used for the recurring entry.'))
    frequency = forms.ChoiceField(choices=RecurringTransaction.FREQUENCY_CHOICES, initial='MONTHLY', widget=forms.Select(attrs={'class': 'form-select'}), help_text=_('How often the bill or rent should repeat.'))
    account = forms.ModelChoiceField(queryset=Account.objects.none(), widget=forms.Select(attrs={'class': 'form-select'}), help_text=_('Account used to pay the recurring bill.'))
    start_date = forms.DateField(widget=forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}), help_text=_('Date the recurring schedule should start.'))
    create_historical_entries = forms.BooleanField(required=False, initial=False, widget=forms.CheckboxInput(attrs={'class': 'form-check-input'}), help_text=_('If start date is in the past, create past due entries immediately.'))

    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)
        self.fields['account'].required = False
        if user:
            accounts = Account.objects.filter(user=user, is_active=True).order_by('name')
            self.fields['account'].queryset = accounts
            self.fields['account'].initial = accounts.filter(name='Cash').first() or accounts.first()
            if not accounts.exists():
                self.fields['account'].help_text = _('No active accounts found. <a href="/accounts/add/" target="_blank" class="fw-semibold text-decoration-underline">Add an account</a> or leave blank.')
            self.fields['currency'].initial = user.profile.currency
            from ..models import Category
            from finance_tracker.plans import get_limit
            categories = Category.objects.filter(user=user).order_by('id')
            profile = getattr(user, 'profile', None)
            if profile:
                limit = get_limit(profile.active_tier, 'budget_categories')
                if limit != -1:
                    categories = categories[:limit]
            cat_names = [c.name for c in categories]
            if 'Rent' not in cat_names:
                choices = [('Rent', 'Rent')] + [(c, c) for c in cat_names]
            else:
                choices = [(c, c) for c in cat_names]
            self.fields['category'].widget = forms.Select(choices=choices, attrs={'class': 'form-select'})


from django.urls import reverse


@register_flow
class RentBillFlow(Flow):
    key = 'rentbill'
    label = _('Rent / Bill')
    title = _('I pay rent')
    description = _('A recurring bill with a due date and reminder.')
    category = 'bills'
    icon = 'bi-file-text'
    tags = [_('Bill'), _('Monthly rent')]
    estimated_time = _('About 1 min')
    creates = [
        _('Recurring expense entry'),
        _('Due-date reminder'),
    ]
    limit_map = {'recurring_transactions': RecurringTransaction}
    form_class = RentBillFlowForm
    wizard_steps = [
        FlowWizardStep('bill_basics', _('Bill Details'), ['description', 'amount', 'category', 'currency', 'account'], _('Basic bill information and the payment account.')),
        FlowWizardStep('bill_schedule', _('Schedule'), ['frequency', 'start_date', 'create_historical_entries'], _('How often it should repeat and when it starts.')),
    ]

    def is_configured(self, user, snapshot: FlowSnapshot | None = None) -> bool:
        if snapshot is None:
            snapshot = FlowSnapshot.for_user(user)
        return any(t == 'EXPENSE' for t, _ in snapshot.recurring_signatures)

    def get_edit_url(self, user) -> str:
        return reverse('recurring-list')

    def derive(self, cleaned_data) -> dict:
        data = dict(cleaned_data)
        data['amount'] = Decimal(str(data.get('amount') or 0))
        data['category'] = data.get('category') or 'Rent'
        data['frequency'] = data.get('frequency') or 'MONTHLY'
        data['description'] = data.get('description') or str(_('Rent bill'))
        data['start_date'] = data.get('start_date') or date.today()
        user = data.get('user')
        data['currency'] = data.get('currency') or (data['account'].currency if data.get('account') else (user.profile.currency if user and hasattr(user, 'profile') else '₹'))
        return data


    def plan(self, data) -> list[CreateStep]:
        create_historical = data.get('create_historical_entries', False)
        last_processed = None if create_historical else RecurringService.last_due_before_today(data['start_date'], data['frequency'])
        return [
            CreateStep(
                RecurringTransaction,
                {
                    'user': data['user'],
                    'transaction_type': 'EXPENSE',
                    'amount': data['amount'],
                    'currency': data['currency'],
                    'account': data['account'],
                    'category': data['category'],
                    'description': data['description'],
                    'frequency': data['frequency'],
                    'start_date': data['start_date'],
                    'last_processed_date': last_processed,
                    'is_active': True,
                },
                key='bill',
            )
        ]

    def preview(self, user, cleaned_data) -> dict:
        data = self.derive({**cleaned_data, 'user': user})
        steps = self.plan(data)
        warnings = self.check_limits(user, steps)
        amount = Decimal(str(data.get('amount') or 0))
        frequency = data.get('frequency') or 'MONTHLY'
        multipliers = {'DAILY': 365, 'WEEKLY': 52, 'BIWEEKLY': 26, 'MONTHLY': 12, 'QUARTERLY': 4, 'SEMIANNUALLY': 2, 'YEARLY': 1}
        annual = float(amount) * multipliers.get(frequency, 12)
        return {
            'headline': float(amount),
            'bullets': [
                _('Creates a recurring bill schedule'),
                _('Estimated annual total: %(total)s') % {'total': f"{data['currency']}{annual:,.2f}"},
            ],
            'warnings': warnings,
        }
