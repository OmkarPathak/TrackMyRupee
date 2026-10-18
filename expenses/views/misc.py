import base64
import calendar
import csv
import io
import json
import re
import logging
from itertools import islice
from datetime import date, datetime, timedelta
from decimal import Decimal

from django.conf import settings

import dns.flags
import dns.message
import dns.rdata
import dns.rdataclass
import dns.rdatatype
import openpyxl
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.db.models import Case, Count, Q, Sum, When
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.utils.formats import date_format
from django.utils import timezone
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy
from django.views.decorators.csrf import csrf_exempt
from django.views.generic import TemplateView, View

import logging

from ..account_types import investment_codes
from ..importing import MAX_IMPORT_BYTES, MAX_IMPORT_ROWS, TooManyRows
from ..forms import ContactForm
from ..models import (
    CURRENCY_CHOICES,
    Account,
    Expense,
    Income,
    RecurringTransaction,
    Transfer,
)
from ..posthog_utils import ph_capture
from .mixins import HtmxPartialTemplateMixin

logger = logging.getLogger(__name__)

# Iteration safety cap; should stay consistent with MAX_CATCHUP_PER_RUN in expenses/views/mixins.py
MAX_CATCHUP_PER_RUN = 100


class CalendarView(HtmxPartialTemplateMixin, LoginRequiredMixin, TemplateView):
    template_name = 'expenses/calendar.html'
    htmx_template_name = 'expenses/partials/_calendar_content.html'

    def get_context_data(self, **kwargs):
        from .. import calendar_data

        context = super().get_context_data(**kwargs)
        today = timezone.localdate()
        year, month = calendar_data.clamp_month(
            self.kwargs.get('year', today.year), self.kwargs.get('month', today.month), today)
        prev_year, prev_month = calendar_data.shift_month(year, month, -1)
        next_year, next_month = calendar_data.shift_month(year, month, 1)
        search_query = self.request.GET.get('search', '').strip()

        weeks = calendar_data.build_month(self.request.user, year, month, today, search_query)
        context.update({
            'calendar_data': weeks,
            'current_year': year,
            'current_month': month,
            'month_name': date_format(date(year, month, 1), 'F'),
            'prev_year': prev_year, 'prev_month': prev_month,
            'next_year': next_year, 'next_month': next_month,
            'search_query': search_query,
            'today': today,
            # the day opened in the detail panel: today when viewing this month, else nothing yet
            'selected_iso': today.isoformat() if (year, month) == (today.year, today.month) else '',
        })
        return context


class CalendarDayView(LoginRequiredMixin, TemplateView):
    """The detail panel of one day (what happened, what is due, what is coming up)."""
    template_name = 'expenses/partials/_calendar_day.html'

    def get_context_data(self, **kwargs):
        from .. import calendar_data

        context = super().get_context_data(**kwargs)
        today = timezone.localdate()
        try:
            day = date(int(self.kwargs['year']), int(self.kwargs['month']), int(self.kwargs['day']))
        except (ValueError, OverflowError):
            raise Http404
        if not (calendar_data.MIN_YEAR <= day.year <= today.year + calendar_data.MAX_YEARS_AHEAD):
            raise Http404
        context.update(calendar_data.day_detail(
            self.request.user, day, today, self.request.GET.get('search', '').strip()))
        context['search_query'] = self.request.GET.get('search', '').strip()
        return context


# Limits on one import. A file is read whole into memory, so it must be small; the row cap is far above what
# a month or a year of personal statements holds.
MAX_XLSX_UNCOMPRESSED_BYTES = 30 * 1024 * 1024   # a spreadsheet is a zip: refuse one that expands enormously


def _xlsx_expands_too_much(uploaded_file):
    """True for a spreadsheet whose contents would unpack far beyond its upload size (a zip bomb)."""
    import zipfile
    uploaded_file.seek(0)
    try:
        with zipfile.ZipFile(uploaded_file) as archive:
            return sum(info.file_size for info in archive.infolist()) > MAX_XLSX_UNCOMPRESSED_BYTES
    except zipfile.BadZipFile:
        return False        # not a real .xlsx: the fallbacks in importing.parsing deal with old .xls, HTML and CSV
    finally:
        uploaded_file.seek(0)


