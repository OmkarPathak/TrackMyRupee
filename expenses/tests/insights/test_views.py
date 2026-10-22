"""The panel fragment, the actions on a card, Settings, and the dashboard switch-over."""
import uuid
from datetime import timedelta
from unittest import mock

from django.contrib.auth.models import User
from django.test import override_settings
from django.urls import reverse

from expenses.insights import engine, prefs, sampling
from expenses.insights.types import Action, Module, Surface
from expenses.models import Account, InsightFeedback, InsightState, UserProfile

from .base import ENGINE_ON, TODAY, D, InsightBase, temp_rule
from .test_engine import StoredBase, insight


@ENGINE_ON
class PanelTests(StoredBase):
    def panel(self, query=''):
        return self.client.get(reverse('insights-panel') + query)

    def test_the_panel_refreshes_stale_data_once_then_serves_it(self):
        self.life()
        with mock.patch('expenses.insights.views.engine.refresh', wraps=engine.refresh) as refresh:
            with mock.patch('django.utils.timezone.localdate', return_value=TODAY):
                html = self.panel().content.decode()
                self.panel()
        self.assertEqual(refresh.call_count, 1)
        self.assertIn('Spending is running a little higher', html)
        self.assertIn('data-insight', html)
        self.assertEqual(InsightState.objects.filter(user=self.user).count(), 5)

    def test_it_requires_a_login_and_the_engine_to_be_on(self):
        self.client.logout()
        self.assertEqual(self.panel().status_code, 302)
        self.client.force_login(self.user)
        with override_settings(INSIGHTS_ENGINE_MODE='off'):
            self.assertEqual(self.panel().content, b'')

    def test_an_unknown_surface_is_refused(self):
        self.assertEqual(self.panel('?surface=nope').status_code, 400)

    def test_empty_states_are_friendly(self):
        self.income(1000, TODAY)
        html = self.panel().content.decode()
        self.assertIn('still learning your patterns', html)
        prefs.save(self.profile, enabled=False, modules_off=[], notify='normal', push=True)
        self.assertIn('switched off', self.panel().content.decode())

    def test_html_in_an_account_name_is_escaped(self):
        Account.objects.create(user=self.user, name='<script>alert(1)</script>', account_type='FIXED_DEPOSIT',
                               currency='₹', balance=D('1000'), deposit_maturity_date=TODAY + timedelta(days=5))
        with mock.patch('django.utils.timezone.localdate', return_value=TODAY):
            self.income(1000, TODAY)
            html = self.panel().content.decode()
        self.assertNotIn('<script>alert(1)</script>', html)
        self.assertIn('&lt;script&gt;', html)

    def test_the_cards_are_accessible(self):
        self.life()
        with mock.patch('django.utils.timezone.localdate', return_value=TODAY):
            html = self.panel().content.decode()
        self.assertIn('aria-labelledby="insight-title-', html)
        self.assertIn('aria-label="More options for this insight"', html)
        self.assertIn('aria-controls="insight-menu-', html)
        self.assertNotIn('dropdown-menu', html)             # an inline row, not an overlay a scrolling card could clip
        self.assertIn('bi-hand-thumbs-up', html)                    # one compact line: two words and two icons
        self.assertIn('aria-label="This was helpful"', html)         # the icons are named for screen readers
        self.assertIn('hx-target="#insight-opts-', html)             # feedback answers in the options row, the card stays
        self.assertIn('visually-hidden', html)                 # the tone is said in words, not colour alone

    def test_hindi_renders_in_devanagari(self):
        self.life()
        UserProfile.objects.filter(pk=self.profile.pk).update(language='hi')
        with mock.patch('django.utils.timezone.localdate', return_value=TODAY):
            html = self.client.get(reverse('insights-panel'), HTTP_ACCEPT_LANGUAGE='hi').content.decode()
        self.assertIn('खर्च थोड़ा ज़्यादा चल रहा है', html)
        self.assertIn('अभी नहीं', html)


