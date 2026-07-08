import re

from bs4 import BeautifulSoup, NavigableString
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


class DPDPAConsentMiddleware:
    """
    Ensures that authenticated users have accepted the DPDPA consent terms.
    If not, redirects them to the consent page.
    """
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated and request.user.username != 'demo':
            from django.urls import Resolver404, resolve
            
            try:
                resolver_match = resolve(request.path_info)
                url_name = resolver_match.url_name
            except Resolver404:
                url_name = None

            try:
                profile = getattr(request.user, 'profile', None)
                if not profile:
                    from .models import UserProfile
                    profile, _ = UserProfile.objects.get_or_create(user=request.user)
            except Exception:
                profile = None

            # Skip checking for specific URLs
            allowed_url_names = [
                'dpdp_consent',
                'account_logout',
                'landing',
                'features',
                'about',
                'privacy-policy',
                'terms-of-service',
                'refund-policy',
                'security',
                'contact',
                'loan-emi-calculator',
                'ping',
                'user-delete',
                'withdraw-consent',
                'axio-alternative',
                'walnut-alternative-no-sms',
                'indmoney-alternative-privacy',
            ]

            is_static_or_media = (
                request.path_info.startswith('/static/') or 
                request.path_info.startswith('/media/') or 
                request.path_info.startswith('/tmr_admin/')
            )

            if profile and not profile.consent_granted and not is_static_or_media and url_name not in allowed_url_names:
                return redirect('dpdp_consent')

        response = self.get_response(request)
        return response


class AgentMarkdownMiddleware:
    """
    Middleware that converts HTML responses to Markdown if the request Accept header
    prefers or includes 'text/markdown'.
    """
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)

        # Only parse HTML with BeautifulSoup for AI/LLM clients requesting Markdown.
        # Regular browsers never send Accept: text/markdown so this is a no-op for them.
        if request.method == 'GET' and 'text/markdown' in request.META.get('HTTP_ACCEPT', ''):
            content_type = response.get('Content-Type', '')
            if content_type.startswith('text/html'):
                html_content = response.content.decode('utf-8')
                markdown_content = self.html_to_markdown(html_content)
                token_count = len(markdown_content) // 4

                response.content = markdown_content.encode('utf-8')
                response['Content-Type'] = 'text/markdown'
                response['X-Markdown-Tokens'] = str(token_count)

                if 'Vary' in response:
                    varies = [v.strip() for v in response['Vary'].split(',')]
                    if 'Accept' not in varies:
                        response['Vary'] = f"{response['Vary']}, Accept"
                else:
                    response['Vary'] = 'Accept'

        return response

    def html_to_markdown(self, html_content):

        soup = BeautifulSoup(html_content, 'html.parser')
        for el in soup(['script', 'style', 'head', 'title', 'meta', 'link']):
            el.decompose()

        def convert_node(node):
            if isinstance(node, NavigableString):
                return node.string

            child_texts = []
            for child in node.children:
                child_texts.append(convert_node(child) or '')
            inner_content = ''.join(child_texts)
            tag = node.name

            if tag in ['h1', 'h2', 'h3', 'h4', 'h5', 'h6']:
                level = int(tag[1])
                return '\n\n' + '#' * level + f' {inner_content.strip()}\n\n'
            elif tag == 'p':
                return f'\n\n{inner_content.strip()}\n\n'
            elif tag == 'br':
                return '\n'
            elif tag == 'li':
                parent_tag = node.parent.name if node.parent else 'ul'
                if parent_tag == 'ol':
                    siblings = [s for s in node.parent.children if s.name == 'li'] if node.parent else []
                    idx = siblings.index(node) + 1 if node in siblings else 1
                    return f'\n{idx}. {inner_content.strip()}'
                else:
                    return f'\n- {inner_content.strip()}'
            elif tag in ['ul', 'ol']:
                return f'\n{inner_content}\n'
            elif tag in ['strong', 'b']:
                return f'**{inner_content}**'
            elif tag in ['em', 'i']:
                return f'*{inner_content}*'
            elif tag == 'a':
                href = node.get('href', '')
                if href and not href.startswith('javascript:'):
                    return f'[{inner_content}]({href})'
                return inner_content
            elif tag == 'code':
                return f'`{inner_content}`'
            elif tag == 'pre':
                return f'\n```\n{inner_content}\n```\n'
            elif tag == 'blockquote':
                lines = [f'> {line}' for line in inner_content.strip().split('\n')]
                return '\n\n' + '\n'.join(lines) + '\n\n'
            return inner_content

        body = soup.find('body') or soup
        markdown_text = convert_node(body)
        markdown_text = re.sub(r'\n{3,}', '\n\n', markdown_text)
        return markdown_text.strip()

