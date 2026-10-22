"""Template flags for the Insights Engine. Cheap: settings only, no queries."""
from . import is_engine_on


def insights_flags(request):
    user = getattr(request, 'user', None)
    return {'INSIGHTS_ON': bool(user is not None and user.is_authenticated and is_engine_on(user))}
