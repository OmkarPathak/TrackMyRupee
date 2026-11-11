"""One question at a time: what a person has answered or closed is kept on the account, and at most one of the
announcement, the daily-reminder card and the year in review banner is shown on a visit."""
from datetime import date, datetime, timedelta, timezone as dt_timezone
from decimal import Decimal
from unittest import mock

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import RequestFactory, SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import timezone

from expenses import prompts
from expenses.context_processors import pending_announcement
from expenses.models import Account, Announcement, Expense

NOW = datetime(2026, 12, 20, 10, 0, tzinfo=dt_timezone.utc)


class ApplyTests(SimpleTestCase):
    def test_a_cookie_choice_is_kept_with_the_policy_version(self):
        new = prompts.apply({}, 'cookie', 'choice', 'accepted', NOW)
        self.assertEqual(new['cookie'], {'choice': 'accepted', 'at': NOW.isoformat(), 'version': prompts.CONSENT_VERSION})

    def test_only_accepted_or_declined_is_a_cookie_choice(self):
        self.assertIsNone(prompts.apply({}, 'cookie', 'choice', 'maybe', NOW))
        self.assertIsNone(prompts.apply({}, 'cookie', 'dismiss', None, NOW))

    def test_the_reminder_card_backs_off_3_7_14_then_30_days_then_stops(self):
        state, waits = {}, []
        for _ in range(4):
            state = prompts.apply(state, 'reminder', 'dismiss', None, NOW)
            waits.append((datetime.fromisoformat(state['reminder']['until']) - NOW).days)
        self.assertEqual(waits, [3, 7, 14, 30])
        self.assertFalse(prompts.reminder_due(state, NOW + timedelta(days=400)))

    def test_the_card_is_due_again_once_the_wait_is_over(self):
        state = prompts.apply({}, 'reminder', 'dismiss', None, NOW)
        self.assertFalse(prompts.reminder_due(state, NOW + timedelta(days=2)))
        self.assertTrue(prompts.reminder_due(state, NOW + timedelta(days=3, minutes=1)))

    def test_turning_reminders_on_ends_the_card_for_good(self):
        state = prompts.apply({}, 'reminder', 'accept', None, NOW)
        self.assertFalse(prompts.reminder_due(state, NOW + timedelta(days=999)))

    def test_announcements_and_the_year_in_review_can_be_closed_and_nothing_else_is_a_key(self):
        self.assertIn('announcement:7', prompts.apply({}, 'announcement:7', 'dismiss', None, NOW))
        self.assertIn('yir:2026', prompts.apply({}, 'yir:2026', 'dismiss', None, NOW))
        for key in ('announcement:x', 'yir:26', 'profile', '../x', '', 'cookie;drop'):
            self.assertIsNone(prompts.apply({}, key, 'dismiss', None, NOW), key)

    def test_applying_never_changes_the_state_it_was_given(self):
        before = {'reminder': {'n': 1, 'until': NOW.isoformat()}}
        prompts.apply(before, 'reminder', 'dismiss', None, NOW)
        self.assertEqual(before, {'reminder': {'n': 1, 'until': NOW.isoformat()}})


class YearInReviewWindowTests(SimpleTestCase):
    def test_it_is_shown_from_15_december_to_31_january_only(self):
        cases = {date(2026, 12, 14): None, date(2026, 12, 15): 2026, date(2026, 12, 31): 2026,
                 date(2027, 1, 1): 2026, date(2027, 1, 31): 2026, date(2027, 2, 1): None, date(2026, 7, 4): None}
        for day, year in cases.items():
            self.assertEqual(prompts.yir_year(day), year, day)


class AnnouncementAudienceTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user('veteran', password='pw')
        self.user.date_joined = timezone.now() - timedelta(days=30)
        self.user.save()
        self.news = Announcement.objects.create(title='News', body='Hello', show_modal=True, status='SENT')

    def test_an_established_account_sees_news_from_after_it_joined(self):
        self.assertTrue(prompts.announcement_visible(self.user, self.news, {}))

    def test_nothing_in_the_first_day(self):
        self.user.date_joined = timezone.now() - timedelta(hours=3)
        self.assertFalse(prompts.announcement_visible(self.user, self.news, {}))

    def test_nothing_from_before_the_person_joined(self):
        self.user.date_joined = self.news.created_at + timedelta(days=5)
        self.assertFalse(prompts.announcement_visible(self.user, self.news, {}))

    def test_one_that_was_closed_is_not_shown_again_on_any_device(self):
        state = prompts.apply({}, f'announcement:{self.news.id}', 'dismiss')
        self.assertFalse(prompts.announcement_visible(self.user, self.news, state))

    def test_the_pages_context_follows_the_same_rule(self):
        request = RequestFactory().get('/')
        request.user = self.user
        self.assertEqual(pending_announcement(request), self.news)
        self.user.profile.prompt_state = prompts.apply({}, f'announcement:{self.news.id}', 'dismiss')
        self.assertIsNone(pending_announcement(request))


