import logging
from typing import Any, Dict, Tuple
from django.db.models import F, QuerySet
from django.http import HttpRequest

from .schema import FilterSetConfig

logger = logging.getLogger(__name__)



def apply_filter_config(
    queryset: QuerySet,
    request: HttpRequest,
    config: FilterSetConfig,
) -> Tuple[QuerySet, Dict[str, Any]]:
    """
    Applies search, time range, filter chips, and sorting to a queryset
    based on a page's FilterSetConfig.

    Returns (filtered_queryset, applied_state) tuple.
    """
    applied_state: Dict[str, Any] = {
        'search': '',
        'time_period': request.GET.get('time_period', config.default_time_range),
        'start_date': request.GET.get('start_date', ''),
        'end_date': request.GET.get('end_date', ''),
        'sort': request.GET.get('sort', config.default_sort),
        'filters': {},
    }

    from ..periods import get_cycle_context, resolve_period
    from ..views.utils import apply_date_filters

    # 1. Date / Time Period Filter
    if config.supports_time_period:
        queryset = apply_date_filters(queryset, request)
        period = resolve_period(
            user=getattr(request, 'user', None),
            time_period=applied_state['time_period'],
            start_date=applied_state['start_date'],
            end_date=applied_state['end_date'],
        )
        cycle_ctx = get_cycle_context(user=getattr(request, 'user', None))
        applied_state['cycle_active'] = cycle_ctx['cycle_active']
        applied_state['salary_date'] = cycle_ctx['salary_date']
        applied_state['cycle_range'] = cycle_ctx['cycle_range']
        applied_state['prev_cycle_range'] = cycle_ctx['prev_cycle_range']
        applied_state['calendar_month_range'] = cycle_ctx['calendar_month_range']
        applied_state['last_month_range'] = cycle_ctx['last_month_range']
        applied_state['last_3_months_range'] = cycle_ctx['last_3_months_range']
        applied_state['this_year_range'] = cycle_ctx['this_year_range']
        applied_state['cycle_day'] = cycle_ctx['cycle_day']
        applied_state['cycle_total_days'] = cycle_ctx['cycle_total_days']
        applied_state['cycle_pct'] = cycle_ctx['cycle_pct']
        applied_state['is_current_period_cycle'] = period.is_cycle
    else:
        applied_state['cycle_active'] = False
        applied_state['salary_date'] = 1
        applied_state['cycle_range'] = ''
        applied_state['prev_cycle_range'] = ''
        applied_state['calendar_month_range'] = ''
        applied_state['last_month_range'] = ''
        applied_state['last_3_months_range'] = ''
        applied_state['this_year_range'] = ''
        applied_state['cycle_day'] = 0
        applied_state['cycle_total_days'] = 0
        applied_state['cycle_pct'] = 0
        applied_state['is_current_period_cycle'] = False


    # 2. Search Query Filter
    search_query = request.GET.get('search') or request.GET.get('q') or ''
    search_query = search_query.strip()
    if search_query and config.supports_search:
        applied_state['search'] = search_query
        if getattr(config, 'search_fields', None):
            from django.db.models import Q
            search_q = Q()
            for sf in config.search_fields:
                search_q |= Q(**{f"{sf}__icontains": search_query})
            queryset = queryset.filter(search_q)
        else:
            queryset = queryset.filter(**{f"{config.search_field}__icontains": search_query})

    # 3. Process Chip Filters declared in config
    for filter_def in config.filters:
        raw_vals = request.GET.getlist(filter_def.key)
        if not raw_vals:
            single_val = request.GET.get(filter_def.key)
            if single_val:
                raw_vals = [single_val]

        # Clean empty strings
        clean_vals = [v.strip() for v in raw_vals if v and str(v).strip()]
        if not clean_vals:
            continue

        # Save to applied state
        applied_state['filters'][filter_def.key] = clean_vals

        # Apply filter logic
        try:
            if filter_def.custom_filter_fn:
                queryset = filter_def.custom_filter_fn(queryset, clean_vals)
            elif filter_def.field_name:
                lookup = filter_def.lookup_expr or ('in' if filter_def.type == 'multi_select' else 'exact')
                if lookup == 'in':
                    queryset = queryset.filter(**{f"{filter_def.field_name}__in": clean_vals})
                elif lookup == 'exact':
                    val = clean_vals[0] if isinstance(clean_vals, list) else clean_vals
                    queryset = queryset.filter(**{filter_def.field_name: val})
                else:
                    queryset = queryset.filter(**{f"{filter_def.field_name}__{lookup}": clean_vals})
        except Exception as exc:
            logger.warning(f"Error applying filter key '{filter_def.key}' with values {clean_vals}: {exc}")
            # Silently swallow malformed/dead filters to prevent page load crashes
            continue

    # 4. Sorting
    if config.supports_sort:
        sort_by = applied_state['sort']
        if sort_by == 'date_asc':
            queryset = queryset.order_by('date', 'created_at', 'id')
        elif sort_by in ('amount_desc', 'amount_asc'):
            amount_field = 'base_amount'
            model = getattr(queryset, 'model', None)
            if model:
                try:
                    model._meta.get_field('base_amount')
                    amount_field = 'base_amount'
                except Exception:
                    try:
                        model._meta.get_field('amount')
                        amount_field = 'amount'
                    except Exception:
                        amount_field = 'base_amount'
            if sort_by == 'amount_desc':
                queryset = queryset.order_by(f'-{amount_field}', '-id')
            else:
                queryset = queryset.order_by(amount_field, 'id')
        elif sort_by == 'name_asc':
            queryset = queryset.order_by('name', 'id')
        elif sort_by == 'name_desc':
            queryset = queryset.order_by('-name', '-id')
        elif sort_by == 'limit_desc':
            queryset = queryset.order_by(F('limit').desc(nulls_last=True), 'name', 'id')
        elif sort_by == 'limit_asc':
            queryset = queryset.order_by(F('limit').asc(nulls_last=True), 'name', 'id')
        elif sort_by == 'date_desc':
            try:
                queryset.model._meta.get_field('date')
                queryset = queryset.order_by('-date', '-created_at', '-id')
            except Exception:
                queryset = queryset.order_by('-created_at', '-id')

    return queryset, applied_state
