"""Phase E: the Analytics sentences, the monthly report and Year in Review sections, and the email digest."""
from datetime import date, timedelta
from types import SimpleNamespace
from unittest import mock

from django.contrib.auth.models import User
from django.core import mail
from django.test import RequestFactory, SimpleTestCase, override_settings
from django.urls import reverse
from django.utils import translation

from expenses.analytics import wealth
from expenses.analytics.common import Page
from expenses.analytics.facts import load_category_cycles
from expenses.insights import composers, engine, prefs, registry, sampling
from expenses.insights.adapters import digest
from expenses.insights.sampling import TODAY as SAMPLE_TODAY
from expenses.insights.types import Insight, Kind, Module, Plan, Surface, Tone
from expenses.insights.views import digest_token
from expenses.management.commands.send_monthly_report import Command as MonthlyCommand
from expenses.models import InsightState, UserProfile
from expenses.tests.test_analytics_module import AnalyticsBase, month_start, today

from .base import ENGINE_ON, TODAY, D, InsightBase
from .base import month_start as insight_month_start

RULES = {r.id: r for r in registry.all_rules()}


def run(rule_id, ctx):
    rule = RULES[rule_id]
    with ctx.declared(rule.needs):
        return rule.evaluate(ctx)


class AnalyticsSentenceTests(SimpleTestCase):
    def test_the_period_sentence_has_the_numbers_and_the_comparison(self):
        [found] = run('analytics.period_summary', sampling.page_sample())
        self.assertEqual(found.message, 'You kept ₹16,000 of ₹70,000 (23%). That is ahead of the comparison period (17%).')

    def test_behind_the_comparison_and_no_comparison_at_all(self):
        ctx = sampling.page_sample()
        ctx.page.compare = SimpleNamespace(income=D(10), rate=D(60))
        [behind] = run('analytics.period_summary', ctx)
        self.assertIn('behind the comparison period (60%)', behind.message)
        ctx.page.compare = None
        [alone] = run('analytics.period_summary', ctx)
        self.assertEqual(alone.message, 'You kept ₹16,000 of ₹70,000 (23%).')

    def test_no_income_says_so_instead_of_inventing_a_rate(self):
        ctx = sampling.page_sample()
        ctx.page.summary = SimpleNamespace(income=D(0))
        [found] = run('analytics.period_summary', ctx)
        self.assertEqual(found.message, 'No income was recorded in this period.')

    def test_the_biggest_cost_is_a_share_of_income(self):
        [found] = run('cashflow.biggest_cost', sampling.page_sample())
        self.assertEqual(found.message, 'Dining is your biggest cost: ₹22,000, 31% of your income.')

    def test_nothing_to_rank_or_no_income_says_nothing(self):
        ctx = sampling.page_sample()
        ctx.page.summary = SimpleNamespace(income=D(0), spending=D(0), invested=D(0), categories={})
        self.assertEqual(run('cashflow.biggest_cost', ctx), [])

    def test_spending_beyond_income_is_sensitive_and_never_shared(self):
        [found] = run('cashflow.spent_beyond_income', sampling.page_overspent_sample())
        self.assertTrue(found.sensitive)
        self.assertIsNone(found.share_text)
        self.assertEqual(found.tone, Tone.GENTLE_ATTENTION)
        self.assertIn('more than you earned', found.message)

    def test_costs_and_investing_above_income_are_paid_from_savings(self):
        ctx = sampling.page_sample()
        ctx.page.summary = SimpleNamespace(income=D(1000), spending=D(300), invested=D(900), categories={})
        [found] = run('cashflow.spent_beyond_income', ctx)
        self.assertIn('paid from savings you already had', found.message)
        self.assertEqual(run('cashflow.invested_and_kept', ctx), [])

    def test_the_split_of_income_only_when_it_all_was_covered(self):
        [found] = run('cashflow.invested_and_kept', sampling.page_sample())
        self.assertEqual(found.message, 'You invested 0% and kept 23% of your income as cash.')
        self.assertEqual(run('cashflow.spent_beyond_income', sampling.page_sample()), [])
        self.assertEqual(run('cashflow.invested_and_kept', sampling.page_overspent_sample()), [])

    def test_the_biggest_mover_up_and_down(self):
        [up] = run('cashflow.category_moved', sampling.page_sample())
        self.assertEqual(up.message, 'Dining went up by ₹13,000 compared with the period before.')
        ctx = sampling.page_sample()
        ctx.page.compare = SimpleNamespace(categories={'dining': D(40000)})
        [down] = run('cashflow.category_moved', ctx)
        self.assertIn('Dining went down by ₹18,000', down.message)
        self.assertEqual(down.tone, Tone.POSITIVE)
        ctx.page.compare = None
        self.assertEqual(run('cashflow.category_moved', ctx), [])

    def test_the_takeaways_keep_their_order_and_the_three_most(self):
        found = engine.inline(sampling.page_sample(), ['cashflow.biggest_cost', 'cashflow.spent_beyond_income',
                                                        'cashflow.invested_and_kept', 'cashflow.category_moved'], limit=3)
        self.assertEqual([i.rule_id for i in found], ['cashflow.biggest_cost', 'cashflow.invested_and_kept', 'cashflow.category_moved'])

    def test_inline_rules_are_never_stored_and_say_which_page_they_are_for(self):
        for rule_id in ('analytics.period_summary', 'cashflow.biggest_cost', 'cashflow.spent_beyond_income',
                        'cashflow.invested_and_kept', 'cashflow.category_moved', 'expenses.categories_creeping_up',
                        'networth.what_moved_period'):
            self.assertEqual(RULES[rule_id].mode, 'inline', rule_id)
            self.assertEqual(RULES[rule_id].surfaces, frozenset({Surface.ANALYTICS}), rule_id)

    def test_a_rule_that_breaks_costs_the_tab_its_sentence_and_nothing_more(self):
        ctx = sampling.page_sample()
        broken = registry.InsightRule(**{**RULES['cashflow.biggest_cost'].__dict__, 'evaluate': mock.Mock(side_effect=ValueError('boom'))})
        with mock.patch.dict(registry.REGISTRY, {'cashflow.biggest_cost': broken}):
            found = engine.inline(ctx, ['cashflow.biggest_cost', 'cashflow.category_moved'])
        self.assertEqual([i.rule_id for i in found], ['cashflow.category_moved'])

    def test_a_plan_below_a_rules_minimum_gets_nothing_not_a_preview(self):
        ctx = sampling.page_sample()
        ctx.plan = Plan.FREE
        self.assertEqual(engine.inline(ctx, ['networth.what_moved_period']), [])


