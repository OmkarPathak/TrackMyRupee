"""Template flag for family sharing. Cheap: settings only, no queries."""
from . import family_enabled_for


def family_flags(request):
    return {'FAMILY_ON': family_enabled_for(getattr(request, 'user', None))}
