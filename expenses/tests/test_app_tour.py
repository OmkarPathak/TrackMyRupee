"""The app tour: shown once to someone who has just finished onboarding, in a desktop and a phone form."""
import re
from pathlib import Path

from django.contrib.auth.models import User
from django.contrib.staticfiles import finders
from django.test import TestCase
from django.urls import reverse

from expenses.models import Account, UserProfile
from expenses.onboarding_v2 import mark_onboarding_finished


def tutorial_js() -> str:
    return Path(finders.find('js/tutorial.js')).read_text(encoding='utf-8')


def step_block(js: str, name: str) -> str:
    return re.search(r'const %s = \[(.*?)\n\];' % name, js, re.S).group(1)


def selectors(block: str) -> list[str]:
    return re.findall(r"element:\s*'([^']+)'", block)


class TourBase(TestCase):
    def make_user(self, name='tour-user', finished=True):
        user = User.objects.create_user(name, password='pass')
        profile, _ = UserProfile.objects.get_or_create(user=user, defaults={'currency': '₹'})
        profile.consent_granted = True
        profile.save()
        Account.objects.create(user=user, name='Bank', account_type='SAVINGS_ACCOUNT', balance=1000, currency='₹')
        if finished:
            mark_onboarding_finished(user)
        user.refresh_from_db()
        return user

    def home(self, **params):
        response = self.client.get(reverse('home'), params)
        self.assertEqual(response.status_code, 200)
        return response


class TourFlagTests(TourBase):
    def test_finishing_onboarding_queues_the_tour(self):
        user = self.make_user()
        self.assertTrue(user.profile.app_tour_pending)
        self.assertTrue(user.profile.has_seen_tutorial)

    def test_an_account_that_never_finished_onboarding_has_no_tour_queued(self):
        user = self.make_user('unfinished', finished=False)
        self.assertFalse(user.profile.app_tour_pending)

    def test_the_first_dashboard_visit_after_onboarding_starts_the_tour(self):
        self.client.force_login(self.make_user())
        response = self.home()
        self.assertTrue(response.context['show_tutorial'])
        self.assertContains(response, 'js/tutorial.js')

    def test_finishing_or_skipping_the_tour_stops_it_coming_back(self):
        user = self.make_user()
        self.client.force_login(user)
        self.assertEqual(self.client.post(reverse('complete-tutorial')).status_code, 200)
        user.profile.refresh_from_db()
        self.assertFalse(user.profile.app_tour_pending)
        self.assertTrue(user.profile.has_seen_tutorial)
        response = self.home()
        self.assertFalse(response.context['show_tutorial'])
        self.assertNotContains(response, 'js/tutorial.js')

    def test_an_existing_account_is_not_shown_the_tour(self):
        user = self.make_user('veteran', finished=False)
        UserProfile.objects.filter(user=user).update(has_seen_tutorial=True)       # as every account before this change
        self.client.force_login(user)
        self.assertFalse(self.home().context['show_tutorial'])

    def test_settings_can_replay_it_any_time(self):
        user = self.make_user('replay')
        self.client.force_login(user)
        self.client.post(reverse('complete-tutorial'))
        self.assertTrue(self.home(tour='true').context['show_tutorial'])
        self.assertContains(self.client.get(reverse('settings-home')), reverse('home') + '?tour=true')

    def test_completing_needs_a_post(self):
        self.client.force_login(self.make_user('getter'))
        self.assertEqual(self.client.get(reverse('complete-tutorial')).status_code, 400)


class TourStepTests(TourBase):
    """The tour points at elements by selector; if one is renamed the tour would silently lose that step."""

    def setUp(self):
        self.js = tutorial_js()
        self.client.force_login(self.make_user())
        self.html = self.home().content.decode()

    def present(self, selector):
        if selector.startswith('#'):
            return f'id="{selector[1:]}"' in self.html
        return re.search(r'class="[^"]*\b%s\b' % re.escape(selector[1:]), self.html) is not None

    def test_there_are_two_tours_and_neither_borrows_the_others_targets(self):
        desktop = selectors(step_block(self.js, 'desktopSteps'))
        mobile = selectors(step_block(self.js, 'mobileSteps'))
        self.assertGreaterEqual(len(desktop), 6)
        self.assertGreaterEqual(len(mobile), 3)
        self.assertTrue(all(not s.startswith('.mobile-') and s != '#global-fab' for s in desktop), desktop)
        self.assertTrue(all(not s.startswith('#tour-nav-') and s != '#sidebarSearchTrigger' for s in mobile), mobile)

    def test_every_step_points_at_something_on_the_page(self):
        for name in ('desktopSteps', 'mobileSteps'):
            for selector in selectors(step_block(self.js, name)):
                self.assertTrue(self.present(selector), f'{name}: nothing matches {selector}')

    def test_each_tour_opens_with_a_welcome_that_points_at_nothing(self):
        for name in ('desktopSteps', 'mobileSteps'):
            self.assertTrue(step_block(self.js, name).lstrip().startswith('WELCOME_STEP'), name)

    def test_the_tour_chooses_by_the_width_at_which_the_sidebar_appears(self):
        self.assertIn("(min-width: 992px)", self.js)
        self.assertIn('usesSidebar() ? desktopSteps : mobileSteps', self.js)
        self.assertIn('d-lg-none', self.html)            # the phone bar is what is shown below that width
        self.assertIn('id="sidebar"', self.html)


class TourTranslationTests(TestCase):
    def test_every_word_of_the_tour_is_in_hindi_and_marathi(self):
        try:
            import polib
        except ImportError:
            self.skipTest('polib is not installed')
        js = tutorial_js()
        strings = set(re.findall(r"gettext\('((?:[^'\\]|\\.)*)'\)", js))
        self.assertGreater(len(strings), 15)
        root = Path(__file__).resolve().parents[2] / 'locale'
        for lang in ('hi', 'mr'):
            catalogue = {e.msgid: e.msgstr for e in polib.pofile(str(root / lang / 'LC_MESSAGES' / 'djangojs.po'))}
            for text in sorted(strings):
                text = text.replace("\\'", "'")
                self.assertTrue(catalogue.get(text), f'{lang}: no translation for {text!r}')