class InlineFlagsTests(TestCase):
    """The daily-reminder card and the year in review banner: at most one is decided, and neither beside an
    announcement or an unanswered cookie question."""

    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user('chooser', password='pw')
        self.user.date_joined = timezone.now() - timedelta(days=30)
        self.user.save()
        self.profile = self.user.profile
        self.profile.prompt_state = {'cookie': {'choice': 'accepted'}}

    def flags(self, *, data=True, yir=2026, tour=False):
        request = RequestFactory().get('/')
        request.user = self.user
        return prompts.inline_flags(request, has_any_data=data, yir_candidate_year=yir, tour_pending=tour)

    def test_the_reminder_card_goes_first_and_the_banner_waits_behind_it(self):
        flags = self.flags()
        self.assertEqual(flags, {'show_reminder_card': True, 'show_year_in_review': True, 'yir_waits_for_reminder': True})

    def test_without_a_year_in_review_only_the_card_shows(self):
        flags = self.flags(yir=None)
        self.assertTrue(flags['show_reminder_card'])
        self.assertFalse(flags['show_year_in_review'])

    def test_once_reminders_are_on_the_banner_has_the_page_to_itself(self):
        self.profile.prompt_state['reminder'] = {'accepted': True}
        self.assertEqual(self.flags(), {'show_reminder_card': False, 'show_year_in_review': True, 'yir_waits_for_reminder': False})

    def test_a_card_that_was_dismissed_recently_lets_the_banner_through(self):
        self.profile.prompt_state = prompts.apply(self.profile.prompt_state, 'reminder', 'dismiss')
        self.assertEqual(self.flags()['show_year_in_review'], True)
        self.assertEqual(self.flags()['show_reminder_card'], False)

    def test_a_closed_banner_is_not_offered_again(self):
        self.profile.prompt_state['reminder'] = {'accepted': True}
        self.profile.prompt_state['yir:2026'] = {'dismissed': 'x'}
        self.assertFalse(self.flags()['show_year_in_review'])

    def test_nothing_is_asked_before_there_is_something_logged(self):
        flags = self.flags(data=False, yir=None)
        self.assertFalse(flags['show_reminder_card'])

    def test_the_cookie_question_comes_first(self):
        self.profile.prompt_state = {}
        self.assertEqual(self.flags(), {'show_reminder_card': False, 'show_year_in_review': False, 'yir_waits_for_reminder': False})

    def test_an_announcement_takes_the_visit(self):
        Announcement.objects.create(title='News', body='Hello', show_modal=True, status='SENT')
        self.assertEqual(self.flags(), {'show_reminder_card': False, 'show_year_in_review': False, 'yir_waits_for_reminder': False})

    def test_the_first_tour_takes_the_visit(self):
        self.assertFalse(self.flags(tour=True)['show_reminder_card'])

    def test_the_demo_account_is_never_asked(self):
        self.user.username = 'demo'
        self.assertFalse(any(self.flags().values()))

    def test_it_costs_no_query_once_the_announcement_is_cached(self):
        request = RequestFactory().get('/')
        request.user = self.user
        pending_announcement(request)           # the page's own context processor does this first
        with self.assertNumQueries(0):
            prompts.inline_flags(request, has_any_data=True, yir_candidate_year=2026, tour_pending=False)


class EndpointTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('tapper', password='pw')
        self.client.force_login(self.user)
        self.url = reverse('prompt-state')

    def post(self, body, **extra):
        import json
        return self.client.post(self.url, data=json.dumps(body), content_type='application/json', **extra)

    def state(self):
        return type(self.user.profile).objects.get(user=self.user).prompt_state

    def test_a_signed_out_visitor_cannot_use_it(self):
        self.client.logout()
        self.assertEqual(self.post({'key': 'reminder', 'action': 'dismiss'}).status_code, 302)

    def test_a_dismissal_is_saved_on_the_account(self):
        self.assertEqual(self.post({'key': 'reminder', 'action': 'dismiss'}).status_code, 200)
        self.assertEqual(self.state()['reminder']['n'], 1)

    def test_a_cookie_choice_is_saved_with_a_time_and_the_policy_version(self):
        self.post({'key': 'cookie', 'action': 'choice', 'value': 'declined'})
        record = self.state()['cookie']
        self.assertEqual((record['choice'], record['version']), ('declined', prompts.CONSENT_VERSION))
        self.assertTrue(record['at'])

    def test_what_is_not_allowed_is_refused_and_changes_nothing(self):
        for body in ({'key': 'cookie', 'action': 'choice', 'value': 'x'}, {'key': 'tier', 'action': 'dismiss'},
                     {'key': 'reminder', 'action': 'delete'}, {}, {'key': ['a'], 'action': 'dismiss'}):
            self.assertEqual(self.post(body).status_code, 400, body)
        self.assertEqual(self.state(), {})

    def test_bad_json_and_oversized_bodies_are_refused(self):
        self.assertEqual(self.client.post(self.url, data='{not json', content_type='application/json').status_code, 400)
        self.assertEqual(self.post({'key': 'reminder', 'action': 'dismiss', 'value': 'x' * 2000}).status_code, 400)

    def test_nothing_else_on_the_profile_changes(self):
        profile = self.user.profile
        profile.tier = 'PRO'
        profile.save()
        self.post({'key': 'reminder', 'action': 'accept'})
        profile.refresh_from_db()
        self.assertEqual(profile.tier, 'PRO')

    def test_a_forged_request_without_the_csrf_token_is_refused(self):
        from django.test import Client
        strict = Client(enforce_csrf_checks=True)
        strict.force_login(self.user)
        response = strict.post(self.url, data='{"key": "reminder", "action": "dismiss"}', content_type='application/json')
        self.assertEqual(response.status_code, 403)


class PagesTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user('pager', 'p@example.com', 'pw')
        profile = self.user.profile
        profile.has_seen_tutorial = True
        profile.consent_granted = True
        profile.save()
        self.client.force_login(self.user)

    def html(self):
        return self.client.get(reverse('settings-home')).content.decode()

    def test_the_cookie_choice_kept_on_the_account_is_applied_before_analytics_starts(self):
        profile = self.user.profile
        profile.prompt_state = {'cookie': {'choice': 'declined'}}
        profile.save()
        html = self.html()
        self.assertIn("localStorage.setItem('cookieConsent', 'declined')", html)
        self.assertLess(html.index("localStorage.setItem('cookieConsent', 'declined')"), html.index('var GA_ID'))
        self.assertIn('data-server-choice="declined"', html)

    def test_with_no_answer_yet_nothing_is_forced(self):
        html = self.html()
        self.assertNotIn("localStorage.setItem('cookieConsent'", html.split('var GA_ID')[0])
        self.assertIn('data-server-choice=""', html)

    def test_the_four_overlapping_install_and_reminder_prompts_are_one_card(self):
        html = self.html()
        for gone in ('installAppContainer', 'iosInstallModal', 'pwa-promo-card', 'Get Daily Reminders', 'Never Miss an Expense'):
            self.assertNotIn(gone, html, gone)

    def test_signing_in_shows_no_toast_about_it(self):
        self.client.logout()
        response = self.client.post(reverse('account_login'), {'login': 'pager', 'password': 'pw'}, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('Successfully signed in', response.content.decode())
        self.assertEqual([str(m) for m in response.context['messages']], [])

    def test_the_dashboard_shows_the_card_once_and_dismissing_it_is_remembered(self):
        account = Account.objects.create(user=self.user, name='Cash', account_type='CASH_WALLET', balance=Decimal('10'), currency='₹')
        Expense.objects.create(user=self.user, date=date.today(), amount=Decimal('5'), category='Food', currency='₹', account=account, payment_method='Cash')
        profile = self.user.profile
        profile.prompt_state = {'cookie': {'choice': 'accepted'}}
        profile.save()
        cache.clear()
        self.assertIn('data-reminder-optin data-variant="card"', self.client.get(reverse('home')).content.decode())
        self.client.post(reverse('prompt-state'), data='{"key": "reminder", "action": "dismiss"}', content_type='application/json')
        cache.clear()
        self.assertNotIn('data-reminder-optin data-variant="card"', self.client.get(reverse('home')).content.decode())