_IMPORT_NOTES = {
    'assumed_positive_are_expenses': gettext_lazy(
        "This file has a single amount column with both positive and negative values, so positive amounts were "
        "treated as expenses and negative ones were skipped as credits. If that is the wrong way round, delete "
        "these expenses and import again with a Debit/Credit or Balance column."),
    'assumed_negative_are_expenses': gettext_lazy(
        "This file has a single amount column with both positive and negative values, so negative amounts were "
        "treated as expenses and positive ones were skipped as credits. If that is the wrong way round, delete "
        "these expenses and import again with a Debit/Credit or Balance column."),
}


class _Upload:
    """What a POST to the upload or preview view carries, checked: the file, its account and currency, or an error."""
    def __init__(self, **kw):
        self.error = None
        self.file = self.file_name = self.currency = self.account = None
        self.selected_account_id = None
        self.__dict__.update(kw)


def read_upload(request):
    """Validate an uploaded statement and its account and currency (shared by the preview and the import)."""
    accounts_qs = Account.objects.filter(user=request.user, is_active=True)
    uploaded_file = request.FILES.get('file')
    selected_account_id = request.POST.get('account')
    upload = _Upload(selected_account_id=selected_account_id)
    if not uploaded_file:
        upload.error = _("Please choose a file to upload.")
        return upload
    file_name = (uploaded_file.name or '').lower()
    if not file_name.endswith(('.xlsx', '.xls', '.csv')):
        upload.error = _("Please upload an Excel (.xlsx) or CSV (.csv) file.")
    elif uploaded_file.size > MAX_IMPORT_BYTES:
        upload.error = _("This file is too large to import (the limit is 5 MB). Split it into smaller files.")
    elif file_name.endswith(('.xlsx', '.xls')) and _xlsx_expands_too_much(uploaded_file):
        upload.error = _("This spreadsheet is too large to import. Split it into smaller files.")
    if upload.error:
        return upload

    if not selected_account_id and accounts_qs.exists():
        selected_account_id = str(accounts_qs.order_by('name').first().id)
        upload.selected_account_id = selected_account_id
    if not selected_account_id:
        upload.error = _("Please create an account before importing expenses.")
        upload.selected_account_id = None
        return upload
    try:
        upload.account = accounts_qs.get(pk=selected_account_id)
    except (Account.DoesNotExist, ValueError):
        upload.error = _("Selected account is invalid. Please choose one of your active accounts.")
        upload.selected_account_id = None
        return upload
    upload.file, upload.file_name = uploaded_file, file_name
    upload.currency = request.POST.get('currency', request.user.profile.currency)
    return upload


@login_required
def upload_view(request):
    """Import expenses from an Excel or CSV statement (the work is in expenses.importing)."""
    results = None
    accounts_qs = Account.objects.filter(user=request.user, is_active=True).order_by('name')

    def page(selected_account_id):
        return render(request, 'upload.html', {
            'results': results,
            'currencies': CURRENCY_CHOICES,
            'default_currency': request.user.profile.currency,
            'accounts': accounts_qs,
            'selected_account_id': selected_account_id,
        })

    if request.method == 'POST' and request.FILES.get('file'):
        upload = read_upload(request)
        if upload.error:
            messages.error(request, upload.error)
            return page(upload.selected_account_id)

        # One import at a time per user: a double click would otherwise run twice and import every row twice.
        upload_lock = f'expense_import_lock_{request.user.id}'
        if not cache.add(upload_lock, 1, timeout=300):
            messages.error(request, _("An import is already running. Wait for it to finish, then upload again."))
            return page(upload.selected_account_id)

        from finance_tracker.plans import get_limit as _plan_limit
        from ..importing import ImportFileError, analyse, commit
        from . import predict_category_ai
        from .imports import options_from_post

        options, excluded, included, reviewed = options_from_post(request.POST)
        try:
            analysis = analyse(
                user=request.user,
                uploaded_file=upload.file,
                file_name=upload.file_name,
                account=upload.account,
                currency=upload.currency,
                predict_category=predict_category_ai,
                plan_cap=_plan_limit(request.user.profile.active_tier, 'expenses_per_month'),
                max_rows=MAX_IMPORT_ROWS,
                options=options,
                excluded=excluded,
                included=included,
                reviewed=reviewed,
            )
            posted_hash = request.POST.get('file_hash', '')
            if analysis is None:
                messages.warning(request, _("Could not detect required columns (Date, Amount, Description). Please check your file."))
            elif posted_hash and posted_hash != analysis.file_hash:
                messages.error(request, _("The file you chose is not the one you previewed. Preview it again before importing."))
            else:
                summary = commit(analysis, user=request.user, account=upload.account,
                                 currency=upload.currency, file_name=upload.file_name)
                results = summary
                for note in summary['notes']:
                    messages.warning(request, _IMPORT_NOTES[note])
                if summary['created_count'] > 0:
                    messages.success(request, _("Processing complete! See summary below."))
                    ph_capture(request.user, 'expenses_imported', {
                        'created_count': summary['created_count'],
                        'duplicate_count': summary['duplicate_count'],
                        'error_count': summary['error_count'],
                        'total_rows': summary['total_rows'],
                    })
                else:
                    messages.info(request, _("Processing complete. No new expenses were added."))
        except TooManyRows as error:
            messages.error(request, str(error))
        except ImportFileError as error:
            messages.error(request, str(error))
        except Exception:
            logging.getLogger(__name__).exception("Expense import failed for user %s", request.user.id)
            messages.error(request, _("We could not read this file. Check that it is a valid Excel or CSV file with Date, "
                                      "Amount and Description columns, then try again."))
        finally:
            cache.delete(upload_lock)

    return page(request.POST.get('account') if request.method == 'POST' else None)


