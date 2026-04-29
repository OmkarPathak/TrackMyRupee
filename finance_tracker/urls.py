"""
URL configuration for finance_tracker project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/4.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.conf import settings
from django.contrib import admin
from django.contrib.sitemaps.views import sitemap
from django.urls import include, path
from django.views.generic import TemplateView
from django.views.generic.base import RedirectView

from blog.sitemap import BlogSitemap
from finance_tracker.sitemaps import StaticViewSitemap

sitemaps = {
    'static': StaticViewSitemap,
    'blog': BlogSitemap,
}

from django.http import HttpResponse
from django.views.decorators.http import require_GET


@require_GET
def robots_txt(request):
    lines = [
        "User-Agent: *",
        "Allow: /",
        "Disallow: /admin/",
        "Disallow: /auth/",
        "Disallow: /accounts/", 
        "",
        f"Sitemap: {request.scheme}://{request.get_host()}/sitemap.xml",
    ]
    return HttpResponse("\n".join(lines), content_type="text/plain")

@require_GET
def llms_txt(request):
    import os
    file_path = os.path.join(settings.BASE_DIR, 'llms.txt')
    if os.path.exists(file_path):
        with open(file_path) as f:
            content = f.read()
        return HttpResponse(content, content_type="text/plain")
    return HttpResponse("llms.txt not found", status=404)

urlpatterns = [
    path('tmr_admin/', admin.site.urls),
    path('auth/login/', RedirectView.as_view(pattern_name='account_login', permanent=True)), # Redirect legacy login
    path('accounts/', include('allauth.urls')),
    path('sitemap.xml', sitemap, {'sitemaps': sitemaps}, name='django.contrib.sitemaps.views.sitemap'),
    path('robots.txt', robots_txt),
    path('llms.txt', llms_txt),
    path('favicon.ico', RedirectView.as_view(url='/static/img/pwa-icon-512.png')),
    path('apple-touch-icon.png', RedirectView.as_view(url='/static/img/pwa-icon-512.png')),
    path('apple-touch-icon-precomposed.png', RedirectView.as_view(url='/static/img/pwa-icon-512.png')),
    path('blog/', include('blog.urls')),
    path('', include('expenses.urls')),
    # PWA
    path('manifest.json', TemplateView.as_view(template_name='manifest.json', content_type='application/json'), name='manifest'),
    path('service-worker.js', TemplateView.as_view(template_name='service-worker.js', content_type='application/javascript'), name='service-worker'),
    path('offline/', TemplateView.as_view(template_name='offline.html'), name='offline'),
    path('webpush/', include('webpush.urls')),
]