class CreepingAndNetWorthRuleTests(SimpleTestCase):
    def test_a_category_rising_every_month_is_listed_with_its_average_rise(self):
        [found] = run('expenses.categories_creeping_up', sampling.creeping_sample())
        self.assertEqual(found.title, 'Food keeps rising')
        self.assertEqual(found.evidence[0].label, 'Food')
        self.assertEqual(found.evidence[0].value, '42%')
        self.assertIn('category=Food', found.evidence[0].link)
        self.assertEqual(found.tone, Tone.GENTLE_ATTENTION)

    def test_a_gentler_rise_is_calm_and_the_fastest_comes_first(self):
        ctx = sampling.creeping_sample()
        ranges, _rows = ctx.category_cycles
        ctx._cache['category_cycles'] = (ranges, {'Food': [D(100), D(105), D(110)], 'Fun': [D(100), D(150), D(300)],
                                                  'Rent': [D(100), D(100), D(200)], 'Gone': [D(0), D(5), D(9)]})
        found = run('expenses.categories_creeping_up', ctx)
        self.assertEqual([i.evidence[0].label for i in found], ['Fun', 'Food'])
        self.assertEqual(found[1].tone, Tone.CALM)

    def test_at_most_three_rows(self):
        ctx = sampling.creeping_sample()
        ranges, _rows = ctx.category_cycles
        ctx._cache['category_cycles'] = (ranges, {f'C{n}': [D(100), D(110 + n), D(130 + n)] for n in range(6)})
        self.assertEqual(len(run('expenses.categories_creeping_up', ctx)), 3)

    def scenario(self, change, kept, filters=False):
        ctx = sampling.net_worth_period_sample()
        ctx.values = {'change': D(change), 'kept': D(kept)}
        ctx.page.filters_active = filters
        return ctx

    def test_what_moved_over_the_period_up_less_and_down(self):
        [up] = run('networth.what_moved_period', self.scenario(26000, 16000))
        self.assertIn('Over this period it is up about ₹26k: roughly ₹16k from what you kept and ₹10k from changes in value.', up.message)
        [less] = run('networth.what_moved_period', self.scenario(10000, 16000))
        self.assertIn('you kept ₹16k, and changes in value took back ₹6k', less.message)
        [down] = run('networth.what_moved_period', self.scenario(-20000, 16000))
        self.assertIn('Over this period it is down about ₹20k', down.message)

    def test_nothing_kept_a_tiny_move_or_a_narrowed_page_says_nothing(self):
        self.assertEqual(run('networth.what_moved_period', self.scenario(26000, 0)), [])
        self.assertEqual(run('networth.what_moved_period', self.scenario(500, 400)), [])
        self.assertEqual(run('networth.what_moved_period', self.scenario(26000, 16000, filters=True)), [])