def ping(request):
    return JsonResponse({'status': 'ok'})

@login_required
def predict_category_view(request):
    # Important: use the one from the package to allow mocking in tests
    from . import predict_category_ai
    
    if not request.user.profile.has_ai_access:
        return JsonResponse({'category': None, 'error': _('AI Insights is a paid feature.')}, status=403)

    description = request.GET.get('description', '').strip()
    if not description:
        return JsonResponse({'category': None})
    
    category = predict_category_ai(description, user=request.user) or 'Food'
    return JsonResponse({'success': True, 'category': category})

class ContactView(View):
    template_name = 'contact.html'
    RATE_LIMIT_HOURLY = 3
    RATE_LIMIT_DAILY = 10
    MIN_MESSAGE_LENGTH = 10
    SPAM_KEYWORDS = ['viagra', 'casino', 'lottery', 'prize', 'make money fast']
    DISPOSABLE_DOMAINS = ['tempmail.com', 'guerrillamail.com']

    def get(self, request):
        form = ContactForm()
        return render(request, self.template_name, {'form': form})

    def post(self, request):
        form = ContactForm(request.POST)
        if not form.is_valid():
            return render(request, self.template_name, {'form': form})
        
        data = form.cleaned_data
        if data.get('website'): # Honeypot
            messages.success(request, _("Your message has been sent!"))
            return redirect('contact')
            
        ip = self._get_client_ip(request)
        allowed, rate_limit_msg = self._check_rate_limit(ip)
        if not allowed:
            messages.error(request, _(rate_limit_msg or "Submission limit reached."))
            return render(request, self.template_name, {'form': form})

        is_spam, spam_msg = self._is_spam_content(data['message'])
        if is_spam:
            messages.error(request, _(spam_msg or "Your message was flagged as potential spam."))
            return render(request, self.template_name, {'form': form})

        if self._is_disposable_email(data['email']):
            messages.error(request, _("Please use a permanent email address."))
            return render(request, self.template_name, {'form': form})

        if not self._deliver(data):
            messages.error(request, _("We couldn't send your message right now. Please email us directly or try again later."))
            return render(request, self.template_name, {'form': form})

        messages.success(request, _("Your message has been sent! We'll get back to you shortly."))
        return redirect('contact')

    def _deliver(self, data):
        """Email the message to the support inbox; replies go straight to the sender."""
        from django.core.mail import EmailMessage
        recipient = getattr(settings, 'CONTACT_EMAIL', '') or 'support@trackmyrupee.com'
        body = f"From: {data['name']} <{data['email']}>\n\n{data['message']}"
        try:
            EmailMessage(
                subject=f"[Contact] {data['subject']}",
                body=body,
                from_email=settings.DEFAULT_FROM_EMAIL,
                to=[recipient],
                reply_to=[data['email']],
            ).send(fail_silently=False)
        except Exception:
            logger.exception("Contact form email could not be sent")
            return False
        return True

    def _get_client_ip(self, request):
        x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
        if x_forwarded_for:
            ip = x_forwarded_for.split(',')[0]
        else:
            ip = request.META.get('REMOTE_ADDR')
        return ip

    def _check_rate_limit(self, ip):
        hourly_key = f'contact_hourly_{ip}'
        daily_key = f'contact_daily_{ip}'
        
        hourly_count = cache.get(hourly_key, 0)
        daily_count = cache.get(daily_key, 0)
        
        if hourly_count >= getattr(self, 'RATE_LIMIT_HOURLY', 5):
            return False, "Too many submissions. Please try again in an hour."
        
        if daily_count >= getattr(self, 'RATE_LIMIT_DAILY', 20):
            return False, "Daily submission limit reached. Please try again tomorrow."
        
        cache.set(hourly_key, hourly_count + 1, 3600)  # 1 hour
        cache.set(daily_key, daily_count + 1, 86400)   # 24 hours
        
        return True, None

    def _is_spam_content(self, text):
        text_lower = text.lower()
        if 'http://' in text_lower or 'https://' in text_lower or 'www.' in text_lower:
            return True, "Messages with URLs are not allowed."
        
        spam_keywords = getattr(self, 'SPAM_KEYWORDS', ['seo', 'marketing', 'guarantee', 'crypto', 'bitcoin'])
        for keyword in spam_keywords:
            if keyword in text_lower:
                return True, "Your message was flagged as potential spam."
        
        if len(text) > 20:
            caps_count = sum(1 for c in text if c.isupper())
            if caps_count / len(text) > 0.5:
                return True, "Please don't use excessive capitalization."
        
        if len(text.strip()) < getattr(self, 'MIN_MESSAGE_LENGTH', 10):
            return True, "Please provide a more detailed message."
        
        return False, None

    def _is_disposable_email(self, email):
        domain = email.split('@')[-1].lower()
        return domain in getattr(self, 'DISPOSABLE_DOMAINS', ['mailinator.com', '10minutemail.com', 'tempmail.com'])

