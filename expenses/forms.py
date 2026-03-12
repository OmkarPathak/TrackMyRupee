from django import template
from django.utils.translation import gettext_lazy as _
from django import forms
from django.contrib.auth.models import User
from django.contrib.auth.forms import UserCreationForm
from allauth.socialaccount.models import SocialAccount
from .models import Expense, Category, Income, RecurringTransaction, UserProfile
from django_recaptcha.fields import ReCaptchaField
from django_recaptcha.widgets import ReCaptchaV3
from datetime import date

class ExpenseForm(forms.ModelForm):
    class Meta:
        model = Expense
        fields = ['date', 'amount', 'currency', 'description', 'category', 'payment_method']
        widgets = {
            'date': forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
            'amount': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'currency': forms.Select(attrs={'class': 'form-select'}),
            'description': forms.TextInput(attrs={'class': 'form-control'}),
            'payment_method': forms.Select(attrs={'class': 'form-select'}),
        }

    def __init__(self, *args, **kwargs):
        user = kwargs.pop('user', None)
        super().__init__(*args, **kwargs)
        self.fields['date'].initial = date.today
        
        # If user is provided, populate category choices
        if user:
            self.fields['currency'].initial = user.profile.currency
            self.fields['payment_method'].initial = 'Credit Card'
            categories = Category.objects.filter(user=user).order_by('id')
            
            # Enforce Tier Limits
            profile = user.profile
            if not profile.is_pro:
                limit = 10 if profile.is_plus else 5
                categories = categories[:limit]
            
            # Create choices list: [(name, name), ...]
            choices = [(cat.name, cat.name) for cat in categories]
            self.fields['category'].widget = forms.Select(choices=choices, attrs={'class': 'form-select django-multi-select'})
        else:
            self.fields['category'].widget = forms.TextInput(attrs={'class': 'form-control'})

    def clean_category(self):
        category = self.cleaned_data.get('category')
        if category:
            return category.strip()
        return category

class IncomeForm(forms.ModelForm):
    class Meta:
        model = Income
        fields = ['date', 'amount', 'currency', 'source', 'description']
        widgets = {
            'date': forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
            'amount': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'currency': forms.Select(attrs={'class': 'form-select'}),
            'source': forms.TextInput(attrs={'class': 'form-control', 'placeholder': _('e.g. Salary, Freelance')}),
            'description': forms.Textarea(attrs={'class': 'form-control', 'rows': 2}),
        }
    
    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop('user', None)
        super().__init__(*args, **kwargs)
        self.fields['date'].initial = date.today
        if self.user:
            self.fields['currency'].initial = self.user.profile.currency
        
    def clean_source(self):
        source = self.cleaned_data.get('source')
        if source:
            return source.strip()
        return source

class RecurringTransactionForm(forms.ModelForm):
    class Meta:
        model = RecurringTransaction
        fields = ['transaction_type', 'amount', 'currency', 'category', 'source', 'frequency', 'start_date', 'description', 'is_active', 'payment_method']
        widgets = {
            'transaction_type': forms.Select(attrs={'class': 'form-select', 'onchange': 'toggleFields()'}),
            'amount': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'currency': forms.Select(attrs={'class': 'form-select'}),
            'category': forms.Select(attrs={'class': 'form-select'}),
            'source': forms.TextInput(attrs={'class': 'form-control', 'placeholder': _('e.g. Salary, Rent')}),
            'frequency': forms.Select(attrs={'class': 'form-select'}),
            'start_date': forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
            'description': forms.Textarea(attrs={'class': 'form-control', 'rows': 2}),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'payment_method': forms.Select(attrs={'class': 'form-select'}),
        }

    def __init__(self, *args, **kwargs):
        user = kwargs.pop('user', None)
        super().__init__(*args, **kwargs)
        if user:
            self.fields['currency'].initial = user.profile.currency
        
        # Category field as Select for Expenses
        if user:
            categories = Category.objects.filter(user=user).order_by('id')
            
            # Enforce Tier Limits
            profile = user.profile
            if not profile.is_pro:
                limit = 10 if profile.is_plus else 5
                categories = categories[:limit]

            category_choices = [('', '---------')] + [(cat.name, cat.name) for cat in categories]
            self.fields['category'].widget = forms.Select(choices=category_choices, attrs={'class': 'form-select'})
        else:
            self.fields['category'].widget = forms.TextInput(attrs={'class': 'form-control'})
        
        self.fields['source'].widget = forms.TextInput(attrs={'class': 'form-control', 'placeholder': _('e.g. Salary (For Income only)')})
        
        # Ensure fields are optional at form level since we handle them in clean()
        self.fields['category'].required = False
        self.fields['source'].required = False

    def clean(self):
        cleaned_data = super().clean()
        transaction_type = cleaned_data.get('transaction_type')
        category = cleaned_data.get('category')
        source = cleaned_data.get('source')

        if transaction_type == 'EXPENSE' and not category:
            self.add_error('category', _('Category is required for expenses.'))
        

        if transaction_type == 'INCOME' and not source:
            self.add_error('source', _('Source is required for income.'))

        return cleaned_data

