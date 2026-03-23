from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import JsonResponse
from django.shortcuts import redirect
from django.urls import reverse_lazy
from django.utils.translation import gettext as _
from django.views.generic import CreateView, DeleteView, ListView, UpdateView

from ..forms import CategoryForm
from ..models import Category


class CategoryListView(LoginRequiredMixin, ListView):
    model = Category
    template_name = 'expenses/category_list.html'
    context_object_name = 'categories'
    paginate_by = 10

    def get_queryset(self):
        queryset = Category.objects.filter(user=self.request.user).order_by('name')
        search_query = self.request.GET.get('search')
        if search_query:
            queryset = queryset.filter(name__icontains=search_query)
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['search_query'] = self.request.GET.get('search', '')
        
        # Nudge context for upgrade banner
        profile = self.request.user.profile
        from finance_tracker.plans import get_limit
        limit = get_limit(profile.active_tier, 'budget_categories')
        
        if limit != -1:
            total_categories = Category.objects.filter(user=self.request.user).count()
            if profile.active_tier == 'PLUS':
                upgrade_tier = 'PRO'
            else:
                upgrade_tier = 'PLUS'
            context['reached_limit'] = total_categories >= limit
            context['current_count'] = total_categories
            context['limit'] = limit
            context['nudge_current'] = total_categories
            context['nudge_limit'] = limit
            context['nudge_feature_name'] = 'categories'
            context['nudge_upgrade_tier'] = upgrade_tier
            context['nudge_at_limit'] = total_categories >= limit
        
        context['reached_limit'] = not profile.can_add_category()
        
        from ..utils import BOOTSTRAP_ICONS
        context['bootstrap_icons'] = BOOTSTRAP_ICONS
        return context

@login_required
def create_category_ajax(request):
    import json
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            name = data.get('name', '').strip()
            if not name:
                return JsonResponse({'success': False, 'error': _('Name is required.')})
                
            profile = request.user.profile
            if not profile.can_add_category():
                return JsonResponse({'success': False, 'error': _('Category limit reached.')}, status=403)

            if Category.objects.filter(user=request.user, name__iexact=name).exists():
                return JsonResponse({'success': False, 'error': _('A category with this name already exists.')})
                
            category = Category.objects.create(user=request.user, name=name)
            return JsonResponse({'success': True, 'id': category.id, 'name': category.name})
        except Exception:
            return JsonResponse({'success': False, 'error': _('Something went wrong. Please try again.')}, status=400)
    return JsonResponse({'success': False}, status=405)

class CategoryCreateView(LoginRequiredMixin, CreateView):
    model = Category
    form_class = CategoryForm
    template_name = 'expenses/category_form.html'
    success_url = reverse_lazy('category-list')

    def form_valid(self, form):
        if not self.request.user.profile.can_add_category():
            messages.error(self.request, _("Category limit reached. Please upgrade."))
            return redirect('pricing')
        form.instance.user = self.request.user
        return super().form_valid(form)

    def get_form(self, form_class=None):
        form = super().get_form(form_class)
        form.instance.user = self.request.user
        return form

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        from ..utils import BOOTSTRAP_ICONS
        context['bootstrap_icons'] = BOOTSTRAP_ICONS
        # Check Limits
        current_count = Category.objects.filter(user=self.request.user).count()
        from finance_tracker.plans import get_limit
        limit = get_limit(self.request.user.profile.active_tier, 'budget_categories')

        context['reached_limit'] = not self.request.user.profile.can_add_category()
        context['category_limit'] = limit if limit != -1 else _('Unlimited')
        return context

class CategoryUpdateView(LoginRequiredMixin, UpdateView):
    model = Category
    form_class = CategoryForm
    template_name = 'expenses/category_form.html'
    success_url = reverse_lazy('category-list')

    def dispatch(self, request, *args, **kwargs):
        obj = self.get_object()
        profile = request.user.profile
        from finance_tracker.plans import get_limit
        limit = get_limit(profile.active_tier, 'budget_categories')
        if limit == -1: return super().dispatch(request, *args, **kwargs)
        categories = list(Category.objects.filter(user=request.user).order_by('id'))
        if obj in categories and categories.index(obj) >= limit:
            messages.error(request, _("This category is locked."))
            return redirect('category-list')
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        return Category.objects.filter(user=self.request.user)

    def get_success_url(self):
        next_url = self.request.POST.get('next') or self.request.GET.get('next')
        if next_url:
            return next_url
        return super().get_success_url()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        from ..utils import BOOTSTRAP_ICONS
        context['bootstrap_icons'] = BOOTSTRAP_ICONS
        context['next_url'] = self.request.POST.get('next') or self.request.GET.get('next') or ''
        return context

    def form_valid(self, form):
        from django.contrib import messages
        from django.db import IntegrityError
        try:
            # Store old name to update related expenses
            old_name = self.get_object().name
            response = super().form_valid(form)
            new_name = self.object.name
            
            if old_name != new_name:
                from ..models import Expense
                Expense.objects.filter(user=self.request.user, category=old_name).update(category=new_name)
                
            return response
        except IntegrityError:
            messages.error(self.request, "This category already exists.")
            return self.form_invalid(form)

class CategoryDeleteView(LoginRequiredMixin, DeleteView):
    model = Category
    success_url = reverse_lazy('category-list')
    def get_queryset(self): return Category.objects.filter(user=self.request.user)
