"""Fixes from the review of the engine: each test fails on the code it replaced."""
from datetime import timedelta
from io import StringIO

from django.core.cache import cache
from django.core.management import call_command
from django.db import connection
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from expenses.insights import engine, prefs, sampling, touch, version
from expenses.insights.adapters import bell, digest
from expenses.insights.types import Insight, Kind, Module, Surface, Tone
from expenses.models import InsightState, UserProfile

from .base import ENGINE_ON, TODAY, InsightBase, temp_rule


class RefreshSafetyTests(InsightBase):
    def test_a_run_does_not_undo_a_change_made_while_it_was_going(self):
        stale = UserProfile.objects.get(pk=self.profile.pk)                  # loaded before the unsubscribe
        prefs.set_digest(UserProfile.objects.get(pk=self.profile.pk), 'off')
        engine.refresh(self.user, today=TODAY, profile=stale)
        self.assertEqual(prefs.get(UserProfile.objects.get(pk=self.profile.pk))['digest'], 'off')

    def test_two_runs_for_one_person_do_not_overlap(self):
        cache.add(f'insights_refresh_{self.user.pk}', 1, 120)                # another run is going
        self.assertFalse(engine.refresh(self.user, today=TODAY).ran)
        cache.delete(f'insights_refresh_{self.user.pk}')
        self.assertTrue(engine.refresh(self.user, today=TODAY).ran)
        self.assertIsNone(cache.get(f'insights_refresh_{self.user.pk}'))     # and the lock is let go afterwards

    def test_a_rule_that_says_the_same_thing_twice_is_stored_once(self):
        @temp_rule(self, id='cashflow.twice', module=Module.CASHFLOW, surfaces={Surface.DASHBOARD}, sample=sampling.default_sample)
        def twice(ctx):
            one = Insight(rule_id='cashflow.twice', module=Module.CASHFLOW, kind=Kind.OBSERVATION, tone=Tone.CALM, priority=10,
                          title='Twice', message='A calm sentence.', dedupe_key='k', valid_until=ctx.cycle[1],
                          surfaces=frozenset({Surface.DASHBOARD}))
            return [one, one]
        self.life()
        engine.refresh(self.user, today=TODAY)
        self.assertEqual(InsightState.objects.filter(user=self.user, rule_id='cashflow.twice').count(), 1)

    def test_old_insights_retire_when_the_person_no_longer_has_the_thing(self):
        row = InsightState.objects.create(user=self.user, rule_id='loans.interest_paid_year', dedupe_key='old',
                                          payload={}, priority=30, valid_until=TODAY + timedelta(days=30))
        engine.refresh(self.user, today=TODAY)                                # there is no loan any more
        self.assertLess(InsightState.objects.get(pk=row.pk).valid_until, TODAY)

    def test_a_failure_in_the_run_does_not_make_every_page_view_retry_it(self):
        from unittest import mock
        before = UserProfile.objects.get(pk=self.profile.pk).insights_generated_at
        with mock.patch('expenses.insights.engine.InsightContext', side_effect=RuntimeError('boom')):
            with self.assertRaises(RuntimeError):
                engine.refresh(self.user, today=TODAY)
        after = UserProfile.objects.get(pk=self.profile.pk).insights_generated_at
        self.assertIsNotNone(after)
        self.assertNotEqual(before, after)


class FreeWeeklyObservationTests(InsightBase):
    tier = 'FREE'
    other_pool_rule = None

    def setUp(self):
        super().setUp()
        from expenses.insights import registry
        self.other_pool_rule = next(r.id for r in registry.all_rules() if r.pool == 'weekly' and r.id != 'expenses.busiest_weekday')

    def row(self, rule_id, **kw):
        insight = Insight(rule_id=rule_id, module=Module.EXPENSES, kind=Kind.PATTERN, tone=Tone.CALM, priority=30, title=rule_id,
                          message='A calm sentence.', dedupe_key=rule_id, valid_until=TODAY + timedelta(days=20),
                          surfaces=frozenset({Surface.DASHBOARD, Surface.MODULE}))
        return InsightState.objects.create(user=self.user, rule_id=rule_id, dedupe_key=rule_id, payload=insight.to_payload(),
                                           priority=30, valid_until=insight.valid_until, **kw)

    def test_another_page_cannot_bring_a_second_observation_into_the_same_week(self):
        now = timezone.now()
        # one weekly-pool insight was shown two days ago (on the dashboard); another is waiting, never shown
        self.row('expenses.busiest_weekday', first_shown=now - timedelta(days=2), last_shown=now - timedelta(days=2))
        self.row(self.other_pool_rule)
        # a page that skips the first one (it shows it itself) must not let the second one in this week
        found = engine.candidates(self.user, Surface.MODULE, profile=self.user.profile, now=now,
                                  skip_rules={'expenses.busiest_weekday'})
        self.assertEqual([r.rule_id for _i, r in found if r.rule_id == self.other_pool_rule], [])
        # a week later the place is free again
        later = now + timedelta(days=8)
        found = engine.candidates(self.user, Surface.MODULE, profile=self.user.profile, now=later,
                                  skip_rules={'expenses.busiest_weekday'})
        self.assertEqual([r.rule_id for _i, r in found if r.rule_id == self.other_pool_rule], [self.other_pool_rule])

