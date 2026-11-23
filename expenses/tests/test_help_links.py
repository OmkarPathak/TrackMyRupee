"""Links to the user guide: every page maps to a guide page that exists, the links carry the person's language and open
in a new tab, and the top bar, the phone menu and the Guided Setup pages have one."""
from pathlib import Path

from django.contrib.auth.models import User
from django.template import Context, Template
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import translation

from expenses import help_links
from expenses.flows.registry import FlowRegistry

GUIDE = Path(__file__).resolve().parents[2] / 'guide-src'


def guide_file_exists(path):
    """'07-budgets/' -> guide-src/07-budgets/index.md; '02-accounts/holdings/' -> guide-src/02-accounts/holdings.md"""
    path = path.strip('/')
    if not path:
        return (GUIDE / 'index.md').exists()
    return (GUIDE / path / 'index.md').exists() or (GUIDE / f'{path}.md').exists()


class GuidePagesExistTests(TestCase):
    def test_every_page_the_help_button_can_open_is_in_the_guide(self):
        pages = list(help_links.EXACT.values()) + [page for _prefix, page in help_links.PREFIXES]
        pages += list(help_links.FLOW_PAGES.values()) + [help_links.FRONT_PAGE]
        for path, _title in pages:
            self.assertTrue(guide_file_exists(path), f'no guide page for {path!r}')

    def test_every_guided_setup_has_its_own_help_page(self):
        for key in FlowRegistry.all():
            self.assertIn(key, help_links.FLOW_PAGES, f'the {key} setup opens the general Guided Setup page')


class HelpForTests(TestCase):
    def test_an_exact_name_wins(self):
        self.assertEqual(help_links.help_for('home')[0], '15-dashboard/')
        self.assertEqual(help_links.help_for('budget')[0], '07-budgets/')

    def test_a_family_of_pages_shares_a_guide_page(self):
        for name in ('loan-list', 'loan-detail', 'loan-create', 'pay-emi'):
            self.assertEqual(help_links.help_for(name)[0], '08-loans/', name)
        for name in ('recurring-list', 'recurring-edit'):
            self.assertEqual(help_links.help_for(name)[0], '05-transactions-recurring/', name)

    def test_a_guided_setup_opens_its_own_page(self):
        self.assertEqual(help_links.help_for('flow-detail', 'fd')[0], '13-tmr-flows/savings-and-investments/')
        self.assertEqual(help_links.help_for('flow-detail', 'loan')[0], '13-tmr-flows/debt/')

    def test_an_unknown_page_opens_the_front_page(self):
        self.assertEqual(help_links.help_for('some-new-page')[0], '')


class GuideUrlTests(TestCase):
    def render(self, template, language='en'):
        with translation.override(language):
            return Template('{% load help_tags %}' + template).render(Context({}))

    def test_english_pages_have_no_language_prefix(self):
        self.assertEqual(self.render("{% docs_url '07-budgets/' %}"), 'https://docs.trackmyrupee.com/07-budgets/')

    def test_hindi_and_marathi_pages_sit_under_their_language(self):
        self.assertEqual(self.render("{% docs_url '07-budgets/' %}", 'hi'), 'https://docs.trackmyrupee.com/hi/07-budgets/')
        self.assertEqual(self.render("{% docs_url '' %}", 'mr'), 'https://docs.trackmyrupee.com/mr/')

    @override_settings(DOCS_BASE_URL='http://localhost:8001')
    def test_the_guide_address_is_a_setting(self):
        self.assertEqual(self.render("{% docs_url 'x/' %}"), 'http://localhost:8001/x/')

    def test_a_help_link_goes_to_the_guide_in_a_new_tab(self):
        html = self.render("{% docs_help '07-budgets/' 'How budgets work' 'budgets-page' %}")
        self.assertIn('data-docs-path="07-budgets/"', html)
        self.assertIn('data-docs-src="budgets-page"', html)
        self.assertIn('href="https://docs.trackmyrupee.com/07-budgets/"', html)
        self.assertIn('target="_blank"', html)

    def test_a_help_link_without_a_label_is_the_question_mark(self):
        html = self.render("{% docs_help '07-budgets/' %}")
        self.assertIn('bi-question-circle', html)
        self.assertIn('aria-label="Help"', html)


class PagesCarryTheHelpTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('helper', 'h@example.com', 'pw')
        self.client.force_login(self.user)

    def get(self, name, **kwargs):
        return self.client.get(reverse(name, kwargs=kwargs)).content.decode()

    def test_the_top_bar_button_opens_the_guide_page_of_this_page(self):
        html = self.get('budget')
        self.assertIn('data-docs-path="07-budgets/" data-docs-src="topbar"', html)
        self.assertIn('data-docs-path="07-budgets/" data-docs-src="more-menu"', html)

    def test_a_guided_setup_page_opens_its_own_guide_page(self):
        html = self.get('flow-detail', key='fd')
        self.assertIn('data-docs-path="13-tmr-flows/savings-and-investments/" data-docs-src="topbar"', html)
        self.assertIn('data-docs-src="flow-detail"', html)

    def test_the_guided_setup_landing_links_to_its_guide(self):
        self.assertIn('data-docs-src="flows-landing"', self.get('tmr-flows'))

    def test_the_help_tile_in_the_phone_menu_is_a_plain_link(self):
        """(a data-bs-dismiss on a link makes Bootstrap cancel the click, so the guide would never open)"""
        import re
        html = self.get('budget')
        tag = re.search(r'<a [^>]*data-docs-src="more-menu"[^>]*>', html).group(0)
        self.assertNotIn('data-bs-dismiss', tag)
        self.assertIn('target="_blank"', tag)

    def test_the_top_bar_link_opens_the_guide_in_a_new_tab(self):
        html = self.get('budget')
        self.assertIn('href="https://docs.trackmyrupee.com/07-budgets/" target="_blank"', html)


