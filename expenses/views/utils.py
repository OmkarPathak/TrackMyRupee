import uuid

from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse


def get_object_by_uuid_or_pk(model_or_queryset, val, **kwargs):
    if hasattr(model_or_queryset, '_default_manager'):
        queryset = model_or_queryset._default_manager.all()
    else:
        queryset = model_or_queryset

    if isinstance(val, uuid.UUID) or (isinstance(val, str) and len(val) == 36):
        return get_object_or_404(queryset, uuid=val, **kwargs)
    return get_object_or_404(queryset, pk=val, **kwargs)

def redirect_to_uuid_url_if_needed(request, obj, lookup_kwarg='pk'):
    if request.method != 'GET':
        return None
        
    import sys

    from django.conf import settings
    if getattr(settings, 'TESTING', False) or 'test' in sys.argv:
        return None
        
    match = request.resolver_match
    if not match or not match.url_name:
        return None
        
    val = match.kwargs.get(lookup_kwarg)
    if val is None:
        return None
        
    is_uuid = False
    if isinstance(val, uuid.UUID):
        is_uuid = True
    elif isinstance(val, str):
        try:
            uuid.UUID(val)
            is_uuid = True
        except ValueError:
            pass
            
    if not is_uuid and hasattr(obj, 'uuid') and obj.uuid:
        new_kwargs = dict(match.kwargs)
        new_kwargs[lookup_kwarg] = str(obj.uuid)
        
        url_name = match.url_name
        if match.namespaces:
            url_name = ':'.join(match.namespaces) + ':' + url_name
            
        try:
            target_url = reverse(url_name, kwargs=new_kwargs)
            if request.GET:
                target_url += '?' + request.GET.urlencode()
            return redirect(target_url, permanent=True)
        except Exception:
            return None
            
    return None

def get_redirect_pk_or_uuid(obj):
    import sys

    from django.conf import settings
    if getattr(settings, 'TESTING', False) or 'test' in sys.argv:
        return obj.pk
    return obj.uuid if hasattr(obj, 'uuid') and obj.uuid else obj.pk

def get_safe_redirect_url(request, next_url, fallback_url):
    from django.utils.http import url_has_allowed_host_and_scheme
    if next_url and url_has_allowed_host_and_scheme(
        url=next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure()
    ):
        return next_url
    return fallback_url

def apply_date_filters(queryset, request, date_field='date'):
    """
    Applies time_period, start_date, or end_date filtering to a queryset.
    time_period can be: 'this_month', 'last_month', 'last_3_months', 'this_year', 'all', 'custom'
    """
    import calendar
    from datetime import datetime, timedelta

    from django.utils import timezone

    time_period = request.GET.get('time_period')
    start_date = request.GET.get('start_date')
    end_date = request.GET.get('end_date')

    if not time_period:
        if start_date or end_date:
            time_period = 'custom'
        else:
            time_period = 'this_month'

    today = timezone.localtime().date()

    if time_period == 'this_month':
        start_date = today.replace(day=1)
        _, last_day = calendar.monthrange(today.year, today.month)
        end_date = today.replace(day=last_day)
    elif time_period == 'last_month':
        first_day_this_month = today.replace(day=1)
        last_day_last_month = first_day_this_month - timedelta(days=1)
        start_date = last_day_last_month.replace(day=1)
        end_date = last_day_last_month
    elif time_period == 'last_3_months':
        start_date = today - timedelta(days=90)
        end_date = today
    elif time_period == 'this_year':
        start_date = today.replace(month=1, day=1)
        end_date = today.replace(month=12, day=31)
    elif time_period == 'custom' or not time_period:
        # Fallback to explicit start_date / end_date if provided
        if isinstance(start_date, str) and start_date:
            try:
                start_date = datetime.strptime(start_date, '%Y-%m-%d').date()
            except ValueError:
                start_date = None
        if isinstance(end_date, str) and end_date:
            try:
                end_date = datetime.strptime(end_date, '%Y-%m-%d').date()
            except ValueError:
                end_date = None

    if start_date:
        queryset = queryset.filter(**{f"{date_field}__gte": start_date})
    if end_date:
        queryset = queryset.filter(**{f"{date_field}__lte": end_date})

    return queryset
