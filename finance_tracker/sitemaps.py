from django.contrib import sitemaps
from django.urls import reverse


class StaticViewSitemap(sitemaps.Sitemap):
    priority = 0.5
    changefreq = 'monthly'
    protocol = 'https'

    def items(self):
        return [
            'landing', 'about', 'signup', 'account_login', 'contact', 
            'privacy-policy', 'terms-of-service', 'demo_login', 
            'blog_list', 'loan-emi-calculator', 'axio-alternative',
            'walnut-alternative-no-sms', 'indmoney-alternative-privacy'
        ]

    def location(self, item):
        return reverse(item)
