from django.contrib import messages
from django.shortcuts import redirect
from django.urls import reverse


class DemoReadOnlyMiddleware:
    """
    Prevents the 'demo' user from making any state-changing requests (POST, PUT, DELETE).
    """
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated and request.user.username == 'demo':
            if request.method in ['POST', 'PUT', 'DELETE', 'PATCH']:
                # Whitelist non-state-changing API POST requests
                whitelisted_paths = [
                    reverse('account_logout'),
                    reverse('parse-expense'),
                    reverse('predict-category')
                ]
                if request.path in whitelisted_paths:
                    return self.get_response(request)

                messages.warning(request, "⚠️ Demo Account: This action is restricted to read-only mode.")
                return redirect(request.META.get('HTTP_REFERER', '/'))

        response = self.get_response(request)
        return response

class TimezoneMiddleware:
    """
    Activates the timezone stored in the 'django_timezone' cookie.
    """
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        import zoneinfo

        from django.utils import timezone
        
        tzname = request.COOKIES.get('django_timezone')
        if tzname:
            try:
                timezone.activate(zoneinfo.ZoneInfo(tzname))
            except Exception:
                # If cookie is invalid, fallback to default
                pass
        else:
            timezone.deactivate()
            
        return self.get_response(request)

class LocaleMiddlewareByProfile:
    """
    Activates the language stored in the UserProfile.
    """
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated:
            from django.utils import translation
            try:
                # Use getattr to avoid issues if profile doesn't exist yet
                profile = getattr(request.user, 'profile', None)
                if profile and profile.language:
                    translation.activate(profile.language)
                    request.LANGUAGE_CODE = translation.get_language()
            except Exception:
                pass
        
        response = self.get_response(request)
        return response
