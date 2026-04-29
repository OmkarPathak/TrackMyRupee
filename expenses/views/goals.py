import json

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.utils.translation import gettext as _
from django.views.generic import CreateView, DeleteView, ListView, UpdateView, View
from finance_tracker.plans import get_limit

from ..forms import GoalContributionForm, SavingsGoalForm
from ..models import GoalContribution, SavingsGoal


class SavingsGoalListView(LoginRequiredMixin, ListView):
    model = SavingsGoal
    template_name = 'expenses/goal_list.html'
    context_object_name = 'ignored'

    def get_queryset(self):
        return SavingsGoal.objects.filter(user=self.request.user).order_by('created_at', 'id')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        all_goals = list(self.get_queryset())
        profile = self.request.user.profile
        from finance_tracker.plans import get_limit
        limit = get_limit(profile.active_tier, 'savings_goals')
        if limit == -1: limit = len(all_goals) + 1 # Unused for infinity, but for safety
        for i, goal in enumerate(all_goals):
            goal.is_locked = limit != -1 and i >= limit
        context.update({'goals': all_goals, 'total_saved': round(sum(g.current_amount for g in all_goals), 2), 'can_create_goal': profile.can_add_goal()})
        return context

class SavingsGoalCreateView(LoginRequiredMixin, CreateView):
    model = SavingsGoal
    form_class = SavingsGoalForm
    template_name = 'expenses/goal_form.html'
    success_url = reverse_lazy('goal-list')

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return super().dispatch(request, *args, **kwargs)
            
        if not request.user.profile.can_add_goal():
            messages.error(request, _("Goal limit reached."))
            return redirect('goal-list')
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        form.instance.user = self.request.user
        messages.success(self.request, _("Savings goal created successfully!"))
        return super().form_valid(form)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs(); kwargs['user'] = self.request.user
        return kwargs

class SavingsGoalUpdateView(LoginRequiredMixin, UpdateView):
    model = SavingsGoal
    form_class = SavingsGoalForm
    template_name = 'expenses/goal_form.html'
    success_url = reverse_lazy('goal-list')

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return super().dispatch(request, *args, **kwargs)
            
        obj = self.get_object(); profile = request.user.profile
        from finance_tracker.plans import get_limit
        limit = get_limit(profile.active_tier, 'savings_goals')
        if limit != -1:
            goals = list(SavingsGoal.objects.filter(user=request.user).order_by('created_at', 'id'))
            if obj in goals and goals.index(obj) >= limit:
                messages.error(request, _("This goal is locked."))
                return redirect('goal-list')
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs(); kwargs['user'] = self.request.user
        return kwargs

    def get_queryset(self):
        return super().get_queryset().filter(user=self.request.user)

    def form_valid(self, form):
        from django.contrib import messages
        from django.utils.translation import gettext as _
        messages.success(self.request, _("Savings goal updated successfully!"))
        return super().form_valid(form)

class SavingsGoalDeleteView(LoginRequiredMixin, DeleteView):
    model = SavingsGoal
    success_url = reverse_lazy('goal-list')
    def get_queryset(self): return SavingsGoal.objects.filter(user=self.request.user)

    def delete(self, request, *args, **kwargs):
        from django.contrib import messages
        from django.utils.translation import gettext as _
        messages.success(self.request, _("Savings goal deleted successfully."))
        return super().delete(request, *args, **kwargs)

class SavingsGoalDetailView(LoginRequiredMixin, View):
    template_name = 'expenses/goal_detail.html'
    def get(self, request, pk):
        goal = get_object_or_404(SavingsGoal, pk=pk, user=request.user)
        is_locked = False
        if request.user.is_authenticated:
            profile = request.user.profile
            from finance_tracker.plans import get_limit
            limit = get_limit(profile.active_tier, 'savings_goals')
            if limit != -1:
                 goals = list(SavingsGoal.objects.filter(user=request.user).order_by('created_at', 'id'))
                 is_locked = (goal in goals and goals.index(goal) >= limit)
        return render(request, self.template_name, {'goal': goal, 'is_locked': is_locked, 'contributions': goal.contributions.all().order_by('-date'), 'form': GoalContributionForm(user=request.user)})
    def post(self, request, pk):
        goal = get_object_or_404(SavingsGoal, pk=pk, user=request.user)
        if request.content_type == 'application/json':
            try:
                if json.loads(request.body).get('clear_confetti'):
                    request.session.pop('trigger_confetti', None)
                    return JsonResponse({'success': True})
            except:
                pass
        # Lock check for POST contributions
        profile = request.user.profile
        
        limit = get_limit(profile.active_tier, 'savings_goals')
        if limit != -1:
             goals = list(SavingsGoal.objects.filter(user=request.user).order_by('created_at', 'id'))
             if goal in goals and goals.index(goal) >= limit:
                 messages.error(request, _("This goal is locked."))
                 return redirect('goal-list')

        form = GoalContributionForm(request.POST, user=request.user)

        if form.is_valid():
            c = form.save(commit=False); c.goal = goal; c.save()
            messages.success(request, _("Contribution added successfully!"))
            request.session['trigger_confetti'] = True
            return redirect('goal-detail', pk=goal.pk)

        return render(request, self.template_name, {'goal': goal, 'form': form})

class GoalContributionUpdateView(LoginRequiredMixin, UpdateView):
    model = GoalContribution
    form_class = GoalContributionForm
    template_name = 'expenses/contribution_form.html'
    
    def get_queryset(self):
        return GoalContribution.objects.filter(goal__user=self.request.user)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['user'] = self.request.user
        return kwargs

    def get_success_url(self):
        return reverse_lazy('goal-detail', kwargs={'pk': self.object.goal.pk})

    def form_valid(self, form):
        messages.success(self.request, _("Contribution updated successfully!"))
        return super().form_valid(form)

class GoalContributionDeleteView(LoginRequiredMixin, DeleteView):
    model = GoalContribution
    
    def get_queryset(self):
        return GoalContribution.objects.filter(goal__user=self.request.user)

    def get_success_url(self):
        next_url = self.request.GET.get('next') or self.request.POST.get('next')
        if next_url:
            return next_url
        return reverse_lazy('goal-detail', kwargs={'pk': self.object.goal.pk})

    def delete(self, request, *args, **kwargs):
        messages.success(self.request, _("Contribution deleted successfully!"))
        return super().delete(request, *args, **kwargs)