class BellAndDigestTests(InsightBase):
    def test_a_run_a_minute_earlier_than_yesterdays_still_delivers_the_daily_insight(self):
        now = timezone.now()
        InsightState.objects.create(user=self.user, rule_id='budgets.x', dedupe_key='x', payload={}, priority=1,
                                    valid_until=TODAY, bell_at=now - timedelta(days=1) + timedelta(minutes=1))
        # yesterday's claim was one minute inside the 24-hour window: it must not block today
        self.assertFalse(InsightState.objects.filter(
            user=self.user, bell_at__gte=now - bell.BELL_WINDOW['normal'] + timedelta(hours=2)).exists())

    def test_the_plain_text_of_an_email_has_no_styles_or_entities(self):
        html = '<html><head><title>T</title><style>.a { color: red }</style></head><body><p>Hi &amp; bye &rarr; now</p></body></html>'
        text = digest.plain_text(html)
        self.assertNotIn('color', text)
        self.assertNotIn('&amp;', text)
        self.assertIn('Hi & bye', text)

    @override_settings(INSIGHTS_CHANNELS=frozenset({'email'}))
    def test_the_digest_goes_only_to_a_verified_address(self):
        from allauth.account.models import EmailAddress

        insight = Insight(rule_id='cashflow.saving_streak', module=Module.CASHFLOW, kind=Kind.PROGRESS, tone=Tone.POSITIVE, priority=50,
                          title='A run', message='A calm sentence.', dedupe_key='k', valid_until=TODAY + timedelta(days=30),
                          surfaces=frozenset({Surface.DASHBOARD}))
        InsightState.objects.create(user=self.user, rule_id='cashflow.saving_streak', dedupe_key='k', payload=insight.to_payload(),
                                    priority=50, valid_until=insight.valid_until)
        out = StringIO()
        call_command('send_insight_digest', stdout=out)
        self.assertIn('sent=0', out.getvalue())                           # no verified address
        EmailAddress.objects.create(user=self.user, email=self.user.email, verified=False, primary=True)
        out = StringIO()
        call_command('send_insight_digest', stdout=out)
        self.assertIn('sent=0', out.getvalue())                           # an unverified one
        EmailAddress.objects.filter(user=self.user).update(verified=True)
        out = StringIO()
        call_command('send_insight_digest', stdout=out)
        self.assertIn('sent=1', out.getvalue())


class StripCacheTests(InsightBase):
    @ENGINE_ON
    @override_settings(INSIGHTS_STRIP_CACHE_SECONDS=60)
    def test_a_strip_is_reused_until_something_changes(self):
        insight = Insight(rule_id='budgets.category_runout', module=Module.BUDGETS, kind=Kind.OBSERVATION, tone=Tone.CALM, priority=50,
                          title='Dining is close', message='A calm sentence.', dedupe_key='k',
                          valid_until=timezone.localdate() + timedelta(days=10), surfaces=frozenset({Surface.MODULE}))
        row = InsightState.objects.create(user=self.user, rule_id='budgets.category_runout', dedupe_key='k',
                                          payload=insight.to_payload(), priority=50, valid_until=insight.valid_until)
        UserProfile.objects.filter(pk=self.profile.pk).update(insights_generated_at=timezone.now(), insights_stale=False)
        url = reverse('insights-panel') + '?surface=module&modules=budgets&strip=1'
        first = self.client.get(url).content.decode()
        self.assertIn('Dining is close', first)
        with CaptureQueriesContext(connection) as queries:
            again = self.client.get(url).content.decode()
        self.assertFalse([q for q in queries if 'insightstate' in q['sql']])      # the copy came from the cache
        self.assertEqual(first, again)
        engine.dismiss(self.user, row.uuid)         # a dismissal retires the copy at once
        self.assertEqual(self.client.get(url).content.decode(), '')

    def test_touch_changes_the_version(self):
        before = version(self.user.pk)
        touch(self.user.pk)
        self.assertEqual(version(self.user.pk), before + 1)