class UntrackedGuideLinksTests(TestCase):
    def test_a_guide_link_inside_the_app_says_where_it_sits(self):
        """The app's own pages mark their guide links (data-docs-path, data-docs-src) so their use can be counted."""
        import re
        templates = Path(__file__).resolve().parents[2] / 'templates'
        skip = ('email',)          # (an email cannot run the script that counts the click)
        bare = []   
        for path in templates.rglob('*.html'):
            if any(word in str(path) for word in skip):
                continue
            for tag in re.findall(r'<a [^>]*href="https://docs\.trackmyrupee\.com[^>]*>', path.read_text()):
                if 'data-docs-path' not in tag:
                    bare.append(f'{path.relative_to(templates)}: {tag[:90]}')
        self.assertEqual(bare, [])


class GuideLinksWhereHelpIsNeededTests(TestCase):
    """A person with nothing yet, or a failed import, or a question for support, is pointed at the guide."""

    def setUp(self):
        self.user = User.objects.create_user('newcomer', 'n@example.com', 'pw')
        self.client.force_login(self.user)

    def page(self, name):
        return self.client.get(reverse(name)).content.decode()

    def test_the_empty_pages_point_at_their_guide(self):
        for name, source, path in (('goal-list', 'goals-empty', '12-goals/'),
                                   ('transfer-list', 'transfers-empty', '06-transfers/'),
                                   ('capital-event-list', 'capital-events-empty', '09-capital-events/'),
                                   ('recurring-list', 'recurring-empty', '05-transactions-recurring/')):
            html = self.page(name)
            self.assertIn(f'data-docs-path="{path}" data-docs-src="{source}"', html, name)

    def test_the_contact_page_offers_quick_answers_first(self):
        html = self.client.get(reverse('contact')).content.decode()
        for path in ('22-faq/', '01-getting-started/', '03-transactions-expenses/importing/', '23-plans-and-billing/'):
            self.assertIn(f'data-docs-path="{path}" data-docs-src="contact"', html)
        self.assertLess(html.index('Quick answers first'), html.index('Send us a message'))

    def test_a_failed_import_points_at_what_can_be_imported(self):
        from django.template.loader import render_to_string
        html = render_to_string('expenses/partials/_import_preview.html', {'error': 'Could not read that file.'})
        self.assertIn('data-docs-path="03-transactions-expenses/importing/" data-docs-src="import-error"', html)

    def test_a_good_import_preview_has_no_guide_link(self):
        from django.template.loader import render_to_string
        html = render_to_string('expenses/partials/_import_preview.html', {'rows': [], 'error': ''})
        self.assertNotIn('import-error', html)


class GuideLinkTrackingTests(TestCase):
    """The script that counts guide-link clicks and marks the visit is on every kind of page that has guide links."""

    def test_it_is_on_the_app_pages(self):
        user = User.objects.create_user('counter', 'c@example.com', 'pw')
        self.client.force_login(user)
        self.assertIn('js/guide_links.js', self.client.get(reverse('budget')).content.decode())

    def test_it_is_on_the_public_pages(self):
        for name in ('landing', 'contact', 'about'):
            self.assertIn('js/guide_links.js', self.client.get(reverse(name)).content.decode(), name)

    def test_the_script_sends_only_the_page_and_where_the_link_sits(self):
        script = (Path(__file__).resolve().parents[2] / 'static' / 'js' / 'guide_links.js').read_text()
        self.assertIn("posthog.capture('docs_link_clicked', { page: page, source: source })", script)
        self.assertIn("localStorage.getItem('cookieConsent') === 'accepted'", script)
        for word in ('email', 'username', 'amount'):
            self.assertNotIn(word, script.split('*/', 1)[1])


class MappingHonestyTests(TestCase):
    def test_every_named_page_in_the_mapping_is_a_real_url(self):
        from django.urls import get_resolver
        real = {name for name in get_resolver().reverse_dict if isinstance(name, str)}
        unknown = [name for name in help_links.EXACT if name not in real]
        self.assertEqual(unknown, [])

    def test_a_post_only_endpoint_is_not_mapped_as_if_it_were_a_page(self):
        self.assertNotIn('insights-settings', help_links.EXACT)

    def test_the_comments_describe_what_the_links_do(self):
        text = Path(help_links.__file__).read_text() + (Path(__file__).resolve().parents[2] / 'templates' / 'components' / 'help_link.html').read_text()
        self.assertNotIn('help_panel', text)
        self.assertNotIn('side panel', text)