@ENGINE_ON
class ActionTests(StoredBase):
    def setUp(self):
        super().setUp()
        temp_rule(self, id='cashflow.view', module=Module.CASHFLOW, surfaces={Surface.DASHBOARD},
                  sample=sampling.default_sample)(lambda ctx: [])
        self.row = self.store('cashflow.view', insight(rule_id='cashflow.view', action=Action('Open budgets', '/budget/')))

    def post(self, name, **kw):
        return self.client.post(reverse(name, args=[self.row.uuid, *kw.get('extra', [])]))

    def test_dismiss_and_snooze_swap_the_card_away_without_a_page_load(self):
        self.assertEqual(self.post('insights-dismiss').content, b'')
        self.assertIsNotNone(InsightState.objects.get(pk=self.row.pk).dismissed_at)
        self.assertEqual(self.post('insights-snooze').status_code, 200)
        self.assertIsNotNone(InsightState.objects.get(pk=self.row.pk).snoozed_until)

    def test_actions_are_post_only(self):
        self.assertEqual(self.client.get(reverse('insights-dismiss', args=[self.row.uuid])).status_code, 405)

    def test_nobody_can_touch_someone_elses_insight(self):
        other = User.objects.create_user('other', password='pw')
        self.client.force_login(other)
        for name in ('insights-dismiss', 'insights-snooze'):
            self.assertEqual(self.post(name).status_code, 404)
        self.assertEqual(self.client.get(reverse('insights-go', args=[self.row.uuid])).status_code, 404)
        self.assertIsNone(InsightState.objects.get(pk=self.row.pk).dismissed_at)

    def test_unknown_ids_are_not_found(self):
        self.assertEqual(self.client.post(reverse('insights-dismiss', args=[uuid.uuid4()])).status_code, 404)

    def test_following_the_next_step_counts_as_resolved(self):
        response = self.client.get(reverse('insights-go', args=[self.row.uuid]))
        self.assertRedirects(response, '/budget/', fetch_redirect_response=False)
        self.assertIsNotNone(InsightState.objects.get(pk=self.row.pk).acted_at)

    def test_go_cannot_be_turned_into_an_open_redirect(self):
        InsightState.objects.filter(pk=self.row.pk).update(payload={**self.row.payload, 'action': ['x', 'https://evil.example/', None]})
        response = self.client.get(reverse('insights-go', args=[self.row.uuid]) + '?to=https://evil.example/')
        self.assertEqual(response['Location'], reverse('home'))

    def test_feedback_is_one_yes_or_no(self):
        yes = self.post('insights-helpful', extra=['yes']).content.decode()
        self.assertIn('glad it was useful', yes)
        self.assertNotIn('data-insight', yes)               # it replaces the options row, never the card
        self.assertTrue(InsightFeedback.objects.get(user=self.user).helpful)
        self.assertIn('say less of what is not useful', self.post('insights-helpful', extra=['no']).content.decode())
        self.assertFalse(InsightFeedback.objects.get(user=self.user).helpful)
        self.assertEqual(self.post('insights-helpful', extra=['maybe']).status_code, 404)


class SettingsTests(InsightBase):
    def post(self, **data):
        return self.client.post(reverse('insights-settings'), data)

    def test_settings_are_saved_and_unknown_things_ignored(self):
        self.post(enabled='on', modules=['budgets', 'bogus'], notify='quiet')
        current = prefs.get(UserProfile.objects.get(pk=self.profile.pk))
        self.assertTrue(current['enabled'])
        self.assertFalse(current['push'])
        self.assertEqual(current['notify'], 'quiet')
        self.assertIn('expenses', current['modules_off'])
        self.assertNotIn('budgets', current['modules_off'])
        self.assertNotIn('bogus', current['modules_off'])

    def test_unchecking_everything_switches_the_feature_off(self):
        self.post(notify='bogus')
        current = prefs.get(UserProfile.objects.get(pk=self.profile.pk))
        self.assertFalse(current['enabled'])
        self.assertEqual(current['notify'], 'normal')

    def test_saving_marks_the_stored_insights_stale(self):
        UserProfile.objects.filter(pk=self.profile.pk).update(insights_stale=False)
        self.post(enabled='on', notify='normal')
        self.assertTrue(UserProfile.objects.get(pk=self.profile.pk).insights_stale)

    def test_the_settings_page_shows_the_section_with_its_topics(self):
        html = self.client.get(reverse('profile-settings')).content.decode()
        self.assertIn('id="insights"', html)
        self.assertIn('name="modules" value="budgets"', html)
        self.assertIn('Save insight settings', html)

    def test_the_section_is_translated(self):
        UserProfile.objects.filter(pk=self.profile.pk).update(language='mr')
        html = self.client.get(reverse('profile-settings'), HTTP_ACCEPT_LANGUAGE='mr').content.decode()
        self.assertIn('अंतर्दृष्टी सेटिंग्ज जतन करा', html)


class DashboardSwitchTests(InsightBase):
    def home(self):
        return self.client.get(reverse('home')).content.decode()

    @override_settings(INSIGHTS_ENGINE_MODE='off')
    def test_off_keeps_the_legacy_cards(self):
        html = self.home()
        self.assertIn('Actionable Alerts', html)
        self.assertNotIn(reverse('insights-panel'), html)

    @ENGINE_ON
    def test_on_replaces_them_with_one_panel_that_loads_after_paint(self):
        html = self.home()
        self.assertNotIn('Actionable Alerts', html)
        self.assertIn(reverse('insights-panel') + '?surface=dashboard', html)
        self.assertIn('hx-trigger="load"', html)
        self.assertIn('skeleton-block', html)                  # space is reserved, so nothing jumps

    @override_settings(INSIGHTS_ENGINE_MODE='off', INSIGHTS_ENGINE_USER_IDS=frozenset())
    def test_a_named_user_gets_the_engine_while_it_is_off_for_everyone_else(self):
        with override_settings(INSIGHTS_ENGINE_USER_IDS=frozenset({self.user.pk})):
            self.assertIn(reverse('insights-panel'), self.home())

    @ENGINE_ON
    def test_the_panel_card_is_bounded_so_many_insights_scroll_inside_it(self):
        html = self.home()
        self.assertIn('insights-card', html)
        self.assertIn('.insights-card { max-height: 30rem; overflow: hidden; }', html)       # rounded corners clip the body
        self.assertIn('overflow-y: auto', html)
        self.assertIn('position: absolute; inset: 0', html)      # beside the metrics it takes their height

    @ENGINE_ON
    def test_the_dashboard_adds_no_queries_for_insights(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        self.home()
        with CaptureQueriesContext(connection) as ctx:
            self.home()
        self.assertFalse([q for q in ctx.captured_queries if 'expenses_insightstate' in q['sql']])