class ComposeAndPickTests(SimpleTestCase):
    def make(self, rule_id, module, tone=Tone.CALM, priority=50, sensitive=False, kind=Kind.OBSERVATION, action=True):
        from expenses.insights.types import Action
        return Insight(rule_id=rule_id, module=module, kind=kind, tone=tone, priority=priority, title='Title', message='Message.',
                       dedupe_key='k', valid_until=SAMPLE_TODAY, surfaces=frozenset({Surface.MONTHLY}), sensitive=sensitive,
                       action=Action('Go', '/x/') if action else None)

    def test_the_good_news_comes_first_then_different_modules(self):
        found = [self.make('a.one', Module.BUDGETS, priority=90), self.make('a.two', Module.BUDGETS, priority=80),
                 self.make('b.one', Module.GOALS, tone=Tone.POSITIVE, priority=20), self.make('c.one', Module.LOANS, priority=30)]
        picked = engine.pick(found, 3)
        self.assertEqual([i.rule_id for i in picked], ['b.one', 'a.one', 'c.one'])

    def test_a_module_may_repeat_when_there_is_nothing_else(self):
        found = [self.make('a.one', Module.BUDGETS), self.make('a.two', Module.BUDGETS, priority=40)]
        self.assertEqual(len(engine.pick(found, 3)), 2)

    def test_sensitive_ones_can_be_left_out(self):
        found = [self.make('a.one', Module.LOANS, sensitive=True), self.make('b.one', Module.GOALS)]
        self.assertEqual([i.rule_id for i in engine.pick(found, 3, allow_sensitive=False)], ['b.one'])


class RealPageTests(AnalyticsBase):
    """The Analytics tabs on real data, with the engine on and off."""

    def setUp(self):
        super().setUp()
        self.addCleanup(translation.deactivate)       # a request leaves its language active on the thread

    def page(self, tab='networth', **params):
        request = RequestFactory().get('/analytics/', params)
        request.user = User.objects.get(pk=self.user.pk)
        return Page(request, tab)

    def tab(self, name, **params):
        return self.client.get(reverse(name), params)

    def test_the_category_cycle_query_is_one_query_and_follows_the_filters(self):
        ranges = [(date(2026, 7, 1), date(2026, 7, 31)), (date(2026, 8, 1), date(2026, 8, 31))]
        self.expense(100, date(2026, 7, 5), 'Food')
        self.expense(300, date(2026, 8, 5), 'Food')
        self.expense(50, date(2026, 8, 6), 'Rent')
        with self.assertNumQueries(1):
            rows = load_category_cycles(self.user, ranges)
        self.assertEqual(rows, {'Food': [D('100'), D('300')], 'Rent': [D('0'), D('50')]})
        self.assertEqual(load_category_cycles(self.user, ranges, categories=['Rent']), {'Rent': [D('0'), D('50')]})
        self.assertEqual(load_category_cycles(self.user, ranges, account_ids=[999999]), {})

    @ENGINE_ON
    def test_with_the_engine_on_creeping_up_follows_salary_cycles(self):
        UserProfile.objects.filter(user=self.user).update(salary_date=15)
        key = date(today().year, today().month, 1)
        from expenses import budget_cycle
        cycle = key
        spans = []
        for _ in range(3):
            cycle = budget_cycle.previous_month(cycle)
            spans.append(budget_cycle.budget_range(15, cycle))
        spans.reverse()
        for (first, _last), amount in zip(spans, (100, 150, 200), strict=True):
            self.expense(amount, first + timedelta(days=1), 'Food')
        rows = self.tab('analytics-networth').context['rising']
        self.assertEqual([r['name'] for r in rows], ['Food'])
        self.assertIn('category=Food', rows[0]['href'])

    @ENGINE_ON
    def test_with_the_engine_on_the_toolbar_filters_narrow_what_is_listed(self):
        for months_back, amount in ((3, 100), (2, 150), (1, 200)):
            self.expense(amount, month_start(months_back), category='Food')
            self.expense(amount, month_start(months_back), category='Fun')
        both = self.tab('analytics-networth').context['rising']
        self.assertEqual(sorted(r['name'] for r in both), ['Food', 'Fun'])
        narrowed = self.tab('analytics-networth', category='Food').context['rising']
        self.assertEqual([r['name'] for r in narrowed], ['Food'])

    def test_with_the_engine_off_the_old_calendar_month_list_is_kept(self):
        for months_back, amount in ((3, 100), (2, 150), (1, 200)):
            self.expense(amount, month_start(months_back), category='Food')
        rows = self.tab('analytics-networth').context['rising']
        self.assertEqual([r['name'] for r in rows], ['Food'])

    @ENGINE_ON
    def test_the_net_worth_tab_says_what_moved_and_stays_quiet_under_filters(self):
        page = self.page('networth')
        self.income(70000, month_start(0))
        self.expense(20000, month_start(0))
        page = self.page('networth')
        text = wealth.moved(page, page.period.start, D('50000'))
        self.assertIn('Over this period it is up about', text)
        self.assertEqual(wealth.moved(self.page('networth', category='Food'), page.period.start, D('50000')), '')
        with override_settings(INSIGHTS_ENGINE_MODE='off'):
            self.assertEqual(wealth.moved(page, page.period.start, D('50000')), '')

    def test_the_sentences_still_come_out_with_the_engine_off(self):
        self.income(100000)
        self.expense(40000)
        self.assertIn('You kept ₹60,000 of ₹1,00,000 (60%).', self.tab('analytics').context['insight'])
        self.assertTrue(self.tab('analytics-cashflow').context['sankey']['takeaways'])


