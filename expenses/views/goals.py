import json
import math
from datetime import timedelta
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.paginator import Paginator
from django.db.models import Sum
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse_lazy
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.generic import CreateView, DeleteView, ListView, UpdateView, View

from expenses.views.utils import get_safe_redirect_url
from finance_tracker.plans import get_limit
from ..posthog_utils import ph_capture

from ..forms import GoalContributionForm, SavingsGoalForm
from ..models import GoalContribution, SavingsGoal
from .mixins import UUIDOrIntLookupMixin
from .utils import (
    get_object_by_uuid_or_pk,
    get_redirect_pk_or_uuid,
    redirect_to_uuid_url_if_needed,
)


class SavingsGoalListView(LoginRequiredMixin, ListView):
    model = SavingsGoal
    template_name = 'expenses/goal_list.html'
    context_object_name = 'ignored'

    def get_queryset(self):
        return SavingsGoal.objects.filter(user=self.request.user).prefetch_related('contributions').order_by('created_at', 'id')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        all_goals = list(context.get('object_list') or self.get_queryset())
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
        response = super().form_valid(form)
        ph_capture(self.request.user, 'goal_created', {'target_amount': str(self.object.target_amount), 'has_target_date': bool(self.object.target_date)})
        return response

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs(); kwargs['user'] = self.request.user
        return kwargs

class SavingsGoalUpdateView(LoginRequiredMixin, UUIDOrIntLookupMixin, UpdateView):
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
        response = super().form_valid(form)
        ph_capture(self.request.user, 'goal_updated', {})
        return response