class SwitchedOffCostsNothingTests(InsightBase):
    def test_a_save_does_not_touch_the_cache_while_the_engine_is_off(self):
        cache.delete(f'insights_dirty_{self.user.pk}')
        self.spend(10, TODAY)
        self.assertIsNone(cache.get(f'insights_dirty_{self.user.pk}'))

    @ENGINE_ON
    def test_but_it_does_once_the_engine_is_on(self):
        cache.delete(f'insights_dirty_{self.user.pk}')
        self.spend(10, TODAY)
        self.assertTrue(cache.get(f'insights_dirty_{self.user.pk}'))

    def test_the_settings_tab_is_not_offered_while_the_engine_is_off(self):
        html = self.client.get(reverse('profile-settings')).content.decode()
        self.assertNotIn('set-tab-insights', html)


# ---------------------------------------------------------------------------------------------- the rules
from datetime import date  # noqa: E402
from types import SimpleNamespace  # noqa: E402

from django.test import SimpleTestCase  # noqa: E402

from expenses.analytics.facts import load_income_rows  # noqa: E402
from expenses.insights import registry  # noqa: E402
from expenses.insights.context import InsightContext  # noqa: E402
from expenses.insights.format import Fmt  # noqa: E402
from expenses.insights.rules.expenses import duplicate_groups  # noqa: E402
from expenses.models import Expense, RecurringTransaction  # noqa: E402

from .base import D  # noqa: E402

RULES = {r.id: r for r in registry.all_rules()}


def run(rule_id, ctx):
    with ctx.declared(RULES[rule_id].needs):
        return RULES[rule_id].evaluate(ctx)


class EarlyInTheCycleTests(SimpleTestCase):
    """A pace needs a week of days behind it; "kept so far" needs half the cycle."""

    def ctx_on(self, day, **kw):
        ctx = sampling.scenario(**kw)
        ctx.today = date(2026, 10, day)
        return ctx

    def test_the_pace_rules_wait_a_week(self):
        for day, expected in ((3, 0), (22, 1)):
            self.assertEqual(len(run('cashflow.pace_vs_usual', self.ctx_on(day))), expected, day)
        rows = [('Transport', 5000, 4000)]
        self.assertEqual(run('budgets.category_runout', self.ctx_on(3, budgets=rows)), [])
        self.assertEqual(len(run('budgets.category_runout', self.ctx_on(22, budgets=rows))), 1)

    def test_what_has_been_kept_so_far_waits_for_half_the_cycle(self):
        self.assertEqual(run('cashflow.savings_momentum', self.ctx_on(4)), [])
        self.assertEqual(len(run('cashflow.savings_momentum', self.ctx_on(22))), 1)

    def test_the_category_above_usual_waits_a_week(self):
        self.assertEqual(run('expenses.category_above_usual', self.ctx_on(3)), [])

    def test_the_date_a_limit_runs_out_is_the_day_it_is_reached(self):
        [insight] = run('budgets.category_runout', self.ctx_on(22, budgets=[('Transport', 5000, 4000)]))
        self.assertIn('27 Oct', insight.message)         # 22 days gone, 4,000 of 5,000: day 27.5, so the 27th


class DuplicateEntryTests(SimpleTestCase):
    def row(self, day, hour, **kw):
        from datetime import datetime
        base = dict(account_id=1, amount=D(40), description='Metro', from_import=False, date=date(2026, 10, day),
                    created_at=datetime(2026, 10, 22, hour, 0))
        base.update(kw)
        return SimpleNamespace(**base)

    def test_a_week_caught_up_in_one_sitting_is_not_a_duplicate(self):
        self.assertEqual(duplicate_groups([self.row(15, 10), self.row(16, 10), self.row(17, 11)]), [])

    def test_the_same_entry_typed_twice_on_one_day_is_one(self):
        [group] = duplicate_groups([self.row(15, 10), self.row(15, 11)])
        self.assertEqual(len(group), 2)


class MoneyTextTests(SimpleTestCase):
    def test_a_rounded_amount_moves_up_a_unit_instead_of_reading_100k(self):
        fmt = Fmt('₹')
        self.assertEqual([fmt.approx(v) for v in (99950, 9999500, 99999999)], ['₹1L', '₹1Cr', '₹10Cr'])
        self.assertEqual(Fmt('$').approx(999950), '$1M')


class LoaderTests(InsightBase):
    def test_cashback_and_refunds_are_not_pay_arriving(self):
        self.income(50000, TODAY - timedelta(days=3), 'Salary')
        self.income(5, TODAY - timedelta(days=1), 'Cashback & Rewards')
        self.income(300, TODAY - timedelta(days=1), 'Refund / Reimbursement')
        rows = load_income_rows(self.user, TODAY - timedelta(days=10), TODAY)
        self.assertEqual([r.source_type for r in rows], ['Salary'])

    def test_a_foreign_schedule_is_compared_in_its_own_currency(self):
        RecurringTransaction.objects.create(user=self.user, transaction_type='EXPENSE', amount=D('20'), currency='USD',
                                            description='Hosting', category='Tech', frequency='MONTHLY',
                                            start_date=TODAY - timedelta(days=90), account=self.cash, is_active=True)
        for days, base in ((35, '1660'), (4, '1780')):               # the same 20 dollars, at two days' rates
            Expense.objects.create(user=self.user, date=TODAY - timedelta(days=days), amount=D('20'), base_amount=D(base),
                                   currency='USD', category='Tech', description='Hosting (Recurring)', account=self.cash,
                                   payment_method='Cash')
        ctx = InsightContext(self.user, today=TODAY, profile=self.profile)
        self.assertEqual(run('recurring.price_change', ctx), [])


class BackgroundRefreshTests(InsightBase):
    @ENGINE_ON
    @override_settings(INSIGHTS_REFRESH_IN_BACKGROUND=True)
    def test_a_person_with_stored_insights_gets_them_without_waiting_for_the_refresh(self):
        from unittest import mock
        self.life()
        engine.refresh(self.user, today=TODAY)                                   # stored, so there is something to show
        UserProfile.objects.filter(pk=self.profile.pk).update(insights_stale=True,
                                                              insights_generated_at=timezone.now() - timedelta(hours=3))
        with mock.patch('expenses.insights.engine.refresh_in_background', return_value=True) as later, \
                mock.patch('expenses.insights.engine.refresh') as inline:
            response = self.client.get(reverse('insights-panel') + '?surface=dashboard')
        self.assertEqual(response.status_code, 200)
        later.assert_called_once_with(self.user.pk)
        inline.assert_not_called()

    @ENGINE_ON
    @override_settings(INSIGHTS_REFRESH_IN_BACKGROUND=True)
    def test_the_very_first_visit_gets_a_placeholder_that_asks_again(self):
        from unittest import mock
        with mock.patch('expenses.insights.engine.refresh_in_background', return_value=True) as later, \
                mock.patch('expenses.insights.engine.refresh') as inline:
            card = self.client.get(reverse('insights-panel') + '?surface=dashboard').content.decode()
            strip = self.client.get(reverse('insights-panel') + '?surface=module&modules=budgets&strip=1').content.decode()
        inline.assert_not_called()
        self.assertEqual(later.call_count, 2)
        self.assertIn('hx-trigger="load delay:2500ms"', card)
        self.assertIn('hx-trigger="load delay:2500ms"', strip)

    @ENGINE_ON
    @override_settings(INSIGHTS_REFRESH_IN_BACKGROUND=True)
    def test_when_the_background_is_busy_the_first_visit_refreshes_inline(self):
        from unittest import mock
        with mock.patch('expenses.insights.engine.refresh_in_background', return_value=False), \
                mock.patch('expenses.insights.engine.refresh') as inline:
            html = self.client.get(reverse('insights-panel') + '?surface=dashboard').content.decode()
        inline.assert_called_once()
        self.assertNotIn('hx-trigger="load delay', html)

    def test_the_background_run_refreshes_and_lets_its_slot_go(self):
        self.life()
        before = UserProfile.objects.get(pk=self.profile.pk).insights_generated_at
        with mock_connection_close():
            engine._run_background(self.user.pk, release=False)
        self.assertNotEqual(UserProfile.objects.get(pk=self.profile.pk).insights_generated_at, before)

    def test_only_a_few_background_runs_happen_at_once(self):
        taken = [engine._background_slots.acquire(blocking=False) for _ in range(4)]
        try:
            self.assertTrue(all(taken))
            self.assertFalse(engine.refresh_in_background(self.user.pk))         # busy: nothing starts, nothing is lost
        finally:
            for ok in taken:
                if ok:
                    engine._background_slots.release()


def mock_connection_close():
    """The background run closes its database connection when done; under test that is the shared one."""
    from unittest import mock
    return mock.patch('django.db.connection.close')