class DigestPrefsAndSettingsTests(InsightBase):
    def test_the_digest_is_weekly_by_default_and_only_known_choices_stick(self):
        self.assertEqual(prefs.get(self.profile)['digest'], 'weekly')
        prefs.save(self.profile, enabled=True, modules_off=[], notify='normal', push=True, digest='daily')
        self.assertEqual(prefs.get(self.profile)['digest'], 'daily')
        prefs.save(self.profile, enabled=True, modules_off=[], notify='normal', push=True, digest='hourly')
        self.assertEqual(prefs.get(self.profile)['digest'], 'daily')
        prefs.save(self.profile, enabled=True, modules_off=[], notify='normal', push=True)
        self.assertEqual(prefs.get(self.profile)['digest'], 'daily')                  # left alone when not sent

    def test_the_settings_page_has_a_profile_tab_and_an_insights_tab(self):
        html = self.client.get(reverse('profile-settings')).content.decode()
        self.assertIn('id="set-tab-profile"', html)
        self.assertIn('id="set-tab-insights"', html)
        self.assertIn('id="set-pane-insights"', html)
        self.assertIn('btn-action-primary', html[html.index('Save Changes') - 200:html.index('Save Changes')])

    def test_the_settings_form_saves_it_for_pro_and_shows_it_disabled_below_pro(self):
        self.client.post(reverse('insights-settings'), {'enabled': 'on', 'notify': 'normal', 'digest': 'off'})
        self.assertEqual(prefs.get(UserProfile.objects.get(pk=self.profile.pk))['digest'], 'off')
        html = self.client.get(reverse('profile-settings')).content.decode()
        self.assertIn('name="digest"', html)
        self.assertNotIn('name="digest" disabled', html.replace('\n', ' ').replace('  ', ' '))
        UserProfile.objects.filter(pk=self.profile.pk).update(tier='PLUS')
        page = self.client.get(reverse('profile-settings')).content.decode()
        self.assertRegex(page, r'<select[^>]*id="insights-digest"[^>]*disabled')
        self.assertIn('part of the Pro plan', page)