class SavingsGoalDeleteView(LoginRequiredMixin, UUIDOrIntLookupMixin, DeleteView):
    model = SavingsGoal
    success_url = reverse_lazy('goal-list')
    def get_queryset(self): return SavingsGoal.objects.filter(user=self.request.user)

    def delete(self, request, *args, **kwargs):
        from django.contrib import messages
        from django.utils.translation import gettext as _
        messages.success(self.request, _("Savings goal deleted successfully."))
        response = super().delete(request, *args, **kwargs)
        ph_capture(self.request.user, 'goal_deleted', {})
        return response

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

        contributions_list = list(contributions_qs)
        if not contributions_list or goal.target_amount <= goal.current_amount:
            return {
                'estimated_completion_date': None,
                'estimated_days_left': None,
                'avg_daily_contribution': Decimal('0.00'),
            }

        first_contribution = min(contributions_list, key=lambda c: (c.date, c.id))
        today = timezone.localdate()
        days_elapsed = max((today - first_contribution.date).days, 1)

        total_contributed = sum(c.amount for c in contributions_list)
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

        contributions_list = list(contributions_qs)
        if not contributions_list:
            return {
                'type': 'daily',
                'labels': labels,
                'values': values,
                'projection_values': [],
            }

        first_contribution = min(contributions_list, key=lambda c: (c.date, c.id))
        span_days = (today - first_contribution.date).days
        is_monthly = span_days > 45

        if is_monthly:
            month_start = today.replace(day=1)
            for _ in range(11):
                month_start = (month_start - timedelta(days=1)).replace(day=1)

            monthly_totals = {}
            for contribution in contributions_list:
                month_key = contribution.date.strftime('%Y-%m')
                monthly_totals[month_key] = monthly_totals.get(month_key, Decimal('0.00')) + contribution.amount

            initial_total = sum(c.amount for c in contributions_list if c.date < month_start)
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
        for contribution in contributions_list:
            daily_totals[contribution.date] = daily_totals.get(contribution.date, Decimal('0.00')) + contribution.amount

        initial_total = sum(c.amount for c in contributions_list if c.date < start_date)
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
        all_contributions = list(goal.contributions.select_related('account').all())
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

        estimate_data = self._get_estimated_completion(goal, all_contributions)
        trend_data = self._build_trend_data(
            goal,
            all_contributions,
            estimated_days_left=estimate_data.get('estimated_days_left'),
        )

        estimated_completion_date = estimate_data.get('estimated_completion_date')
        completion_status = None
        needed_per_month = None
        needed_gap = None
        is_on_track = True

        if goal.target_date and not goal.is_completed:
            today = timezone.localdate()
            days_left = (goal.target_date - today).days

            # Calculate completion status / early-late framing
            if estimated_completion_date:
                if estimated_completion_date <= goal.target_date:
                    diff_days = (goal.target_date - estimated_completion_date).days
                    if diff_days >= 30:
                        months = (goal.target_date.year - estimated_completion_date.year) * 12 + goal.target_date.month - estimated_completion_date.month
                        if months > 0:
                            text = _("%(months)d months early!") % {'months': months}
                        else:
                            text = _("%(days)d days early!") % {'days': diff_days}
                    else:
                        if diff_days > 0:
                            text = _("%(days)d days early!") % {'days': diff_days}
                        else:
                            text = _("on time!")
                    completion_status = {
                        'text': text,
                        'class': 'success',
                        'is_early': True
                    }
                else:
                    diff_days = (estimated_completion_date - goal.target_date).days
                    if diff_days >= 30:
                        months = (estimated_completion_date.year - goal.target_date.year) * 12 + estimated_completion_date.month - goal.target_date.month
                        if months > 0:
                            text = _("%(months)d months late") % {'months': months}
                        else:
                            text = _("%(days)d days late") % {'days': diff_days}
                    else:
                        text = _("%(days)d days late") % {'days': diff_days}
                    completion_status = {
                        'text': text,
                        'class': 'amber',
                        'is_early': False
                    }

            # Calculate needed monthly contribution
            if days_left > 0:
                months_left = Decimal(str(days_left)) / Decimal('30.437')
                months_left = max(months_left, Decimal('0.1'))
                needed_per_month = round(remaining_amount / months_left, 2)

                # Check if on track
                current_monthly_pace = estimate_data.get('avg_daily_contribution', Decimal('0.00')) * Decimal('30.437')
                if estimated_completion_date and estimated_completion_date <= goal.target_date:
                    is_on_track = True
                else:
                    if current_monthly_pace > 0 and needed_per_month > current_monthly_pace:
                        is_on_track = False
                        needed_gap = round(needed_per_month - current_monthly_pace, 2)
                    elif current_monthly_pace == 0:
                        is_on_track = False
                        needed_gap = needed_per_month
            else:
                needed_per_month = remaining_amount
                is_on_track = False
                needed_gap = remaining_amount

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
            'completion_status': completion_status,
            'needed_per_month': needed_per_month,
            'needed_gap': needed_gap,
            'is_on_track': is_on_track,
            **estimate_data,
        }

    def get(self, request, pk):
        goal = get_object_by_uuid_or_pk(SavingsGoal, pk, user=request.user)
        redirect_response = redirect_to_uuid_url_if_needed(request, goal)
        if redirect_response:
            return redirect_response
        return render(request, self.template_name, self._get_context_data(request, goal))

    def post(self, request, pk):
        goal = get_object_by_uuid_or_pk(SavingsGoal, pk, user=request.user)
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
            ph_capture(request.user, 'goal_contribution_added', {'amount': str(c.amount)})
            return redirect('goal-detail', pk=get_redirect_pk_or_uuid(goal))

        return render(request, self.template_name, self._get_context_data(request, goal, form=form))
 
class GoalContributionUpdateView(LoginRequiredMixin, UUIDOrIntLookupMixin, UpdateView):
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
        return reverse_lazy('goal-detail', kwargs={'pk': get_redirect_pk_or_uuid(self.object.goal)})

    def form_valid(self, form):
        messages.success(self.request, _("Contribution updated successfully!"))
        response = super().form_valid(form)
        ph_capture(self.request.user, 'goal_contribution_updated', {'amount': str(self.object.amount)})
        return response

class GoalContributionDeleteView(LoginRequiredMixin, UUIDOrIntLookupMixin, DeleteView):
    model = GoalContribution
    
    def get_queryset(self):
        return GoalContribution.objects.filter(goal__user=self.request.user)

    def get_success_url(self):
        next_url = self.request.GET.get('next') or self.request.POST.get('next')
        fallback = reverse_lazy('goal-detail', kwargs={'pk': get_redirect_pk_or_uuid(self.object.goal)})
        if next_url:
            return get_safe_redirect_url(self.request, next_url, fallback)
        return fallback

    def delete(self, request, *args, **kwargs):
        messages.success(self.request, _("Contribution deleted successfully!"))
        response = super().delete(request, *args, **kwargs)
        ph_capture(self.request.user, 'goal_contribution_deleted', {})
        return response