class ProfileUpdateForm(forms.ModelForm):
    auth_email = forms.EmailField(required=True, label='Email Address')
    first_name = forms.CharField(required=False, widget=forms.TextInput(attrs={'class': 'form-control'}))
    last_name = forms.CharField(required=False, widget=forms.TextInput(attrs={'class': 'form-control'}))

    class Meta:
        model = User
        fields = ['first_name', 'last_name']
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['auth_email'].initial = self.instance.email
        self.fields['auth_email'].initial = self.instance.email
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
        return user

class LanguageUpdateForm(forms.ModelForm):
    class Meta:
        model = UserProfile
        fields = ['language']
        widgets = {
            'language': forms.Select(attrs={'class': 'form-select'}),
        }

class CustomSignupForm(UserCreationForm):
    email = forms.EmailField(required=True, label='Email Address')

    class Meta:
        model = User
        fields = ('username', 'email')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from django.conf import settings

        # Add reCAPTCHA field if keys are configured
        if getattr(settings, 'RECAPTCHA_PUBLIC_KEY', None) and getattr(settings, 'RECAPTCHA_PRIVATE_KEY', None):
            self.fields['captcha'] = ReCaptchaField(widget=ReCaptchaV3)

    def clean_email(self):
        email = self.cleaned_data.get('email')
        if User.objects.filter(email=email).exists():
            raise forms.ValidationError("A user with this email already exists.")
        return email

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
        from django.conf import settings
        
        # Add reCAPTCHA field if keys are configured
        if getattr(settings, 'RECAPTCHA_PUBLIC_KEY', None) and getattr(settings, 'RECAPTCHA_PRIVATE_KEY', None):
            self.fields['captcha'] = ReCaptchaField(widget=ReCaptchaV3)

from .models import SavingsGoal, GoalContribution

class SavingsGoalForm(forms.ModelForm):
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

class GoalContributionForm(forms.ModelForm):
    class Meta:
        model = GoalContribution
        fields = ['amount', 'date']
        widgets = {
            'amount': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'placeholder': _('Amount')}),
            'date': forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
        }
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['date'].initial = date.today
        
    def clean_amount(self):
        amount = self.cleaned_data.get('amount')
        if amount is not None and amount <= 0:
            raise forms.ValidationError(_("Contribution amount must be greater than zero."))
        return amount
 
 
class CategoryForm(forms.ModelForm):
    from .utils import BOOTSTRAP_ICONS
    icon = forms.ChoiceField(choices=BOOTSTRAP_ICONS, widget=forms.Select(attrs={'class': 'form-select'}), required=False)
 
    class Meta:
        model = Category
        fields = ['name', 'icon', 'limit', 'is_investment']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': _('Category Name')}),
            'limit': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'placeholder': '0.00'}),
            'is_investment': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }

