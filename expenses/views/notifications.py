import hmac
import logging
import threading

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.cache import cache
from django.core.management import call_command
from django.http import JsonResponse
from django.shortcuts import redirect, resolve_url
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from django.views.generic import ListView

from expenses.views.utils import get_safe_redirect_url

from ..models import Notification
from .mixins import HtmxPartialTemplateMixin

logger = logging.getLogger(__name__)


def _dispatch_cron_command(command_name, lock_timeout=600):
    """
    Runs a management command in a background thread, guarded by a cache lock so
    overlapping triggers (e.g. a retried external cron call) skip instead of running concurrently.
    Returns a JsonResponse.
    """
    lock_key = f'cron_lock_{command_name}'
    if not cache.add(lock_key, 1, timeout=lock_timeout):
        return JsonResponse({'success': False, 'message': 'Already running'}, status=409)

    def _run():
        try:
            call_command(command_name)
        except Exception:
            logger.exception(f"Background run of {command_name} failed")
        finally:
            cache.delete(lock_key)

    threading.Thread(target=_run, daemon=True).start()
    return JsonResponse({'success': True, 'message': f'{command_name} started'})


def _cron_authorized(request):
    # Prefer header to avoid leaking secrets through URL logs.
    provided_secret = (
        request.headers.get('X-Cron-Secret')
        or request.headers.get('Authorization', '').replace('Bearer ', '').strip()
        or request.POST.get('secret')
    )
    if settings.CRON_ALLOW_QUERY_SECRET or not provided_secret:
        provided_secret = provided_secret or request.GET.get('secret')

    expected_secret = (settings.CRON_SECRET or '').strip()
    provided_secret = (provided_secret or '').strip()
    if not expected_secret:
        return False

    return hmac.compare_digest(provided_secret, expected_secret)


def _get_int_param(request, key, default):
    raw = request.POST.get(key)
    if raw in (None, ''):
        raw = request.GET.get(key)
    if raw in (None, ''):
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def _get_threshold_param(request, default):
    raw = request.POST.get('threshold')
    if raw in (None, ''):
        raw = request.GET.get('threshold')
    if raw in (None, ''):
        return default
    try:
        return str(float(raw))
    except (TypeError, ValueError):
        return default


class NotificationListView(HtmxPartialTemplateMixin, LoginRequiredMixin, ListView):
    model = Notification
    template_name = 'expenses/notification_list.html'
    htmx_template_name = 'expenses/partials/_notification_list.html'
    context_object_name = 'notifications'
    paginate_by = 20

    def get_queryset(self):
        return Notification.objects.filter(user=self.request.user).order_by('-created_at')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['unread_count'] = Notification.objects.filter(user=self.request.user, is_read=False).count()
        return context

@login_required
def mark_notifications_read(request):
    if request.method == 'POST':
        Notification.objects.filter(user=request.user, is_read=False).update(is_read=True)
        messages.success(request, "All notifications marked as read.")
        return redirect('notification-list')
    return redirect('notification-list')

def get_notification_by_uuid_or_pk(pk, user):
    import uuid
    is_uuid = False
    if isinstance(pk, uuid.UUID):
        is_uuid = True
    elif isinstance(pk, str):
        try:
            uuid.UUID(pk)
            is_uuid = True
        except ValueError:
            pass
    if is_uuid:
        return Notification.objects.get(uuid=pk, user=user)
    return Notification.objects.get(pk=pk, user=user)

@login_required
def mark_single_notification_read(request, pk):
    try:
        notification = get_notification_by_uuid_or_pk(pk, request.user)
        notification.is_read = True
        notification.save()
        return JsonResponse({'success': True})
    except Notification.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Notification not found'}, status=404)

@login_required
def notification_redirect(request, pk):
    """
    Mark notification as read and redirect to its link.
    """
    try:
        notification = get_notification_by_uuid_or_pk(pk, request.user)
        notification.is_read = True
        notification.save()
        
        target_link = notification.link or 'notification-list'

        
        try:
            resolved_url = resolve_url(target_link)
        except Exception:
            resolved_url = target_link
            
        safe_link = get_safe_redirect_url(request, resolved_url, resolve_url('notification-list'))
        return redirect(safe_link)
    except Notification.DoesNotExist:
        messages.error(request, "Notification not found.")
        return redirect('notification-list')