class HealthCheckView(View):
    def get(self, request):
        return JsonResponse({"status": "healthy", "timestamp": datetime.now().isoformat()})


@login_required
def dpdp_consent_view(request):
    """
    Standalone DPDPA consent page for users who haven't granted consent yet.
    """
    try:
        profile = request.user.profile
    except Exception:
        from ..models import UserProfile
        profile, created = UserProfile.objects.get_or_create(user=request.user)

    if profile.consent_granted:
        return redirect('home')

    if request.method == 'POST':
        consent_email = request.POST.get('consent_email') == 'on'
        consent_transactions = request.POST.get('consent_transactions') == 'on'
        consent_device = request.POST.get('consent_device') == 'on'
        # Age is confirmed here for social (Google) signups; it is not stored.
        confirm_age = request.POST.get('confirm_age') == 'on'

        if consent_email and consent_transactions and consent_device and confirm_age:
            from django.utils import timezone
            profile.consent_granted = True
            profile.consent_timestamp = timezone.now()
            profile.consent_version = 'v1.0'
            profile.save()

            from ..models import ConsentEvent
            ConsentEvent.objects.create(
                user=request.user,
                action='GRANTED',
                purpose='Terms and Data Collection',
                consent_version='v1.0',
                ip_address=request.META.get('REMOTE_ADDR'),
                user_agent=request.META.get('HTTP_USER_AGENT', '')
            )

            messages.success(request, _("Thank you for your consent. You can now use TrackMyRupee."))
            return redirect('home')
        else:
            if consent_email and consent_transactions and consent_device:
                messages.error(request, _("You must confirm that you are 18 years of age or older to use TrackMyRupee."))
            else:
                messages.error(request, _("You must agree to all data collection terms to use TrackMyRupee."))

    return render(request, 'expenses/dpdp_consent.html', {
        'page_title': _('Data Consent Required')
    })


