import uuid

from datetime import date
from decimal import Decimal

from allauth.socialaccount.models import SocialAccount
from django import forms
from django.conf import settings
from django.contrib.auth.models import User
from django.utils.translation import gettext_lazy as _
from django_recaptcha.fields import ReCaptchaField
from django_recaptcha.widgets import ReCaptchaV3

from finance_tracker.plans import get_limit

from .models import (
    Account,
    CapitalEvent,
    Category,
    Expense,
    GoalContribution,
    Income,
    Loan,
    LoanInterestRate,
    LoanRepayment,
    PhysicalAsset,
    RecurringTransaction,
    SavingsGoal,
    Transfer,
    UserProfile,
)
from .utils import BOOTSTRAP_ICONS


class CachedModelChoiceField(forms.ModelChoiceField):
    """ModelChoiceField that can resolve selected objects from a preloaded map.

    This avoids repeated queryset.get(pk=...) calls during formset validation.
    """

    def __init__(self, *args, object_cache=None, **kwargs):
        self.object_cache = object_cache or {}
        super().__init__(*args, **kwargs)

    def to_python(self, value):
        if value in self.empty_values:
            return None

        try:
            lookup_key = int(value)
        except (TypeError, ValueError):
            lookup_key = value

        obj = self.object_cache.get(lookup_key)
        if obj is not None:
            return obj

        return super().to_python(value)