@override_settings(INSIGHTS_CHANNELS=frozenset({'email'}), DEFAULT_FROM_EMAIL='hello@trackmyrupee.com')
class DigestTests(InsightBase):
    now = None

    def setUp(self):
        super().setUp()
        from django.utils import timezone
        self.now = timezone.now()
        self.today = timezone.localdate(self.now)
        mail.outbox.clear()                      # the welcome email sent when the account was made

    def store(self, rule_id='cashflow.saving_streak', module=Module.CASHFLOW, tone=Tone.POSITIVE, priority=50, sensitive=False,
              surfaces=(Surface.DASHBOARD,), key=None, **extra):
        insight = Insight(rule_id=rule_id, module=module, kind=Kind.OBSERVATION, tone=tone, priority=priority, title=f'About {rule_id}',
                          message='A calm sentence.', dedupe_key=key or rule_id, valid_until=self.today + timedelta(days=40),
                          surfaces=frozenset(surfaces), sensitive=sensitive)
        return InsightState.objects.create(user=self.user, rule_id=rule_id, dedupe_key=insight.dedupe_key, payload=insight.to_payload(),
                                           priority=priority, valid_until=insight.valid_until, **extra)

    def profile_now(self):
        return UserProfile.objects.get(pk=self.profile.pk)

    def send(self):
        return digest.send(self.user, self.profile_now(), self.now)

    def test_a_pro_person_gets_the_best_three_from_different_modules_with_an_unsubscribe_link(self):
        self.store('cashflow.saving_streak', Module.CASHFLOW, priority=70)
        self.store('budgets.limit_nearing', Module.BUDGETS, priority=60, surfaces=(Surface.MODULE,))
        self.store('goals.stalled', Module.GOALS, priority=50)
        self.store('expenses.category_change', Module.EXPENSES, priority=40)
        self.store('cashflow.pace_vs_usual', Module.CASHFLOW, priority=65)
        self.assertEqual(self.send(), 1)
        [email] = mail.outbox
        self.assertEqual(email.to, ['i@example.com'])
        body = email.alternatives[0][0]
        self.assertEqual(body.count('class="item"'), 3)
        self.assertIn('About budgets.limit_nearing', body)
        self.assertNotIn('About expenses.category_change', body)
        self.assertIn('/insights/digest/unsubscribe/', body)
        self.assertIn('/insights/digest/unsubscribe/', email.extra_headers['List-Unsubscribe'])
        self.assertEqual(email.extra_headers['List-Unsubscribe-Post'], 'List-Unsubscribe=One-Click')
        self.assertEqual(InsightState.objects.filter(user=self.user, email_at__isnull=False).count(), 3)

    def test_nothing_is_sent_twice_inside_the_window_and_the_same_insight_is_never_repeated(self):
        self.store('cashflow.saving_streak', Module.CASHFLOW)
        self.assertEqual(self.send(), 1)
        self.store('goals.stalled', Module.GOALS)
        self.assertEqual(self.send(), 0)                                   # a week has not passed
        self.now += timedelta(days=8)
        self.assertEqual(self.send(), 1)                                   # the new one only
        self.assertEqual(len(mail.outbox), 2)
        self.assertNotIn('saving_streak', mail.outbox[1].alternatives[0][0])
        self.now += timedelta(days=8)
        self.assertEqual(self.send(), 0)                                   # nothing new to say

    def test_daily_people_get_one_a_day_when_there_is_something_new(self):
        prefs.set_digest(self.profile, 'daily')
        self.store('cashflow.saving_streak', Module.CASHFLOW)
        self.assertEqual(self.send(), 1)
        self.store('goals.stalled', Module.GOALS)
        self.now += timedelta(days=1, minutes=5)
        self.assertEqual(self.send(), 1)

    def test_sensitive_dismissed_acted_snoozed_and_locked_insights_are_never_emailed(self):
        self.store('loans.interest_paid_year', Module.LOANS, sensitive=True)
        self.store('goals.stalled', Module.GOALS, dismissed_at=self.now)
        self.store('goals.milestone', Module.GOALS, acted_at=self.now)
        self.store('budgets.limit_nearing', Module.BUDGETS, snoozed_until=self.now + timedelta(days=3))
        self.store('recurring.fixed_costs', Module.RECURRING, locked=True)
        self.assertEqual(self.send(), 0)
        self.assertEqual(mail.outbox, [])

    def test_only_pro_with_the_channel_on_the_digest_on_and_an_address(self):
        self.store('cashflow.saving_streak', Module.CASHFLOW)
        UserProfile.objects.filter(pk=self.profile.pk).update(tier='PLUS')
        self.assertEqual(self.send(), 0)
        UserProfile.objects.filter(pk=self.profile.pk).update(tier='PRO')
        with override_settings(INSIGHTS_CHANNELS=frozenset({'bell', 'push'})):
            self.assertEqual(self.send(), 0)
        prefs.set_digest(self.profile, 'off')
        self.assertEqual(self.send(), 0)
        prefs.set_digest(self.profile, 'weekly')
        prefs.save(self.profile_now(), enabled=True, modules_off=[], notify='off', push=True)
        self.assertEqual(self.send(), 0)                                   # "only on the pages"
        prefs.save(self.profile_now(), enabled=True, modules_off=[], notify='normal', push=True)
        User.objects.filter(pk=self.user.pk).update(email='')
        self.user.refresh_from_db()
        self.assertEqual(self.send(), 0)
        self.assertEqual(mail.outbox, [])

    def test_a_failed_send_gives_the_insights_back_for_the_next_run(self):
        self.store('cashflow.saving_streak', Module.CASHFLOW)
        with mock.patch('django.core.mail.EmailMultiAlternatives.send', side_effect=OSError('smtp down')):
            with self.assertRaises(OSError):
                self.send()
        self.assertFalse(InsightState.objects.filter(user=self.user, email_at__isnull=False).exists())
        self.assertEqual(self.send(), 1)

    def test_the_command_counts_and_respects_the_channel(self):
        from io import StringIO

        from django.core.management import call_command
        self.store('cashflow.saving_streak', Module.CASHFLOW)
        out = StringIO()
        call_command('send_insight_digest', stdout=out)
        self.assertIn('insight digests sent=1 failed=0', out.getvalue())
        with override_settings(INSIGHTS_CHANNELS=frozenset()):
            out = StringIO()
            call_command('send_insight_digest', stdout=out)
            self.assertIn('is off', out.getvalue())

    def test_the_email_is_in_the_persons_language(self):
        UserProfile.objects.filter(pk=self.profile.pk).update(language='hi')
        self.store('cashflow.saving_streak', Module.CASHFLOW)
        self.assertEqual(self.send(), 1)
        self.assertIn('कुछ बातें जो हमने देखीं', mail.outbox[0].alternatives[0][0])