@csrf_exempt
def doh_handler_view(request):
    """
    DNS-over-HTTPS (DoH) handler supporting both RFC 8484 binary wire format
    and JSON DNS formats.
    """
    query_bytes = None
    is_json = False
    data = {}

    if request.method == 'GET':
        if 'dns' in request.GET:
            dns_param = request.GET.get('dns', '')
            # Add base64url padding
            padding_len = len(dns_param) % 4
            if padding_len:
                dns_param += '=' * (4 - padding_len)
            try:
                # Use urlsafe_b64decode to decode base64url
                query_bytes = base64.urlsafe_b64decode(dns_param)
            except Exception:
                return HttpResponse("Invalid base64 in dns parameter", status=400)
        elif 'name' in request.GET:
            is_json = True
        else:
            return HttpResponse("Missing dns or name parameter", status=400)
    elif request.method == 'POST':
        # Default to binary body if request has content-type 'application/dns-message'
        # or doesn't start with json curly brace
        body = request.body
        if body.startswith(b'{'):
            try:
                data = json.loads(body.decode('utf-8'))
                if 'name' in data:
                    is_json = True
            except Exception:
                pass
        
        if not is_json:
            query_bytes = body
    else:
        return HttpResponse("Method Not Allowed", status=405)

    if is_json:
        name = request.GET.get('name', '') if request.method == 'GET' else data.get('name', '')
        qtype_str = request.GET.get('type', 'SVCB') if request.method == 'GET' else data.get('type', 'SVCB')
        # JSON bodies can carry any type; names are at most 255 characters
        name = str(name)[:255]
        qtype_str = str(qtype_str)[:20]

        try:
            if qtype_str.isdigit():
                qtype_val = int(qtype_str)
                qtype = qtype_val
            else:
                qtype = dns.rdatatype.from_text(qtype_str.upper())
        except Exception:
            qtype = dns.rdatatype.SVCB

        # Format name properly for lookup
        name_lower = name.lower().strip()
        if not name_lower.endswith('.'):
            name_lower += '.'

        answers = []
        labels = name_lower.rstrip('.').split('.')

        is_dns_aid = False
        domain = ""
        if '_agents' in labels:
            idx = labels.index('_agents')
            if idx > 0 and labels[idx - 1].startswith('_'):
                is_dns_aid = True
                domain = '.'.join(labels[idx + 1:])

        if is_dns_aid:
            # We return SVCB/HTTPS parameters
            types_to_return = []
            try:
                target_rdtype = dns.rdatatype.from_text(qtype_str.upper()) if not qtype_str.isdigit() else int(qtype_str)
            except Exception:
                target_rdtype = dns.rdatatype.SVCB

            if target_rdtype == dns.rdatatype.SVCB or target_rdtype == 255:
                types_to_return.append((64, 'SVCB'))
            if target_rdtype == dns.rdatatype.HTTPS or target_rdtype == 255:
                types_to_return.append((65, 'HTTPS'))

            for rdtype_val, rdtype_name in types_to_return:
                target_host = domain if domain.endswith('.') else f"{domain}."
                answers.append({
                    "name": name,
                    "type": rdtype_val,
                    "TTL": 3600,
                    "data": f"1 {target_host} mandatory=alpn,port alpn=a2a port=443"
                })

        response_data = {
            "Status": 0,  # NOERROR
            "TC": False,
            "RD": True,
            "RA": True,
            "AD": True,   # Authenticated Data (DNSSEC validated)
            "CD": False,
            "Question": [
                {
                    "name": name,
                    "type": qtype
                }
            ],
            "Answer": answers
        }
        return JsonResponse(response_data, content_type='application/dns-json')

    if query_bytes:
        try:
            query_msg = dns.message.from_wire(query_bytes)
        except Exception as e:
            return HttpResponse(f"Invalid DNS binary message: {e}", status=400)

        response_msg = dns.message.make_response(query_msg)
        response_msg.flags |= dns.flags.AD

        for question in query_msg.question:
            qname = question.name
            qtype = question.rdtype
            qclass = question.rdclass

            qname_str = qname.to_text().lower().rstrip('.')
            labels = qname_str.split('.')

            is_dns_aid = False
            domain = ""
            if '_agents' in labels:
                idx = labels.index('_agents')
                if idx > 0 and labels[idx - 1].startswith('_'):
                    is_dns_aid = True
                    domain = '.'.join(labels[idx + 1:])

            if is_dns_aid:
                types_to_return = []
                if qtype == dns.rdatatype.SVCB or qtype == 255:
                    types_to_return.append(dns.rdatatype.SVCB)
                if qtype == dns.rdatatype.HTTPS or qtype == 255:
                    types_to_return.append(dns.rdatatype.HTTPS)

                target_host = domain if domain.endswith('.') else f"{domain}."
                for rdtype in types_to_return:
                    rrset = response_msg.find_rrset(
                        response_msg.answer,
                        qname,
                        qclass,
                        rdtype,
                        create=True
                    )
                    rdata_text = f"1 {target_host} mandatory=alpn,port alpn=a2a port=443"
                    rdata = dns.rdata.from_text(qclass, rdtype, rdata_text)
                    rrset.add(rdata, ttl=3600)

        response_wire = response_msg.to_wire()
        return HttpResponse(response_wire, content_type='application/dns-message')

    return HttpResponse("Bad Request", status=400)