class SearchableSelectFormMixin:
    """Mixin to automatically ensure all single-select fields have the 'searchable-select' CSS class."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            if isinstance(field.widget, forms.Select) and not isinstance(field.widget, (forms.SelectMultiple, forms.CheckboxSelectMultiple)):
                css_class = field.widget.attrs.get('class', '')
                if 'searchable-select' not in css_class and 'django-multi-select' not in css_class:
                    field.widget.attrs['class'] = f"{css_class} searchable-select".strip()


class ExpenseForm(SearchableSelectFormMixin, forms.ModelForm):
    class Meta:
        model = Expense
        fields = ['date', 'amount', 'currency', 'account', 'description', 'category', 'payment_method', 'client_dedup_key']
        widgets = {
            'date': forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
            'amount': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'currency': forms.Select(attrs={'class': 'form-select'}),
            'account': forms.Select(attrs={'class': 'form-select searchable-select'}),
            'description': forms.TextInput(attrs={'class': 'form-control'}),
            'payment_method': forms.Select(attrs={'class': 'form-select'}),
            'client_dedup_key': forms.HiddenInput(),
        }

    def __init__(self, *args, **kwargs):
        user = kwargs.pop('user', None)
        super().__init__(*args, **kwargs)
        
        if not self.is_bound and not self.instance.pk:
            self.fields['client_dedup_key'].initial = str(uuid.uuid4())
        self.fields['date'].initial = date.today
        
        # If user is provided, populate category choices
        if user:
            self.fields['currency'].initial = user.profile.currency
            self.fields['payment_method'].initial = 'Credit Card'
            profile = user.profile
            active_tier = profile.active_tier

            # Cache category choices for this request/user object to avoid repeated queries
            category_cache = getattr(user, '_expense_form_category_cache', None)
            if category_cache and category_cache.get('tier') == active_tier:
                choices = category_cache.get('choices', [])
            else:
                categories = Category.objects.filter(user=user).order_by('id')
                # Enforce Tier Limits
                limit = get_limit(active_tier, 'budget_categories')
                if limit != -1:
                    categories = categories[:limit]

                choices = [(cat.name, cat.name) for cat in categories]
                user._expense_form_category_cache = {
                    'tier': active_tier,
                    'choices': choices,
                }

            self.fields['category'].widget = forms.Select(choices=choices, attrs={'class': 'form-select django-multi-select'})
            
            # Cache account queryset/default account id to avoid repeated lookups per form init
            account_cache = getattr(user, '_expense_form_account_cache', None)
            if account_cache and account_cache.get('tier') == active_tier:
                accounts_qs = account_cache.get('queryset')
                default_account_id = account_cache.get('default_account_id')
                account_map = account_cache.get('account_map', {})
            else:
                all_accounts = Account.objects.filter(user=user, is_active=True).order_by('created_at', 'id')
                limit = get_limit(active_tier, 'accounts')
                if limit != -1:
                    unlocked_ids = list(all_accounts.values_list('id', flat=True)[:limit])
                    accounts_qs = all_accounts.filter(id__in=unlocked_ids)
                else:
                    accounts_qs = all_accounts

                account_map = {acc.id: acc for acc in accounts_qs}
                default_account_id = accounts_qs.filter(name='Cash').values_list('id', flat=True).first()
                user._expense_form_account_cache = {
                    'tier': active_tier,
                    'queryset': accounts_qs,
                    'default_account_id': default_account_id,
                    'account_map': account_map,
                }

            existing_field = self.fields['account']
            self.fields['account'] = CachedModelChoiceField(
                queryset=accounts_qs,
                object_cache=account_map,
                required=existing_field.required,
                label=existing_field.label,
                help_text=existing_field.help_text,
                widget=existing_field.widget,
            )

            # Default to the first account (likely 'Cash')
            if default_account_id:
                self.fields['account'].initial = default_account_id
        else:
            self.fields['category'].widget = forms.TextInput(attrs={'class': 'form-control'})
            self.fields['account'].queryset = Account.objects.none()

    def clean_category(self):
        category = self.cleaned_data.get('category')
        if category:
            return category.strip()
        return category

class IncomeForm(SearchableSelectFormMixin, forms.ModelForm):
    class Meta:
        model = Income
        fields = ['date', 'amount', 'currency', 'account', 'source_type', 'description', 'client_dedup_key']
        widgets = {
            'date': forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
            'amount': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'currency': forms.Select(attrs={'class': 'form-select'}),
            'account': forms.Select(attrs={'class': 'form-select searchable-select'}),
            'source_type': forms.Select(attrs={'class': 'form-select'}),
            'description': forms.Textarea(attrs={'class': 'form-control', 'rows': 2, 'placeholder': _('e.g. Salary from company, Freelance project details')}),
            'client_dedup_key': forms.HiddenInput(),
        }
    
    add_to_recurring = forms.BooleanField(required=False, label=_("Make this a recurring income"))
    frequency = forms.ChoiceField(
        choices=RecurringTransaction.FREQUENCY_CHOICES,
        required=False,
        label=_("Frequency"),
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    
    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop('user', None)
        super().__init__(*args, **kwargs)

        if not self.is_bound and not self.instance.pk:
            self.fields['client_dedup_key'].initial = str(uuid.uuid4())
        self.fields['date'].initial = date.today
        self.fields['source_type'].required = True
        self.fields['source_type'].label = _("Source Type")
        self.fields['description'].required = False
        self.fields['description'].label = _("Description")
        if self.user:
            self.fields['currency'].initial = self.user.profile.currency
            
            # Enforce Tier Limits for Accounts
            all_accounts = Account.objects.filter(user=self.user, is_active=True).order_by('created_at', 'id')
            limit = get_limit(self.user.profile.active_tier, 'accounts')
            if limit != -1:
                unlocked_ids = all_accounts.values_list('id', flat=True)[:limit]
                self.fields['account'].queryset = all_accounts.filter(id__in=unlocked_ids)
            else:
                self.fields['account'].queryset = all_accounts

            default_account = self.fields['account'].queryset.filter(name='Cash').first()
            if default_account:
                self.fields['account'].initial = default_account
        else:
            self.fields['account'].queryset = Account.objects.none()
        
    def clean_source(self):
        source = self.cleaned_data.get('source')
        if source:
            return source.strip()
        return source or ""

class RecurringTransactionForm(SearchableSelectFormMixin, forms.ModelForm):
    class Meta:
        model = RecurringTransaction
        fields = ['transaction_type', 'amount', 'currency', 'account', 'category', 'source',
                  'loan',
                  'from_account', 'to_account',
                  'frequency', 'start_date', 'end_date', 'description', 'is_active', 'payment_method',
                  'capital_subtype', 'exclude_from_averages', 'exclude_from_budget', 'include_in_net_worth']
        widgets = {
            'transaction_type': forms.Select(attrs={'class': 'form-select', 'onchange': 'toggleFields()'}),
            'amount': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'currency': forms.Select(attrs={'class': 'form-select'}),
            'account': forms.Select(attrs={'class': 'form-select searchable-select'}),
            'loan': forms.Select(attrs={'class': 'form-select'}),
            'category': forms.Select(attrs={'class': 'form-select'}),
            'source': forms.TextInput(attrs={'class': 'form-control', 'placeholder': _('e.g. Salary, Rent')}),
            'from_account': forms.Select(attrs={'class': 'form-select searchable-select'}),
            'to_account': forms.Select(attrs={'class': 'form-select searchable-select'}),
            'frequency': forms.Select(attrs={'class': 'form-select'}),
            'start_date': forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
            'end_date': forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
            'description': forms.Textarea(attrs={'class': 'form-control', 'rows': 2}),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'payment_method': forms.Select(attrs={'class': 'form-select'}),
            'capital_subtype': forms.Select(attrs={'class': 'form-select'}),
            'exclude_from_averages': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'exclude_from_budget': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'include_in_net_worth': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }

    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop('user', None)
        super().__init__(*args, **kwargs)
        user = self.user

        allowed_types = [
            ('EXPENSE', _('Expense')),
            ('INCOME', _('Income')),
            ('TRANSFER', _('Transfer')),
            ('LOAN', _('Loan Repayment')),
            ('CAPITAL', _('Capital Event')),
        ]
        self.fields['transaction_type'].choices = allowed_types
        if self.instance and self.instance.pk and self.instance.transaction_type == 'LOAN':
            self.fields['transaction_type'].disabled = True

        self.fields['capital_subtype'].choices = [('', '---------')] + CapitalEvent.SUBTYPE_CHOICES
        self.fields['capital_subtype'].required = False

        if user:
            self.fields['currency'].initial = user.profile.currency
            
            # Enforce Tier Limits for Accounts
            all_accounts = Account.objects.filter(user=user, is_active=True).order_by('created_at', 'id')
            limit = get_limit(user.profile.active_tier, 'accounts')
            if limit != -1:
                unlocked_ids = all_accounts.values_list('id', flat=True)[:limit]
                accounts_qs = all_accounts.filter(id__in=unlocked_ids)
            else:
                accounts_qs = all_accounts

            self.fields['account'].queryset = accounts_qs
            self.fields['from_account'].queryset = accounts_qs
            self.fields['to_account'].queryset = accounts_qs
            self.fields['loan'].queryset = Loan.objects.filter(user=user, is_active=True).order_by('-created_at')
        else:
            self.fields['account'].queryset = Account.objects.none()
            self.fields['from_account'].queryset = Account.objects.none()
            self.fields['to_account'].queryset = Account.objects.none()
            self.fields['loan'].queryset = Loan.objects.none()
        
        # Category field as Select for Expenses
        if user:
            categories = Category.objects.filter(user=user).order_by('id')
            
            # Enforce Tier Limits
            profile = user.profile
            limit = get_limit(profile.active_tier, 'budget_categories')
            if limit != -1:
                categories = categories[:limit]

            category_choices = [('', '---------')] + [(cat.name, cat.name) for cat in categories]
            self.fields['category'].widget = forms.Select(choices=category_choices, attrs={'class': 'form-select'})
        else:
            self.fields['category'].widget = forms.TextInput(attrs={'class': 'form-control'})
        
        self.fields['source'].widget = forms.TextInput(attrs={'class': 'form-control', 'placeholder': _('e.g. Salary (For Income only)')})
        
        # Ensure fields are optional at form level since we handle them in clean()
        self.fields['category'].required = False
        self.fields['source'].required = False
        self.fields['from_account'].required = False
        self.fields['to_account'].required = False
        self.fields['loan'].required = False

    def clean(self):
        cleaned_data = super().clean()
        if self.instance and self.instance.pk and self.instance.transaction_type == 'LOAN':
            cleaned_data['transaction_type'] = 'LOAN'
        transaction_type = cleaned_data.get('transaction_type')
        category = cleaned_data.get('category')
        source = cleaned_data.get('source')
        loan = cleaned_data.get('loan')

        if transaction_type == 'EXPENSE' and not category:
            self.add_error('category', _('Category is required for expenses.'))
        
        if transaction_type == 'INCOME' and not source:
            self.add_error('source', _('Source is required for income.'))

        if transaction_type == 'TRANSFER':
            from_account = cleaned_data.get('from_account')
            to_account = cleaned_data.get('to_account')
            if not from_account:
                self.add_error('from_account', _('From account is required for transfers.'))
            if not to_account:
                self.add_error('to_account', _('To account is required for transfers.'))
            if from_account and to_account and from_account == to_account:
                self.add_error('to_account', _('Source and destination accounts must be different.'))

        if transaction_type == 'LOAN':
            account = cleaned_data.get('account')
            if not loan:
                self.add_error('loan', _('Loan is required for recurring loan repayments.'))
            if not account:
                self.add_error('account', _('Account is required for recurring loan repayments.'))

        if transaction_type == 'CAPITAL':
            capital_subtype = cleaned_data.get('capital_subtype')
            if not capital_subtype:
                self.add_error('capital_subtype', _('Subtype is required for capital events.'))

        start_date = cleaned_data.get('start_date')
        end_date = cleaned_data.get('end_date')
        if start_date and end_date and end_date < start_date:
            self.add_error('end_date', _('End date must be after or equal to start date.'))

        user = getattr(self, 'user', None) or (self.instance.user if self.instance else None)
        is_active = cleaned_data.get('is_active', False)
        if user and is_active and transaction_type:
            amount = cleaned_data.get('amount')
            currency = cleaned_data.get('currency')
            description = cleaned_data.get('description', '')
            frequency = cleaned_data.get('frequency')
            
            qs = RecurringTransaction.objects.filter(
                user=user,
                transaction_type=transaction_type,
                amount=amount,
                currency=currency,
                description=description,
                frequency=frequency,
                start_date=start_date,
                is_active=True
            )
            if self.instance and self.instance.pk:
                qs = qs.exclude(pk=self.instance.pk)
            if qs.exists():
                self.add_error(None, _('An active recurring transaction with these exact details already exists.'))

        return cleaned_data

class ProfileUpdateForm(SearchableSelectFormMixin, forms.ModelForm):
    SALARY_DATE_CHOICES = [(i, str(i)) for i in range(1, 32)]
    
    auth_email = forms.EmailField(required=True, label='Email Address')
    first_name = forms.CharField(required=False, widget=forms.TextInput(attrs={'class': 'form-control'}))
    last_name = forms.CharField(required=False, widget=forms.TextInput(attrs={'class': 'form-control'}))
    daily_reminder = forms.BooleanField(required=False, label=_('Daily Expense Reminder'), widget=forms.CheckboxInput(attrs={'class': 'form-check-input'}))
    salary_date = forms.ChoiceField(
        choices=SALARY_DATE_CHOICES,
        widget=forms.Select(attrs={'class': 'form-select'}),
        label=_('Salary Date'),
        required=True
    )

    class Meta:
        model = User
        fields = ['first_name', 'last_name']
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['auth_email'].initial = self.instance.email
        self.fields['daily_reminder'].initial = self.instance.profile.daily_reminder
        self.fields['salary_date'].initial = self.instance.profile.salary_date
        self.fields['auth_email'].widget.attrs.update({'class': 'form-control'})

        # Check if user has social account
        if SocialAccount.objects.filter(user=self.instance).exists():
            for field in ['first_name', 'last_name', 'auth_email']:
                self.fields[field].disabled = True
                self.fields[field].widget.attrs['disabled'] = 'disabled'
                self.fields[field].required = False
            self.fields['auth_email'].help_text = "Managed by social login. You cannot change this info."

    def clean_auth_email(self):
        email = self.cleaned_data.get('auth_email')
        
        # If the email hasn't changed, allow it (even if duplicates exist in DB)
        if email == self.instance.email:
            return email
            
        if User.objects.filter(email=email).exclude(id=self.instance.id).exists():
            raise forms.ValidationError("Email already assigned to another account.")
        return email

    def save(self, commit=True):
        user = super().save(commit=False)
        user.email = self.cleaned_data['auth_email']
        if commit:
            user.save()
            profile = user.profile
            profile.daily_reminder = self.cleaned_data['daily_reminder']
            profile.salary_date = int(self.cleaned_data['salary_date'])
            profile.save()
        return user

class LanguageUpdateForm(SearchableSelectFormMixin, forms.ModelForm):
    class Meta:
        model = UserProfile
        fields = ['language']
        widgets = {
            'language': forms.Select(attrs={'class': 'form-select'}),
        }

class SalaryDateUpdateForm(SearchableSelectFormMixin, forms.ModelForm):
    SALARY_DATE_CHOICES = [(i, str(i)) for i in range(1, 32)]
    salary_date = forms.ChoiceField(
        choices=SALARY_DATE_CHOICES,
        widget=forms.Select(attrs={'class': 'form-select'}),
        label=_('Salary Date'),
        help_text=_('Day of month when you receive salary (1-31)')
    )
    
    class Meta:
        model = UserProfile
        fields = ['salary_date']
        widgets = {
            'salary_date': forms.Select(attrs={'class': 'form-select'}),
        }

class CustomSignupForm(forms.Form):
    consent_email = forms.BooleanField(
        required=True,
        label=_("Email Address"),
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input'})
    )
    consent_transactions = forms.BooleanField(
        required=True,
        label=_("Financial Transactions"),
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input'})
    )
    consent_device = forms.BooleanField(
        required=True,
        label=_("Device and Browser Information"),
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input'})
    )

    def signup(self, request, user):
        from django.utils import timezone
        profile, created = UserProfile.objects.get_or_create(user=user)
        profile.consent_granted = True
        profile.consent_timestamp = timezone.now()
        profile.consent_version = 'v1.0'
        profile.save()

        from .models import ConsentEvent
        ConsentEvent.objects.create(
            user=user,
            action='GRANTED',
            purpose='Signup Data Collection',
            consent_version='v1.0',
            ip_address=request.META.get('REMOTE_ADDR') if request else None,
            user_agent=request.META.get('HTTP_USER_AGENT', '') if request else ''
        )

class ContactForm(forms.Form):
    name = forms.CharField(max_length=100, widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Your Name'}))
    email = forms.EmailField(widget=forms.EmailInput(attrs={'class': 'form-control', 'placeholder': 'name@example.com'}))
    # Honeypot implementation in form
    website = forms.CharField(required=False, widget=forms.TextInput(attrs={
        'style': 'position: absolute; left: -9999px; opacity: 0;',
        'tabindex': '-1',
        'autocomplete': 'off'
    }))
    subject = forms.CharField(max_length=200, widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'What is this about?'}))
    message = forms.CharField(widget=forms.Textarea(attrs={'class': 'form-control', 'rows': 5, 'placeholder': 'How can we help you?'}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        
        # Add reCAPTCHA field if keys are configured
        if getattr(settings, 'RECAPTCHA_PUBLIC_KEY', None) and getattr(settings, 'RECAPTCHA_PRIVATE_KEY', None):
            self.fields['captcha'] = ReCaptchaField(widget=ReCaptchaV3)


class SavingsGoalForm(SearchableSelectFormMixin, forms.ModelForm):
    class Meta:
        model = SavingsGoal
        fields = ['name', 'target_amount', 'currency', 'target_date', 'icon', 'color']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': _('e.g. Dream Vacation')}),
            'target_amount': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'currency': forms.Select(attrs={'class': 'form-select'}),
            'target_date': forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
            'icon': forms.TextInput(attrs={'class': 'form-control', 'placeholder': _('e.g. ✈️')}),
            'color': forms.Select(attrs={'class': 'form-select'}, choices=[
                ('primary', _('Blue')),
                ('success', _('Green')),
                ('danger', _('Red')),
                ('warning', _('Yellow')),
                ('info', _('Light Blue')),
            ]),
        }
        
    def __init__(self, *args, **kwargs):
        user = kwargs.pop('user', None)
        super().__init__(*args, **kwargs)
        if user:
            self.fields['currency'].initial = user.profile.currency

    def clean_target_amount(self):
        target_amount = self.cleaned_data.get('target_amount')
        if target_amount is not None and target_amount <= 0:
            raise forms.ValidationError(_("Target amount must be greater than zero."))
        return target_amount

class GoalContributionForm(SearchableSelectFormMixin, forms.ModelForm):
    class Meta:
        model = GoalContribution
        fields = ['account', 'amount', 'date']
        widgets = {
            'account': forms.Select(attrs={'class': 'form-select searchable-select'}),
            'amount': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'placeholder': _('Amount')}),
            'date': forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
        }
    
    def __init__(self, *args, **kwargs):
        user = kwargs.pop('user', None)
        super().__init__(*args, **kwargs)
        self.fields['date'].initial = date.today
        if user:
            # Enforce Tier Limits for Accounts
            all_accounts = Account.objects.filter(user=user, is_active=True).order_by('created_at', 'id')
            limit = get_limit(user.profile.active_tier, 'accounts')
            if limit != -1:
                unlocked_ids = all_accounts.values_list('id', flat=True)[:limit]
                self.fields['account'].queryset = all_accounts.filter(id__in=unlocked_ids)
            else:
                self.fields['account'].queryset = all_accounts
        
    def clean_amount(self):
        amount = self.cleaned_data.get('amount')
        if amount is not None and amount <= 0:
            raise forms.ValidationError(_("Contribution amount must be greater than zero."))
        return amount
 
 
class CategoryForm(SearchableSelectFormMixin, forms.ModelForm):
    icon = forms.ChoiceField(choices=BOOTSTRAP_ICONS, widget=forms.Select(attrs={'class': 'form-select'}), required=False)
 
    class Meta:
        model = Category
        fields = ['name', 'icon', 'limit']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': _('Category Name')}),
            'limit': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'placeholder': '0.00'}),
        }

    def clean_name(self):
        name = self.cleaned_data.get('name', '').strip()
        if not name:
            raise forms.ValidationError(_('Category name is required.'))
        user = getattr(self.instance, 'user', None) or getattr(self, '_user', None)
        if user and Category.objects.filter(user=user, name__iexact=name).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError(_('A category with this name already exists.'))
        return name

class AccountForm(SearchableSelectFormMixin, forms.ModelForm):
    # Optional: Link to a Loan record (used for LOAN_OUTSTANDING strategy)
    linked_loan = forms.ModelChoiceField(
        queryset=Loan.objects.none(),
        required=False,
        label=_('Linked Loan'),
        help_text=_('For loan account types (Home Loan, Vehicle Loan, etc.) — link to a Loan record for outstanding balance calculation.'),
        widget=forms.Select(attrs={'class': 'form-select'}),
        empty_label=_('— No linked loan —'),
    )

    # Optional DEPOSIT accrual fields
    deposit_principal = forms.DecimalField(
        required=False,
        max_digits=15,
        decimal_places=2,
        label=_('Deposit Principal'),
        widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
    )
    deposit_rate = forms.DecimalField(
        required=False,
        max_digits=7,
        decimal_places=4,
        label=_('Annual Interest Rate (%)'),
        widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.0001'}),
    )
    deposit_start_date = forms.DateField(
        required=False,
        label=_('Deposit Start Date'),
        widget=forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
    )
    deposit_compounding = forms.ChoiceField(
        choices=[('', '—')] + [
            ('SIMPLE', _('Simple Interest')),
            ('QUARTERLY', _('Quarterly Compounding')),
            ('MONTHLY', _('Monthly Compounding')),
            ('ANNUAL', _('Annual Compounding')),
        ],
        required=False,
        label=_('Compounding Frequency'),
        widget=forms.Select(attrs={'class': 'form-select'}),
    )
    deposit_maturity_date = forms.DateField(
        required=False,
        label=_('Deposit Maturity Date'),
        widget=forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
    )
    rd_installment_amount = forms.DecimalField(
        required=False,
        max_digits=15,
        decimal_places=2,
        label=_('RD Installment Amount'),
        widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
    )
    rd_installment_day = forms.IntegerField(
        required=False,
        min_value=1,
        max_value=28,
        label=_('RD Installment Day of Month (1-28)'),
        widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '1', 'min': '1', 'max': '28'}),
    )

    class Meta:
        model = Account
        fields = [
            'name', 'account_type', 'balance', 'currency',
            'linked_loan', 'linked_physical_asset',
            'deposit_principal', 'deposit_rate', 'deposit_start_date', 'deposit_maturity_date', 'deposit_compounding', 'show_accrued_balance',
            'rd_installment_amount', 'rd_installment_day',
        ]
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': _('Account Name (e.g. HDFC Bank)')}),
            'account_type': forms.Select(attrs={'class': 'form-select searchable-select'}),
            'balance': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'currency': forms.Select(attrs={'class': 'form-select'}),
            'linked_physical_asset': forms.Select(attrs={'class': 'form-select'}),
            'show_accrued_balance': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }

    @property
    def fields_by_type_json(self):
        import json
        from .account_types import ACCOUNT_TYPE_META, get_fields_for_account_type
        mapping = {code: get_fields_for_account_type(code) for code in ACCOUNT_TYPE_META}
        return json.dumps(mapping)

    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop('user', None)
        super().__init__(*args, **kwargs)
        if self.user:
            self.fields['currency'].initial = self.user.profile.currency
            self.fields['linked_loan'].queryset = Loan.objects.filter(
                user=self.user, is_active=True
            ).order_by('name')
            self.fields['linked_physical_asset'].queryset = (
                PhysicalAsset.objects.filter(user=self.user, is_active=True).order_by('name')
            )
        else:
            self.fields['linked_loan'].queryset = Loan.objects.none()
            self.fields['linked_physical_asset'].queryset = PhysicalAsset.objects.none()

    def clean_name(self):
        name = self.cleaned_data.get('name')
        if name and self.user:
            queryset = Account.objects.filter(user=self.user, name__iexact=name)
            if self.instance.pk:
                queryset = queryset.exclude(pk=self.instance.pk)

            if queryset.exists():
                raise forms.ValidationError(_("An account with this name already exists."))
        return name

    def clean(self):
        cleaned_data = super().clean()
        account_type = cleaned_data.get('account_type')
        if not account_type:
            return cleaned_data

        from .account_types import strategy_for, STRATEGY, get_fields_for_account_type
        strategy = strategy_for(account_type)
        allowed_fields = set(get_fields_for_account_type(account_type))

        linked_loan = cleaned_data.get('linked_loan')
        linked_physical_asset = cleaned_data.get('linked_physical_asset')

        # Validation for DEPOSIT strategy
        if strategy == STRATEGY.DEPOSIT:
            deposit_principal = cleaned_data.get('deposit_principal')
            if deposit_principal is None and (not self.instance.pk or self.instance.account_type != account_type):
                self.add_error('deposit_principal', _('Deposit principal is required for deposit accounts.'))

            if cleaned_data.get('deposit_rate') is None:
                self.add_error('deposit_rate', _('Interest rate is required for deposit accounts.'))

            if not cleaned_data.get('deposit_start_date'):
                self.add_error('deposit_start_date', _('Start date is required for deposit accounts.'))

            if account_type == 'RD':
                if not cleaned_data.get('rd_installment_amount'):
                    self.add_error('rd_installment_amount', _('Installment amount is required for Recurring Deposits.'))

                rd_day = cleaned_data.get('rd_installment_day')
                if not rd_day:
                    self.add_error('rd_installment_day', _('Installment day (1-28) is required for Recurring Deposits.'))
                elif not (1 <= rd_day <= 28):
                    self.add_error('rd_installment_day', _('Installment day must be between 1 and 28.'))

        # Required: LOAN_OUTSTANDING strategy accounts must link a Loan record
        if strategy == STRATEGY.LOAN_OUTSTANDING and not linked_loan:
            self.add_error(
                'linked_loan',
                _('This account type requires a linked Loan record for outstanding balance valuation.')
            )

        # Required: physical valuation strategy accounts must link a Physical Asset
        if strategy in (STRATEGY.PHYSICAL_VALUATION, STRATEGY.INSURANCE_SURRENDER) \
                and not linked_physical_asset:
            self.add_error(
                'linked_physical_asset',
                _('This account type requires a linked Physical Asset for valuation.')
            )

        # Null out stray/irrelevant fields not belonging to this account_type strategy
        strategy_fields = {
            'deposit_principal', 'deposit_rate', 'deposit_start_date',
            'deposit_maturity_date', 'deposit_compounding',
            'rd_installment_amount', 'rd_installment_day',
            'linked_loan', 'linked_physical_asset',
        }
        for field in strategy_fields:
            if field not in allowed_fields:
                cleaned_data[field] = None

        return cleaned_data

    def save(self, commit=True):
        account = super().save(commit=False)
        for field, value in self.cleaned_data.items():
            if hasattr(account, field):
                setattr(account, field, value)
        if commit:
            account.save()
        return account

class TransferForm(SearchableSelectFormMixin, forms.ModelForm):
    class Meta:
        model = Transfer
        fields = ['date', 'amount', 'from_account', 'to_account', 'description', 'client_dedup_key']
        widgets = {
            'date': forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
            'amount': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'from_account': forms.Select(attrs={'class': 'form-select searchable-select'}),
            'to_account': forms.Select(attrs={'class': 'form-select searchable-select'}),
            'description': forms.Textarea(attrs={'class': 'form-control', 'rows': 2}),
            'client_dedup_key': forms.HiddenInput(),
        }

    def __init__(self, *args, **kwargs):
        user = kwargs.pop('user', None)
        super().__init__(*args, **kwargs)

        if not self.is_bound and not self.instance.pk:
            self.fields['client_dedup_key'].initial = str(uuid.uuid4())
        self.fields['date'].initial = date.today
        if user:
            # Enforce Tier Limits for Accounts
            all_accounts = Account.objects.filter(user=user, is_active=True).order_by('created_at', 'id')
            limit = get_limit(user.profile.active_tier, 'accounts')
            if limit != -1:
                unlocked_ids = all_accounts.values_list('id', flat=True)[:limit]
                accounts_qs = all_accounts.filter(id__in=unlocked_ids)
            else:
                accounts_qs = all_accounts

            self.fields['from_account'].queryset = accounts_qs
            self.fields['to_account'].queryset = accounts_qs

    def clean(self):
        cleaned_data = super().clean()
        from_account = cleaned_data.get('from_account')
        to_account = cleaned_data.get('to_account')
        amount = cleaned_data.get('amount')

        if from_account == to_account:
            raise forms.ValidationError(_("Source and destination accounts must be different."))
        
        if amount and amount <= 0:
            raise forms.ValidationError(_("Transfer amount must be greater than zero."))

        if from_account and amount and from_account.balance < amount:
            # Allow negative balances to show "liability", example: in case of credit cards, 
            # the account balance can be negative
            pass

        return cleaned_data

class LoanForm(SearchableSelectFormMixin, forms.ModelForm):
    class Meta:
        model = Loan
        fields = ['name', 'loan_type', 'initial_principal', 'duration_months', 'start_date', 'currency']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': _('e.g. Home Loan')}),
            'loan_type': forms.Select(attrs={'class': 'form-select'}),
            'initial_principal': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'duration_months': forms.NumberInput(attrs={'class': 'form-control'}),
            'start_date': forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
            'currency': forms.Select(attrs={'class': 'form-select'}),
        }

    interest_rate = forms.DecimalField(
        max_digits=5, decimal_places=2, label=_('Initial Interest Rate (%)'),
        widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'})
    )

    def __init__(self, *args, **kwargs):
        user = kwargs.pop('user', None)
        super().__init__(*args, **kwargs)
        self.fields['start_date'].initial = date.today
        if user:
            self.fields['currency'].initial = user.profile.currency
        
        if self.instance.pk:
            latest_rate = self.instance.interest_rates.order_by('-effective_date').first()
            if latest_rate:
                self.fields['interest_rate'].initial = latest_rate.interest_rate

class LoanInterestRateForm(SearchableSelectFormMixin, forms.ModelForm):
    class Meta:
        model = LoanInterestRate
        fields = ['interest_rate', 'effective_date']
        widgets = {
            'interest_rate': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'effective_date': forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['effective_date'].initial = date.today

class LoanRepaymentForm(SearchableSelectFormMixin, forms.ModelForm):
    add_to_recurring = forms.BooleanField(required=False, label=_("Make this a recurring loan repayment"))
    recurring_frequency = forms.ChoiceField(
        choices=RecurringTransaction.FREQUENCY_CHOICES,
        required=False,
        initial='MONTHLY',
        label=_("Frequency"),
        widget=forms.Select(attrs={'class': 'form-select'})
    )

    class Meta:
        model = LoanRepayment
        fields = ['from_account', 'amount', 'principal_portion', 'interest_portion', 'date']
        widgets = {
            'from_account': forms.Select(attrs={'class': 'form-select searchable-select'}),
            'amount': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'onchange': 'recalculatePortions()'}),
            'principal_portion': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'interest_portion': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'date': forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
        }

    def __init__(self, *args, **kwargs):
        user = kwargs.pop('user', None)
        loan = kwargs.pop('loan', None)
        super().__init__(*args, **kwargs)
        self.loan = loan
        if loan:
            self.instance.loan = loan
        self.fields['date'].initial = date.today
        
        if user:
            # Enforce Tier Limits for Accounts
            all_accounts = Account.objects.filter(user=user, is_active=True).order_by('created_at', 'id')
            limit = get_limit(user.profile.active_tier, 'accounts')
            if limit != -1:
                unlocked_ids = list(all_accounts.values_list('id', flat=True)[:limit])
                self.fields['from_account'].queryset = all_accounts.filter(id__in=unlocked_ids)
            else:
                self.fields['from_account'].queryset = all_accounts

            # Default to the first account (likely 'Cash')
            default_account = self.fields['from_account'].queryset.filter(name='Cash').first()
            if default_account:
                self.fields['from_account'].initial = default_account
        
        if loan:
            # Pre-calculate a suggested split, but the amount can be changed freely.
            breakdown = self._calculate_repayment_breakdown(Decimal('0.00'), loan, use_initial_preview=True)
            if breakdown:
                self.fields['amount'].initial = breakdown['suggested_amount']
                self.fields['principal_portion'].initial = breakdown['principal_portion']
                self.fields['interest_portion'].initial = breakdown['interest_portion']

        self.fields['principal_portion'].required = False
        self.fields['interest_portion'].required = False

    def _calculate_repayment_breakdown(self, amount, loan, use_initial_preview=False):
        from .services import LoanService

        summary = LoanService.get_loan_summary(loan)
        latest_rate_obj = loan.interest_rates.order_by('-effective_date').first()
        annual_rate = float(latest_rate_obj.interest_rate) if latest_rate_obj else 0.0

        # EMI suggestion is useful for the initial preview only.
        today = date.today()
        months_passed = (today.year - loan.start_date.year) * 12 + today.month - loan.start_date.month
        remaining_months = max(1, loan.duration_months - months_passed)

        suggested_amount = LoanService.calculate_emi(summary['remaining_principal'], annual_rate, remaining_months)
        estimated_interest = summary['remaining_principal'] * (annual_rate / 12.0 / 100.0)

        if use_initial_preview:
            return {
                'suggested_amount': round(suggested_amount, 2),
                'principal_portion': round(suggested_amount - estimated_interest, 2),
                'interest_portion': round(estimated_interest, 2),
            }

        try:
            amount_value = Decimal(str(amount))
        except Exception:
            return None

        interest_portion = min(amount_value, Decimal(str(estimated_interest))).quantize(Decimal('0.01'))
        principal_portion = (amount_value - interest_portion).quantize(Decimal('0.01'))
        return {
            'amount': amount_value.quantize(Decimal('0.01')),
            'principal_portion': principal_portion,
            'interest_portion': interest_portion,
        }

    def clean(self):
        cleaned_data = super().clean()
        amount = cleaned_data.get('amount')
        add_to_recurring = cleaned_data.get('add_to_recurring')
        recurring_frequency = cleaned_data.get('recurring_frequency')
        loan = self.loan

        if amount is not None and amount <= 0:
            self.add_error('amount', _("Repayment amount must be greater than zero."))

        if loan and amount is not None:
            user_principal = cleaned_data.get('principal_portion')
            user_interest = cleaned_data.get('interest_portion')

            if user_principal is not None and user_interest is not None:
                cleaned_data['principal_portion'] = user_principal
                cleaned_data['interest_portion'] = user_interest
            elif user_principal is not None:
                cleaned_data['principal_portion'] = user_principal
                cleaned_data['interest_portion'] = max(Decimal('0.00'), amount - user_principal)
            elif user_interest is not None:
                cleaned_data['interest_portion'] = user_interest
                cleaned_data['principal_portion'] = max(Decimal('0.00'), amount - user_interest)
            else:
                breakdown = self._calculate_repayment_breakdown(amount, loan)
                if breakdown:
                    cleaned_data['amount'] = breakdown['amount']
                    cleaned_data['principal_portion'] = breakdown['principal_portion']
                    cleaned_data['interest_portion'] = breakdown['interest_portion']

        if add_to_recurring and not recurring_frequency:
            self.add_error('recurring_frequency', _("Please select a recurring frequency."))

        return cleaned_data

class CapitalEventForm(SearchableSelectFormMixin, forms.ModelForm):
    class Meta:
        model = CapitalEvent
        fields = [
            'date', 'amount', 'currency', 'account', 'subtype',
            'linked_loan', 'note',
            'exclude_from_averages', 'exclude_from_budget', 'include_in_net_worth',
        ]
        widgets = {
            'date': forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
            'amount': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'min': '0.01'}),
            'currency': forms.Select(attrs={'class': 'form-select'}),
            'account': forms.Select(attrs={'class': 'form-select'}),
            'subtype': forms.Select(attrs={'class': 'form-select', 'id': 'id_subtype'}),
            'linked_loan': forms.Select(attrs={'class': 'form-select', 'id': 'id_linked_loan'}),
            'note': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
            'exclude_from_averages': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'exclude_from_budget': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'include_in_net_worth': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }

    def __init__(self, *args, **kwargs):
        user = kwargs.pop('user', None)
        super().__init__(*args, **kwargs)
        self.fields['date'].initial = date.today
        if user:
            self.fields['currency'].initial = user.profile.currency
            self.fields['account'].queryset = Account.objects.filter(user=user, is_active=True).order_by('name')
            self.fields['linked_loan'].queryset = Loan.objects.filter(user=user, is_active=True).order_by('name')
            self.fields['linked_loan'].required = False
            self.fields['linked_loan'].empty_label = _('— No linked loan —')

    def clean_amount(self):
        amount = self.cleaned_data.get('amount')
        if amount is not None and amount <= 0:
            raise forms.ValidationError(_("Amount must be greater than zero."))
        return amount