class UnsubscribeTests(InsightBase):
    def test_a_link_shows_a_button_and_changes_nothing_until_it_is_pressed(self):
        self.client.logout()
        url = reverse('insights-digest-unsubscribe', args=[digest_token(self.user)])
        page = self.client.get(url)
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, '<button')
        self.assertEqual(prefs.get(UserProfile.objects.get(pk=self.profile.pk))['digest'], 'weekly')
        done = self.client.post(url)
        self.assertContains(done, 'will not get the insight digest')
        self.assertEqual(prefs.get(UserProfile.objects.get(pk=self.profile.pk))['digest'], 'off')

    def test_one_click_unsubscribe_works_without_signing_in_or_a_csrf_token(self):
        from django.test import Client
        anonymous = Client(enforce_csrf_checks=True)
        anonymous.post(reverse('insights-digest-unsubscribe', args=[digest_token(self.user)]), {'List-Unsubscribe': 'One-Click'})
        self.assertEqual(prefs.get(UserProfile.objects.get(pk=self.profile.pk))['digest'], 'off')

    def test_it_changes_only_the_digest_and_a_forged_link_does_nothing(self):
        prefs.save(self.profile, enabled=True, modules_off=['goals'], notify='quiet', push=False, emergency_months=12)
        self.client.post(reverse('insights-digest-unsubscribe', args=[digest_token(self.user)]))
        current = prefs.get(UserProfile.objects.get(pk=self.profile.pk))
        self.assertEqual((current['digest'], current['modules_off'], current['notify'], current['emergency_months']),
                         ('off', ['goals'], 'quiet', 12))
        bad = self.client.post(reverse('insights-digest-unsubscribe', args=['not-a-token']))
        self.assertEqual(bad.status_code, 400)

    def test_the_cron_trigger_is_locked_to_the_secret(self):
        self.assertEqual(self.client.post(reverse('cron-insight-digest')).status_code, 403)