@csrf_exempt
@require_POST
def trigger_notifications(request):
    """
    HTTP endpoint to trigger notifications via external cron service.
    """
    if not _cron_authorized(request):
        return JsonResponse({'error': 'Unauthorized'}, status=403)
    return _dispatch_cron_command('send_notifications', lock_timeout=600)


@csrf_exempt
@require_POST
def trigger_lifecycle_emails(request):
    """
    HTTP endpoint to trigger lifecycle drip emails via external cron service.
    """
    if not _cron_authorized(request):
        return JsonResponse({'error': 'Unauthorized'}, status=403)
    return _dispatch_cron_command('send_lifecycle_emails', lock_timeout=600)


@csrf_exempt
@require_POST
def trigger_monthly_reports_view(request):
    """
    HTTP endpoint to trigger monthly financial reports via external cron service.
    """
    if not _cron_authorized(request):
        return JsonResponse({'error': 'Unauthorized'}, status=403)
    return _dispatch_cron_command('send_monthly_report', lock_timeout=900)


@csrf_exempt
@require_POST
def trigger_daily_reminders_view(request):
    """
    HTTP endpoint to trigger daily expense reminders via external cron service.
    """
    if not _cron_authorized(request):
        return JsonResponse({'error': 'Unauthorized'}, status=403)
    return _dispatch_cron_command('send_daily_reminders', lock_timeout=300)


@csrf_exempt
@require_POST
def trigger_announcements(request):
    """
    HTTP endpoint to trigger broadcast announcement sending via external cron service.
    """
    if not _cron_authorized(request):
        return JsonResponse({'error': 'Unauthorized'}, status=403)
    return _dispatch_cron_command('send_announcements', lock_timeout=600)


@csrf_exempt
@require_POST
def trigger_ledger_retry_view(request):
    """
    HTTP endpoint to retry failed ledger shadow postings via external cron service.
    Optional query params:
    - limit (default: 200)
    """
    if not _cron_authorized(request):
        return JsonResponse({'error': 'Unauthorized'}, status=403)

    limit = _get_int_param(request, 'limit', 200)
    try:
        call_command('retry_ledger_shadow_failures', limit=limit)
        return JsonResponse(
            {
                'success': True,
                'message': 'Ledger retry triggered successfully',
                'limit': limit,
            }
        )
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


@csrf_exempt
@require_POST
def trigger_ledger_reconcile_view(request):
    """
    HTTP endpoint to reconcile ledger/account balances via external cron service.
    Optional query params:
    - threshold (default: 0.01)
    """
    if not _cron_authorized(request):
        return JsonResponse({'error': 'Unauthorized'}, status=403)

    threshold = _get_threshold_param(request, '0.01')
    try:
        call_command('reconcile_ledgers', threshold=threshold)
        return JsonResponse(
            {
                'success': True,
                'message': 'Ledger reconciliation triggered successfully',
                'threshold': threshold,
            }
        )
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


@csrf_exempt
@require_POST
def trigger_ledger_maintenance_view(request):
    """
    HTTP endpoint to run combined ledger maintenance via external cron service.
    Optional query params:
    - retry_limit (default: 200)
    - threshold (default: 0.01)
    """
    if not _cron_authorized(request):
        return JsonResponse({'error': 'Unauthorized'}, status=403)

    retry_limit = _get_int_param(request, 'retry_limit', 200)
    threshold = _get_threshold_param(request, '0.01')
    try:
        call_command(
            'run_ledger_maintenance',
            retry_limit=retry_limit,
            reconcile=True,
            threshold=threshold,
        )
        return JsonResponse(
            {
                'success': True,
                'message': 'Ledger maintenance triggered successfully',
                'retry_limit': retry_limit,
                'threshold': threshold,
            }
        )
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


@csrf_exempt
def trigger_sync_nav_cron_view(request):
    """
    HTTP Cron endpoint to sync daily mutual fund NAVs via external services like cron-job.org.
    Accepts GET or POST requests with X-Cron-Secret header or ?secret= query parameter.
    """
    if not _cron_authorized(request):
        return JsonResponse({'error': 'Unauthorized'}, status=403)

    force = (request.GET.get('force') or request.POST.get('force', '')).lower() in {'1', 'true', 'yes', 'on'}
    try:
        from ..nav_provider import NAVFetchService
        service = NAVFetchService()
        summary = service.sync_active_holdings_navs(force=force)
        return JsonResponse({
            'success': True,
            'message': 'NAV sync completed successfully',
            'summary': summary,
        })
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)
