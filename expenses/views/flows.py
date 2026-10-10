from __future__ import annotations

import json
import uuid

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import ValidationError
from django.http import Http404, HttpResponse, HttpResponseBadRequest
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views.generic import TemplateView, View

from ..flows import *  # noqa: F403
from ..flows.base import FlowSnapshot
from ..flows.registry import FlowRegistry



class FlowBaseView(LoginRequiredMixin):
    flow = None

    def _idem_session_key(self):
        return f'flow_idem_{self.kwargs["key"]}'

    def dispatch(self, request, *args, **kwargs):
        key = kwargs.get('key')
        try:
            self.flow = FlowRegistry.get(key)
        except KeyError as exc:
            raise Http404 from exc
        return super().dispatch(request, *args, **kwargs)

    def get_idempotency_key(self):
        session_key = self._idem_session_key()
        value = self.request.session.get(session_key)
        if not value:
            value = str(uuid.uuid4())
            self.request.session[session_key] = value
            self.request.session.modified = True
        return value

    def rotate_idempotency_key(self):
        self.request.session[self._idem_session_key()] = str(uuid.uuid4())
        self.request.session.modified = True

    def get_form(self, data=None):
        return self.flow.form_class(data=data, user=self.request.user)

    def get_preview_data(self, form):
        if not form:
            return {}

        data = {}
        cleaned_data = getattr(form, 'cleaned_data', {})
        for k, v in cleaned_data.items():
            if v is not None:
                data[k] = v

        if form.is_bound and hasattr(form, 'data'):
            for field_name, field in form.fields.items():
                if field_name not in data or data[field_name] is None:
                    raw_val = form.data.get(field_name)
                    if raw_val is not None and raw_val != '':
                        try:
                            converted = field.to_python(raw_val)
                            if converted is not None:
                                data[field_name] = converted
                        except Exception:
                            # Unparseable input stays out of the review; the form shows its error.
                            continue
        return data

    def render_review(self, request, form, preview, status=200):
        preview_data = self.get_preview_data(form)
        review = self.flow.review_context(request.user, form, preview_data)
        return render(
            request,
            'flows/partials/_review.html',
            {
                'flow': self.flow,
                'form': form,
                'preview': preview,
                'review': review,
                'idempotency_key': self.get_idempotency_key(),
            },
            status=status,
        )




class FlowLandingView(LoginRequiredMixin, TemplateView):
    template_name = 'flows/landing.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        all_flows = FlowRegistry.all()

        categories = [
            {'key': 'all', 'label': _('All')},
            {'key': 'debt', 'label': _('Debt')},
            {'key': 'income', 'label': _('Income')},
            {'key': 'bills', 'label': _('Bills')},
            {'key': 'savings', 'label': _('Savings & investments')},
            {'key': 'assets', 'label': _('Assets')},
        ]

        curated_keys = {'salary', 'rentbill', 'creditcard', 'loan', 'sip'}
        snapshot = FlowSnapshot.for_user(self.request.user)

        flow_items = []
        for key, flow in all_flows.items():
            is_configured = flow.is_configured(self.request.user, snapshot)
            flow_items.append({
                'key': flow.key,
                'category': flow.category,
                'icon': flow.icon,
                'label': str(flow.label),
                'title': str(flow.title),
                'short_title': str(flow.label),
                'description': str(flow.description),
                'tags': [str(t) for t in flow.tags],
                'estimated_time': str(flow.estimated_time),
                'creates': [str(c) for c in flow.creates],
                'is_configured': is_configured,
                'is_curated': flow.key in curated_keys,
                'setup_url': reverse('flow-detail', kwargs={'key': flow.key}),
                'edit_url': flow.get_edit_url(self.request.user),
            })

        configured_count = sum(1 for f in flow_items if f['is_configured'])
        total_count = len(flow_items)
        pct_configured = round((configured_count / total_count) * 100) if total_count > 0 else 0

        todo_flows = [f for f in flow_items if not f['is_configured']]
        configured_flows = [f for f in flow_items if f['is_configured']]

        flows_json = json.dumps(flow_items)
        legacy_categories = FlowRegistry.by_category()

        context.update({
            'categories': categories,
            'legacy_categories': legacy_categories,
            'flows': flow_items,
            'todo_flows': todo_flows,
            'configured_flows': configured_flows,
            'configured_count': configured_count,
            'total_count': total_count,
            'pct_configured': pct_configured,
            'flows_json': flows_json,
            'initial_category': 'all',
            'initial_flow_key': flow_items[0]['key'] if flow_items else 'loan',
        })
        return context


class FlowDetailView(FlowBaseView, View):
    template_name = 'flows/detail.html'

    def get(self, request, key):
        form = self.get_form()
        preview_data = self.get_preview_data(form)
        return render(
            request,
            self.template_name,
            {
                'flow': self.flow,
                'form': form,
                'idempotency_key': self.get_idempotency_key(),
                'preview': None,
                'review': self.flow.review_context(request.user, form, preview_data),
            },
        )

    def post(self, request, key):
        form = self.get_form(data=request.POST)
        preview_data = self.get_preview_data(form)
        try:
            preview = self.flow.preview(request.user, preview_data)
        except Exception:
            preview = {'headline': 0, 'bullets': []}

        review = self.flow.review_context(request.user, form, preview_data)
        return render(
            request,
            self.template_name,
            {
                'flow': self.flow,
                'form': form,
                'idempotency_key': self.get_idempotency_key(),
                'preview': preview,
                'review': review,
            },
        )


class FlowPreviewView(FlowBaseView, View):
    def post(self, request, key):
        form = self.get_form(data=request.POST)
        preview_data = self.get_preview_data(form)
        try:
            preview = self.flow.preview(request.user, preview_data)
        except Exception:
            preview = {'headline': 0, 'bullets': []}

        return self.render_review(request, form, preview, status=200)


class FlowCommitView(FlowBaseView, View):
    def post(self, request, key):
        form = self.get_form(data=request.POST)
        if not form.is_valid():
            preview_data = self.get_preview_data(form)
            try:
                preview = self.flow.preview(request.user, preview_data)
            except Exception:
                preview = {'headline': 0, 'bullets': []}
            return self.render_review(request, form, preview, status=200)

        raw_key = request.POST.get('idempotency_key') or ''
        try:
            idem_key = uuid.UUID(raw_key)
        except ValueError:
            idem_key = uuid.UUID(self.get_idempotency_key())
        try:
            result = self.flow.commit(request.user, form.cleaned_data, idem_key)
            if form.cleaned_data.get('create_historical_entries'):
                from .mixins import process_user_recurring_transactions
                process_user_recurring_transactions(request.user, force=True)
        except ValidationError as exc:
            preview = self.flow.preview(request.user, form.cleaned_data)
            preview['warnings'] = list(exc.messages)
            return self.render_review(request, form, preview, status=200)

        # Allow subsequent runs of the same flow in the same session.
        self.rotate_idempotency_key()

        messages.success(request, f'{self.flow.label} created successfully.')
        if request.headers.get('HX-Request', '').lower() == 'true':
            response = HttpResponse(status=204)
            response['HX-Redirect'] = reverse('home')
            return response
        return redirect('home')

