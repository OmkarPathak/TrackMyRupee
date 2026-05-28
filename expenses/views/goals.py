import json
import math
from datetime import timedelta
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Sum
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.core.paginator import Paginator
from django.utils import timezone
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

    def _is_locked(self, user, goal):
        is_locked = False
        if user.is_authenticated:
            profile = user.profile
            limit = get_limit(profile.active_tier, 'savings_goals')
            if limit != -1:
                goals = list(SavingsGoal.objects.filter(user=user).order_by('created_at', 'id'))
                is_locked = goal in goals and goals.index(goal) >= limit
        return is_locked

    def _get_estimated_completion(self, goal, contributions_qs):
        if goal.is_completed:
            return {
                'estimated_completion_date': timezone.localdate(),
                'estimated_days_left': 0,
                'avg_daily_contribution': Decimal('0.00'),
            }

        if not contributions_qs.exists() or goal.target_amount <= goal.current_amount:
            return {
                'estimated_completion_date': None,
                'estimated_days_left': None,
                'avg_daily_contribution': Decimal('0.00'),
            }

        first_contribution = contributions_qs.order_by('date', 'id').first()
        today = timezone.localdate()
        days_elapsed = max((today - first_contribution.date).days, 1)

        total_contributed = contributions_qs.aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
        avg_daily = total_contributed / Decimal(days_elapsed)

        remaining_amount = goal.target_amount - goal.current_amount
        if avg_daily <= 0 or remaining_amount <= 0:
            return {
                'estimated_completion_date': None,
                'estimated_days_left': None,
                'avg_daily_contribution': avg_daily,
            }

        days_left = max(math.ceil(float(remaining_amount / avg_daily)), 1)
        return {
            'estimated_completion_date': today + timedelta(days=days_left),
            'estimated_days_left': days_left,
            'avg_daily_contribution': avg_daily,
        }

    def _build_trend_data(self, goal, contributions_qs, estimated_days_left=None):
        today = timezone.localdate()
        labels = []
        values = []

        if not contributions_qs.exists():
            return {
                'type': 'daily',
                'labels': labels,
                'values': values,
                'projection_values': [],
            }

        first_contribution = contributions_qs.order_by('date', 'id').first()
        span_days = (today - first_contribution.date).days
        is_monthly = span_days > 45

        if is_monthly:
            month_start = today.replace(day=1)
            for _ in range(11):
                month_start = (month_start - timedelta(days=1)).replace(day=1)

            monthly_totals = {}
            for contribution in contributions_qs:
                month_key = contribution.date.strftime('%Y-%m')
                monthly_totals[month_key] = monthly_totals.get(month_key, Decimal('0.00')) + contribution.amount

            initial_total = contributions_qs.filter(date__lt=month_start).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
            running_total = initial_total

            current_month = month_start
            for _ in range(12):
                month_key = current_month.strftime('%Y-%m')
                running_total += monthly_totals.get(month_key, Decimal('0.00'))
                labels.append(current_month.strftime('%b'))
                values.append(float(running_total))
                next_month = (current_month + timedelta(days=32)).replace(day=1)
                current_month = next_month

            return self._attach_projection(
                trend_type='monthly',
                labels=labels,
                values=values,
                estimated_days_left=estimated_days_left,
                goal=goal,
                today=today,
            )

        start_date = today - timedelta(days=29)
        daily_totals = {}
        for contribution in contributions_qs:
            daily_totals[contribution.date] = daily_totals.get(contribution.date, Decimal('0.00')) + contribution.amount

        initial_total = contributions_qs.filter(date__lt=start_date).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
        running_total = initial_total
        current_day = start_date

        for _ in range(30):
            running_total += daily_totals.get(current_day, Decimal('0.00'))
            labels.append(current_day.strftime('%d %b'))
            values.append(float(running_total))
            current_day += timedelta(days=1)

        return self._attach_projection(
            trend_type='daily',
            labels=labels,
            values=values,
            estimated_days_left=estimated_days_left,
            goal=goal,
            today=today,
        )

    def _attach_projection(self, trend_type, labels, values, estimated_days_left, goal, today):
        if not values:
            return {
                'type': trend_type,
                'labels': labels,
                'values': values,
                'projection_values': [],
            }

        extended_labels = list(labels)
        actual_values = list(values)
        projection_values = [None] * max(len(values) - 1, 0) + [values[-1]]

        can_project = (
            estimated_days_left
            and estimated_days_left > 0
            and not goal.is_completed
            and goal.target_amount > goal.current_amount
        )

        if not can_project:
            return {
                'type': trend_type,
                'labels': extended_labels,
                'values': actual_values,
                'projection_values': projection_values,
            }

        target_value = float(goal.target_amount)
        start_value = float(values[-1])
        if trend_type == 'monthly':
            projection_points = min(max(math.ceil(estimated_days_left / 30), 1), 6)
            month_cursor = today.replace(day=1)
            for _ in range(projection_points):
                month_cursor = (month_cursor + timedelta(days=32)).replace(day=1)
                extended_labels.append(month_cursor.strftime('%b'))
        else:
            projection_points = min(max(int(estimated_days_left), 1), 30)
            for offset in range(1, projection_points + 1):
                extended_labels.append((today + timedelta(days=offset)).strftime('%d %b'))

        actual_values.extend([None] * projection_points)
        for idx in range(1, projection_points + 1):
            progress = idx / projection_points
            projected_value = start_value + ((target_value - start_value) * progress)
            projection_values.append(round(projected_value, 2))

        return {
            'type': trend_type,
            'labels': extended_labels,
            'values': actual_values,
            'projection_values': projection_values,
        }

    def _get_context_data(self, request, goal, form=None):
        contributions_qs = goal.contributions.select_related('account').all().order_by('-date', '-id')
        is_locked = self._is_locked(request.user, goal)

        search_query = (request.GET.get('q') or '').strip()
        if search_query:
            contributions_qs = contributions_qs.filter(account__name__icontains=search_query)

        filtered_total = contributions_qs.aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
        total_count = contributions_qs.count()
        paginator = Paginator(contributions_qs, 10)
        page_number = request.GET.get('page')
        page_obj = paginator.get_page(page_number)
        contributions_page = page_obj.object_list

        remaining_amount = goal.target_amount - goal.current_amount
        if remaining_amount < Decimal('0.00'):
            remaining_amount = Decimal('0.00')

        all_contributions = goal.contributions.select_related('account').all()
        estimate_data = self._get_estimated_completion(goal, all_contributions)
        trend_data = self._build_trend_data(
            goal,
            all_contributions,
            estimated_days_left=estimate_data.get('estimated_days_left'),
        )

        return {
            'goal': goal,
            'is_locked': is_locked,
            'contributions': contributions_page,
            'contribution_count': total_count,
            'page_obj': page_obj,
            'is_paginated': page_obj.has_other_pages(),
            'paginator': paginator,
            'remaining_amount': remaining_amount,
            'filtered_total': filtered_total,
            'search_query': search_query,
            'trend_data': trend_data,
            'form': form or GoalContributionForm(user=request.user),
            **estimate_data,
        }

    def get(self, request, pk):
        goal = get_object_or_404(SavingsGoal, pk=pk, user=request.user)
        return render(request, self.template_name, self._get_context_data(request, goal))

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

        return render(request, self.template_name, self._get_context_data(request, goal, form=form))

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
