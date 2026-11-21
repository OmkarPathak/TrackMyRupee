"""A click on a guide link is counted (PostHog, after analytics consent) and the guide is opened with the source marked.
The guide is a different site, so it is answered here with a small stand-in page."""
from django.urls import reverse

from e2e.base import E2ETestCase, make_account, make_user

CAPTURE = "window.posthog = {capture: (name, props) => { (window.__captured = window.__captured || []).push([name, props]); }}"


class Base(E2ETestCase):
    def setUp(self):
        super().setUp()
        self.user = make_user('tracker')
        make_account(self.user, 'HDFC Savings', 'SAVINGS_ACCOUNT', 500000)

    def page_with_guide(self, accepted):
        page = self.new_page(self.user)
        self.opened = []
        page.context.route('https://docs.trackmyrupee.com/**', lambda route: (self.opened.append(route.request.url),
                                                                       route.fulfill(status=200, content_type='text/html', body='<h1>guide</h1>')))
        page.add_init_script(f"localStorage.setItem('cookieConsent', '{accepted}')")
        return page

    def click_help(self, page):
        self.open(page, reverse('budget'))
        page.wait_for_load_state('networkidle')   # (the page's own analytics snippet puts its queue in place once loaded)
        page.evaluate(CAPTURE)
        with page.context.expect_page() as guide:
            page.click('a[data-docs-src="topbar"]')
        guide.value.wait_for_load_state()


class CountingTests(Base):
    def test_a_click_is_counted_with_the_page_and_where_the_link_sits(self):
        page = self.page_with_guide('accepted')
        self.click_help(page)
        self.assertEqual(page.evaluate('window.__captured'), [['docs_link_clicked', {'page': '07-budgets/', 'source': 'topbar'}]])

    def test_nothing_is_counted_without_consent(self):
        page = self.page_with_guide('declined')
        self.click_help(page)
        self.assertIsNone(page.evaluate('window.__captured || null'))


class MarkingTests(Base):
    def test_the_guide_is_opened_with_the_source_marked(self):
        page = self.page_with_guide('declined')
        self.click_help(page)
        self.assertEqual(len(self.opened), 1)
        self.assertIn('utm_source=trackmyrupee-app', self.opened[0])
        self.assertIn('utm_medium=guide-link', self.opened[0])
        self.assertIn('utm_content=topbar', self.opened[0])
        self.assertTrue(self.opened[0].startswith('https://docs.trackmyrupee.com/07-budgets/?'))
