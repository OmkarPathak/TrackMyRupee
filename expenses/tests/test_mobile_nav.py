"""The phone's bottom bar names each page under its icon, and the More sheet is two plain groups."""
import re

from django.urls import reverse

from expenses.tests.test_analytics_page import PageCase


class MobileNavBase(PageCase):
    def html(self, name='settings-home'):
        response = self.client.get(reverse(name))
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    @staticmethod
    def bar(html):
        start = html.index('<nav class="mobile-bottom-nav')
        return html[start:html.index('</nav>', start)]

    @staticmethod
    def sheet(html):
        start = html.index('id="moreMenuSheet"')
        return html[start:html.index('id="supportLegalSheet"', start)]


class BottomBarLabelTests(MobileNavBase):
    def test_each_page_is_named_under_its_icon(self):
        bar = self.bar(self.html())
        labels = re.findall(r'<span class="mobile-nav-label">([^<]+)</span>', bar)
        self.assertEqual(labels, ['Dashboard', 'Transactions', 'Accounts', 'More'])

    def test_the_visible_name_is_the_accessible_name(self):
        bar = self.bar(self.html())
        items = re.findall(r'<(?:a|button)[^>]*class="mobile-nav-item[^"]*"[^>]*aria-label="([^"]+)"[^>]*>(.*?)</(?:a|button)>', bar, re.S)
        self.assertEqual(len(items), 4)
        for aria, inner in items:
            self.assertIn(f'>{aria}</span>', inner, aria)

    def test_the_add_button_is_unchanged(self):
        bar = self.bar(self.html())
        fab = bar[bar.index('id="global-fab"'):]
        self.assertNotIn('mobile-nav-label', fab)
        self.assertIn('aria-label="Add"', fab)

    def test_the_label_follows_the_page_you_are_on(self):
        bar = self.bar(self.html('all-transactions'))
        item = re.search(r'<a href="%s" class="mobile-nav-item ([^"]*)"' % re.escape(reverse('all-transactions')), bar)
        self.assertIn('active', item.group(1))

    def test_the_stylesheet_styles_the_label_and_is_cache_busted(self):
        from django.contrib.staticfiles import finders
        css = open(finders.find('style.css'), encoding='utf-8').read()
        self.assertIn('.mobile-nav-label', css)
        self.assertIn("style.css' %}?v=7.9", open('templates/base.html', encoding='utf-8').read())


class MoreSheetTests(MobileNavBase):
    def test_it_is_two_plain_groups_and_a_quiet_footer(self):
        sheet = self.sheet(self.html())
        headings = re.findall(r'text-uppercase fw-bold text-muted"[^>]*>\s*([^<]+?)\s*</div>', sheet)
        self.assertEqual(headings, ['Everyday', 'Money & plans'])
        for old in ('Cash Flow', 'Net Worth', 'Insights', 'System'):
            self.assertNotIn(old, headings)

    def test_the_bar_pages_are_not_repeated_in_the_sheet(self):
        sheet = self.sheet(self.html())
        for name in ('account-list', 'all-transactions', 'home'):
            self.assertNotIn(f'href="{reverse(name)}"', sheet, name)

    def test_nothing_that_was_reachable_is_gone(self):
        sheet = self.sheet(self.html())
        for name in ('expense-list', 'income-list', 'budget', 'recurring-list', 'calendar', 'analytics',
                     'holding-list', 'loan-list', 'goal-list', 'capital-event-list', 'tmr-flows',
                     'notification-list', 'settings-home'):
            self.assertIn(f'href="{reverse(name)}"', sheet, name)
        self.assertIn('data-bs-target="#supportLegalSheet"', sheet)
        self.assertIn('theme-toggle-btn', sheet)
        self.assertIn(reverse('account_logout'), sheet)

    def test_goals_is_offered_on_every_plan_as_it_is_on_the_web(self):
        for tier in ('FREE', 'PLUS', 'PRO'):
            self.user.profile.__class__.objects.filter(user=self.user).update(tier=tier)
            sheet = self.sheet(self.html())
            self.assertIn(f'href="{reverse("goal-list")}"', sheet, tier)

    def test_a_plan_without_loans_still_sees_them_marked_plus(self):
        self.user.profile.__class__.objects.filter(user=self.user).update(tier='FREE')
        sheet = self.sheet(self.html())
        tile = sheet[sheet.index(f'href="{reverse("pricing")}"'):]
        tile = tile[:tile.index('</a>')]
        self.assertIn('Loans', tile)
        self.assertIn('Plus', tile)

    def test_the_tour_calls_the_first_screen_what_the_bar_calls_it(self):
        from django.contrib.staticfiles import finders
        js = open(finders.find('js/tutorial.js'), encoding='utf-8').read()
        self.assertIn('Dashboard is your summary', js)
        self.assertNotIn('Home is your summary', js)
