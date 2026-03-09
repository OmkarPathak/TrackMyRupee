import json
from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth import logout
from django.views.generic import TemplateView, UpdateView, DeleteView
from django.contrib import messages
from django.utils.translation import gettext as _
from django.urls import reverse_lazy
from django.utils import timezone, translation
from django.conf import settings
from django.db import IntegrityError
from allauth.socialaccount.models import SocialAccount

from ..models import UserProfile, Expense, Income, RecurringTransaction
from ..forms import ProfileUpdateForm, LanguageUpdateForm

class SettingsHomeView(LoginRequiredMixin, TemplateView):
    template_name = 'expenses/settings_home.html'

class AccountDeleteView(LoginRequiredMixin, DeleteView):
    model = settings.AUTH_USER_MODEL # Handled via get_object
    success_url = reverse_lazy('landing')
    template_name = 'expenses/account_confirm_delete.html'

    def get_object(self, queryset=None):
        return self.request.user

    def form_valid(self, form):
        user = self.get_object()
        logout(self.request)
        user.delete()
        messages.success(self.request, _("Your account has been deleted successfully."))
        return redirect(self.success_url)

class CurrencyUpdateView(LoginRequiredMixin, UpdateView):
    model = UserProfile
    fields = ['currency']
    template_name = 'expenses/currency_settings.html'
    success_url = reverse_lazy('currency-settings')

    def get_object(self, queryset=None):
        profile, created = UserProfile.objects.get_or_create(user=self.request.user)
        return profile

    def form_valid(self, form):
        old_currency = self.get_object().currency
        new_currency = form.cleaned_data.get('currency')
        
        response = super().form_valid(form)
        
        if old_currency != new_currency:
            user = self.request.user
            skipped_count = 0
            for model in [Expense, Income, RecurringTransaction]:
                transactions = model.objects.filter(user=user)
                for tx in transactions:
                    try:
                        tx.save() 
                    except IntegrityError:
                        skipped_count += 1
                        continue
            
            if skipped_count > 0:
                messages.warning(self.request, _('Currency preference updated. %(count)d transactions were skipped due to potential duplication.') % {'count': skipped_count})
            else:
                messages.success(self.request, _('Currency preference updated successfully.'))
        else:
            messages.success(self.request, _('Currency preference updated successfully.'))
            
        return response

class LanguageUpdateView(LoginRequiredMixin, UpdateView):
    model = UserProfile
    form_class = LanguageUpdateForm
    template_name = 'expenses/language_settings.html'
    success_url = reverse_lazy('language-settings')

    def get_object(self, queryset=None):
        profile, created = UserProfile.objects.get_or_create(user=self.request.user)
        return profile

    def form_valid(self, form):
        lang = form.cleaned_data.get('language')
        translation.activate(lang)
        messages.success(self.request, _('Language preference updated successfully.'))
        
        response = super().form_valid(form)
        response.set_cookie(settings.LANGUAGE_COOKIE_NAME, lang)
        return response

class ProfileUpdateView(LoginRequiredMixin, UpdateView):
    model = settings.AUTH_USER_MODEL
    form_class = ProfileUpdateForm
    template_name = 'expenses/profile_settings.html'
    success_url = reverse_lazy('profile-settings')

    def get_object(self, queryset=None):
        return self.request.user

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['page_title'] = _('Profile Settings')
        context['is_social_user'] = SocialAccount.objects.filter(user=self.request.user).exists()
        
        now = timezone.now()
        has_any_data = Expense.objects.filter(user=self.request.user).exists() or Income.objects.filter(user=self.request.user).exists()
        show_year_in_review = False
        year_in_review_year = None
        
        if has_any_data:
            # Logic: 
            # 1. From Nov 1st to Dec 31st, show CURRENT year's review (as it's coming to an end)
            # 2. From Jan 1st to Oct 31st, show PREVIOUS year's review
            if now.month >= 11:
                year_in_review_year = now.year
            else:
                year_in_review_year = now.year - 1
                
            if year_in_review_year:
                show_year_in_review = Expense.objects.filter(user=self.request.user, date__year=year_in_review_year).exists()
                
        context['show_year_in_review'] = show_year_in_review
        context['year_in_review_year'] = year_in_review_year
        
        return context

    def form_valid(self, form):
        messages.success(self.request, _("Profile updated successfully."))
        return super().form_valid(form)
