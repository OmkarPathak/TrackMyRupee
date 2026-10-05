from __future__ import annotations

import uuid

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import ValidationError
from django.http import Http404, HttpResponse, HttpResponseBadRequest
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.generic import TemplateView, View

from ..flows import *  # noqa: F403
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

    def render_review(self, request, form, preview, status=200):
        review = self.flow.review_context(request.user, form, form.cleaned_data)
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

    @staticmethod
    def collect_form_warnings(form):
        warnings = []
        for field_name, errors in form.errors.items():
            if field_name == '__all__':
                for error in errors:
                    warnings.append(str(error))
                continue

            label = form.fields.get(field_name).label if form.fields.get(field_name) else field_name
            for error in errors:
                warnings.append(f'{label}: {error}')
        return warnings


class FlowLandingView(LoginRequiredMixin, TemplateView):
    template_name = 'flows/landing.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        categories = FlowRegistry.by_category()
        first_category = next(iter(categories.keys())) if categories else None
        first_pill = categories[first_category]['flows'][0].key if first_category and categories[first_category]['flows'] else None
        context.update(
            {
                'categories': categories,
                'initial_category': first_category,
                'initial_pill': first_pill,
            }
        )
        return context


class FlowDetailView(FlowBaseView, View):
    template_name = 'flows/detail.html'

    def get(self, request, key):
        form = self.get_form()
        return render(request, self.template_name, {'flow': self.flow, 'form': form, 'idempotency_key': self.get_idempotency_key(), 'preview': None, 'review': self.flow.review_context(request.user, form, {})})

    def post(self, request, key):
        form = self.get_form(data=request.POST)
        if not form.is_valid():
            return render(request, self.template_name, {'flow': self.flow, 'form': form, 'idempotency_key': self.get_idempotency_key(), 'preview': None, 'review': self.flow.review_context(request.user, form, form.cleaned_data if form.is_bound else {})})

        preview = self.flow.preview(request.user, form.cleaned_data)
        return render(request, self.template_name, {'flow': self.flow, 'form': form, 'idempotency_key': self.get_idempotency_key(), 'preview': preview, 'review': self.flow.review_context(request.user, form, form.cleaned_data)})


class FlowPreviewView(FlowBaseView, View):
    def post(self, request, key):
        form = self.get_form(data=request.POST)
        if not form.is_valid():
            return self.render_review(request, form, {'headline': 0, 'bullets': [], 'warnings': self.collect_form_warnings(form)}, status=200)

        preview = self.flow.preview(request.user, form.cleaned_data)
        return self.render_review(request, form, preview)


class FlowCommitView(FlowBaseView, View):
    def post(self, request, key):
        form = self.get_form(data=request.POST)
        if not form.is_valid():
            return self.render_review(request, form, {'headline': 0, 'bullets': [], 'warnings': self.collect_form_warnings(form)}, status=200)

        raw_key = request.POST.get('idempotency_key') or ''
        try:
            idem_key = uuid.UUID(raw_key)
        except ValueError:
            idem_key = uuid.UUID(self.get_idempotency_key())
        try:
            result = self.flow.commit(request.user, form.cleaned_data, idem_key)
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
