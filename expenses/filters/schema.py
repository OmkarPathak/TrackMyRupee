import json
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Union


@dataclass
class FilterDef:
    key: str
    label: str
    type: str = 'multi_select'  # multi_select, single_select, range, boolean
    source: str = 'static'  # static, dynamic
    options: Optional[List[Union[str, Dict[str, str]]]] = None
    options_fn: Optional[Callable[..., List[Dict[str, str]]]] = None
    searchable: Optional[bool] = None
    field_name: Optional[str] = None
    lookup_expr: Optional[str] = None
    custom_filter_fn: Optional[Callable[..., Any]] = None

    def is_searchable(self) -> bool:
        if self.searchable is not None:
            return self.searchable
        if self.source == 'dynamic':
            return True
        if self.options and len(self.options) > 10:
            return True
        return False

    def get_options_list(self, user=None, q: str = '') -> List[Dict[str, str]]:
        """Returns option objects formatted as [{'value': val, 'label': label}, ...]"""
        raw_options = []
        if self.source == 'dynamic' and self.options_fn:
            raw_options = self.options_fn(user=user, q=q)
        elif self.options:
            raw_options = self.options

        formatted = []
        for opt in raw_options:
            if isinstance(opt, dict):
                val = str(opt.get('value', ''))
                lbl = str(opt.get('label', val))
                item = {**opt, 'value': val, 'label': lbl}
            elif isinstance(opt, (list, tuple)) and len(opt) == 2:
                val = str(opt[0])
                lbl = str(opt[1])
                item = {'value': val, 'label': lbl}
            else:
                val = str(opt)
                lbl = str(opt)
                item = {'value': val, 'label': lbl}
            
            if q and q.strip():
                query = q.strip().lower()
                if query not in val.lower() and query not in lbl.lower():
                    continue

            formatted.append(item)
        return formatted

    def to_dict(self) -> Dict[str, Any]:
        static_opts = []
        if self.source == 'static' and self.options:
            static_opts = self.get_options_list()
        
        return {
            'key': self.key,
            'label': self.label,
            'type': self.type,
            'source': self.source,
            'searchable': self.is_searchable(),
            'options': static_opts,
        }


@dataclass
class FilterSetConfig:
    page_key: str
    filters: List[FilterDef]
    sort_options: List[Dict[str, str]] = field(default_factory=list)
    default_sort: str = 'date_desc'
    default_time_range: str = 'this_month'
    supports_search: bool = True
    search_placeholder: str = 'Search description...'
    search_field: str = 'description'
    search_fields: Optional[List[str]] = None
    supports_time_period: bool = True
    supports_sort: bool = True
    external_chip_row: bool = False

    def get_filter(self, key: str) -> Optional[FilterDef]:
        for f in self.filters:
            if f.key == key:
                return f
        return None

    def to_dict(self) -> Dict[str, Any]:
        return {
            'page_key': self.page_key,
            'supports_search': self.supports_search,
            'search_placeholder': self.search_placeholder,
            'supports_time_period': self.supports_time_period,
            'supports_sort': self.supports_sort,
            'external_chip_row': self.external_chip_row,
            'default_time_range': self.default_time_range,
            'default_sort': self.default_sort,
            'sort_options': self.sort_options,
            'filters': [f.to_dict() for f in self.filters],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict())