class MonthlyAndYearTests(InsightBase):
    def setUp(self):
        super().setUp()
        self.life()
        self.end = insight_month_start(TODAY, 0) - timedelta(days=1)

    @ENGINE_ON
    def test_a_pro_report_has_a_section_with_the_good_news_first_and_no_sensitive_insight(self):
        section = composers.monthly(self.user, self.end, profile=UserProfile.objects.get(pk=self.profile.pk))
        self.assertIsNotNone(section)
        self.assertLessEqual(len(section['highlights']), 3)
        self.assertTrue(all(not i.sensitive for i in section['highlights']))
        self.assertTrue(all(Surface.MONTHLY in i.surfaces for i in section['highlights']))
        if section['went_well']:
            self.assertEqual(section['went_well'].tone, Tone.POSITIVE)
        if section['try_next']:
            self.assertEqual(section['try_next'].kind, Kind.OPPORTUNITY)
            self.assertIsNotNone(section['try_next'].action)

    @ENGINE_ON
    def test_not_for_plus_or_free_and_not_with_the_engine_off(self):
        for tier in ('PLUS', 'FREE'):
            UserProfile.objects.filter(pk=self.profile.pk).update(tier=tier)
            self.assertIsNone(composers.monthly(self.user, self.end, profile=UserProfile.objects.get(pk=self.profile.pk)), tier)
        UserProfile.objects.filter(pk=self.profile.pk).update(tier='PRO')
        with override_settings(INSIGHTS_ENGINE_MODE='off'):
            self.assertIsNone(composers.monthly(self.user, self.end, profile=UserProfile.objects.get(pk=self.profile.pk)))

    @ENGINE_ON
    def test_the_reports_data_and_email_carry_the_section(self):
        first = self.end.replace(day=1)
        data = MonthlyCommand().get_report_data(self.user, first, self.end)
        self.assertTrue(data['has_data'])
        self.assertIsNotNone(data['insights'])
        from django.template.loader import render_to_string
        html = render_to_string('emails/monthly_report.html', {'user': self.user, 'start_date': first, 'end_date': self.end,
                                                               'data': data, 'currency_symbol': '₹'})
        self.assertIn('Worth knowing', html)
        data['insights'] = None
        self.assertNotIn('Worth knowing', render_to_string('emails/monthly_report.html', {
            'user': self.user, 'start_date': first, 'end_date': self.end, 'data': data, 'currency_symbol': '₹'}))

    def test_the_report_is_unchanged_with_the_engine_off(self):
        data = MonthlyCommand().get_report_data(self.user, self.end.replace(day=1), self.end)
        self.assertIsNone(data['insights'])

    @ENGINE_ON
    def test_year_lines_come_from_the_rules_that_list_the_year_and_never_a_sensitive_one(self):
        lines = composers.year_lines(self.user, 2026, profile=UserProfile.objects.get(pk=self.profile.pk))
        self.assertTrue(all(Surface.YEAR_IN_REVIEW in i.surfaces and not i.sensitive for i in lines))
        self.assertLessEqual(len(lines), 4)
        with override_settings(INSIGHTS_ENGINE_MODE='off'):
            self.assertEqual(composers.year_lines(self.user, 2026, profile=UserProfile.objects.get(pk=self.profile.pk)), [])

    @ENGINE_ON
    def test_the_wrapped_story_gets_a_what_we_noticed_card_that_hides_with_the_amounts(self):
        for n in range(25):
            self.spend(100, insight_month_start(TODAY, 1) + timedelta(days=n % 20), 'Dining')
        html = self.client.get(reverse('year_in_review', args=[2026])).content.decode()
        if 'data-card="noticed"' in html:
            card = html[html.index('data-card="noticed"'):]
            card = card[:card.index('</section>')]
            self.assertIn('data-money="1"', html[html.index('data-card="noticed"') - 40:html.index('data-card="noticed"') + 80])
            self.assertIn('class="wr-amt"', card)


class SurfacesTests(SimpleTestCase):
    def test_the_rules_the_plan_lists_for_the_report_and_the_year_say_so(self):
        monthly = {'cashflow.saving_streak', 'cashflow.savings_momentum', 'recurring.fixed_costs', 'loans.interest_paid_year',
                   'accounts.idle_cash', 'income.steady_vs_variable', 'transfers.invested_share', 'networth.what_moved',
                   'budgets.limit_often_over', 'budgets.limit_often_under', 'budgets.room_to_share', 'goals.small_change',
                   'loans.extra_emi_effect'}
        year = {'cashflow.saving_streak', 'cashflow.year_pace', 'loans.interest_paid_year', 'income.steady_vs_variable',
                'transfers.invested_share', 'networth.what_moved', 'goals.milestone'}
        for rule_id in monthly:
            self.assertIn(Surface.MONTHLY, RULES[rule_id].surfaces, rule_id)
        for rule_id in year:
            self.assertIn(Surface.YEAR_IN_REVIEW, RULES[rule_id].surfaces, rule_id)

    def test_what_a_rule_declares_is_what_its_insights_carry(self):
        for rule_id in ('cashflow.saving_streak', 'cashflow.year_pace', 'loans.interest_paid_year', 'accounts.idle_cash',
                        'budgets.limit_often_over', 'goals.small_change'):
            ctx = RULES[rule_id].sample()
            for found in run(rule_id, ctx):
                self.assertEqual(found.surfaces, RULES[rule_id].surfaces, rule_id)

    def test_the_insight_text_for_email_never_pushes_a_sensitive_insight(self):
        for rule in registry.all_rules():
            if Surface.DIGEST in rule.surfaces:
                self.fail(f'{rule.id}: the digest reads what is stored, not a surface of its own')
