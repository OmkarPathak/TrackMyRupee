import logging
from typing import Any, Dict, Tuple
from django.db.models import QuerySet
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

    from ..views.utils import apply_date_filters

    # 1. Date / Time Period Filter
    queryset = apply_date_filters(queryset, request)


    # 2. Search Query Filter
    search_query = request.GET.get('search') or request.GET.get('q') or ''
    search_query = search_query.strip()
    if search_query and config.supports_search:
        applied_state['search'] = search_query
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
    sort_by = applied_state['sort']
    if sort_by == 'date_asc':
        queryset = queryset.order_by('date', 'created_at', 'id')
    elif sort_by == 'amount_desc':
        queryset = queryset.order_by('-base_amount', '-id')
    elif sort_by == 'amount_asc':
        queryset = queryset.order_by('base_amount', 'id')
    else:  # default date_desc
        queryset = queryset.order_by('-date', '-created_at', '-id')

    return queryset, applied_state
