"""The engine: storing, ranking, caps, cooldowns, dismissal, snooze, plan gating, failure isolation, query counts."""
import logging
from datetime import datetime, timedelta

from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from expenses.insights import engine, prefs, sampling
from expenses.insights.types import Insight, Kind, Module, Plan, Surface, Tone
from expenses.models import Account, InsightFeedback, InsightState, UserProfile

from .base import TODAY, D, InsightBase, month_start, temp_rule

NOW = timezone.make_aware(datetime(2026, 10, 22, 9, 0))


def insight(rule_id='cashflow.test', module=Module.CASHFLOW, priority=50, key='2026-10', surfaces=None, sensitive=False,
            kind=Kind.OBSERVATION, message='One calm sentence.', **kw):
    return Insight(rule_id=rule_id, module=module, kind=kind, tone=Tone.CALM, priority=priority, title='A calm title',
                   message=message, dedupe_key=key, valid_until=TODAY + timedelta(days=9),
                   surfaces=frozenset(surfaces or {Surface.DASHBOARD}), sensitive=sensitive, **kw)


class StoredBase(InsightBase):
    def store(self, rule_id, ins=None, **row):
        """A stored insight, as refresh would write it, for the read path."""
        ins = ins or insight(rule_id=rule_id)
        defaults = dict(user=self.user, rule_id=rule_id, dedupe_key=ins.dedupe_key, payload=ins.to_payload(),
                        priority=ins.priority, valid_until=ins.valid_until, generated_at=NOW)
        defaults.update(row)
        return InsightState.objects.create(**defaults)

    def generate(self, surface=Surface.DASHBOARD, **kw):
        profile = UserProfile.objects.get(pk=self.profile.pk)
        return engine.generate(self.user, surface, profile=profile, now=kw.pop('now', NOW), **kw)


class RefreshTests(StoredBase):
    def test_refresh_stores_what_the_rules_say_and_is_safe_to_repeat(self):
        self.life()
        first = engine.refresh(self.user, today=TODAY)
        rows = {r.rule_id: r for r in InsightState.objects.filter(user=self.user)}
        self.assertEqual(set(rows), {'cashflow.pace_vs_usual', 'cashflow.saving_streak', 'cashflow.savings_momentum',
                                     'cashflow.year_pace', 'expenses.category_above_usual', 'expenses.category_change',
                                     'expenses.spending_timing', 'accounts.idle_cash', 'recurring.looks_recurring'})
        self.assertEqual(first.failed, [])
        uuids = {r.rule_id: r.uuid for r in rows.values()}
        engine.refresh(self.user, today=TODAY)
        again = {r.rule_id: r.uuid for r in InsightState.objects.filter(user=self.user)}
        self.assertEqual(again, uuids)                       # updated in place: no duplicates, the same rows
        self.assertEqual(InsightState.objects.filter(user=self.user).count(), 10)   # one row per rule here (travel is nothing so far, so no "lower"); looks_recurring speaks twice

    def test_refresh_never_undoes_a_dismissal_or_a_snooze(self):
        self.life()
        engine.refresh(self.user, today=TODAY)
        row = InsightState.objects.get(user=self.user, rule_id='cashflow.pace_vs_usual')
        engine.dismiss(self.user, row.uuid)
        engine.refresh(self.user, today=TODAY)
        self.assertNotIn('cashflow.pace_vs_usual', [p.insight.rule_id for p in self.generate(now=timezone.now())])
        self.assertIsNotNone(InsightState.objects.get(pk=row.pk).dismissed_at)

    def test_an_insight_that_stops_being_true_is_retired(self):
        self.life()
        engine.refresh(self.user, today=TODAY)
        self.spend(0, month_start(TODAY, 0))               # nothing changes...
        from expenses.models import Expense
        Expense.objects.filter(user=self.user, date=month_start(TODAY, 0), category='Dining').update(
            amount=D('8000'), base_amount=D('8000'))        # ...except dining comes back down
        engine.refresh(self.user, today=TODAY)
        row = InsightState.objects.get(user=self.user, rule_id='expenses.category_above_usual')
        self.assertLess(row.valid_until, TODAY)
        self.assertNotIn('expenses.category_above_usual', [p.insight.rule_id for p in self.generate(now=NOW)])

    def test_a_new_user_gets_the_still_learning_state_not_noise(self):
        self.income(60000, month_start(TODAY, 0))
        result = engine.refresh(self.user, today=TODAY)
        self.assertGreater(result.skipped_for_history, 0)
        self.assertEqual(InsightState.objects.filter(user=self.user).count(), 0)
        self.assertTrue(engine.is_learning(UserProfile.objects.get(pk=self.profile.pk)))

    def test_switching_everything_off_stores_and_shows_nothing(self):
        self.life()
        prefs.save(self.profile, enabled=False, modules_off=[], notify='normal', push=True)
        engine.refresh(self.user, today=TODAY)
        self.assertEqual(InsightState.objects.filter(user=self.user).count(), 0)
        self.store('cashflow.test')
        self.assertEqual(self.generate(), [])

    def test_a_module_switched_off_is_skipped(self):
        self.life()
        prefs.save(self.profile, enabled=True, modules_off=['cashflow'], notify='normal', push=True)
        engine.refresh(self.user, today=TODAY)
        self.assertEqual(set(InsightState.objects.filter(user=self.user).values_list('rule_id', flat=True)),
                         {'expenses.category_above_usual', 'expenses.category_change', 'expenses.spending_timing',
                          'recurring.looks_recurring', 'accounts.idle_cash'})

    def test_old_rows_are_pruned(self):
        self.store('cashflow.test', valid_until=TODAY - timedelta(days=120))
        engine.refresh(self.user, today=TODAY)
        self.assertEqual(InsightState.objects.filter(user=self.user).count(), 0)

    def test_text_is_stored_in_the_persons_language(self):
        self.life()
        UserProfile.objects.filter(pk=self.profile.pk).update(language='hi')
        engine.refresh(self.user, today=TODAY, profile=UserProfile.objects.get(pk=self.profile.pk))
        row = InsightState.objects.get(user=self.user, rule_id='cashflow.pace_vs_usual')
        self.assertEqual(row.lang, 'hi')
        self.assertRegex(row.payload['message'], '[०-९]')

    def test_the_profile_is_marked_fresh(self):
        self.life()
        self.assertTrue(UserProfile.objects.get(pk=self.profile.pk).insights_stale)
        engine.refresh(self.user, today=TODAY)
        fresh = UserProfile.objects.get(pk=self.profile.pk)
        self.assertFalse(fresh.insights_stale)
        self.assertFalse(engine.needs_refresh(fresh))
        UserProfile.objects.filter(pk=fresh.pk).update(insights_stale=True)
        self.assertFalse(engine.needs_refresh(UserProfile.objects.get(pk=fresh.pk)))     # ran a moment ago
        self.assertTrue(engine.needs_refresh(UserProfile.objects.get(pk=fresh.pk), now=timezone.now() + timedelta(minutes=11)))

    def test_a_refresh_is_also_due_when_the_day_turns_or_an_hour_passes_even_with_nothing_changed(self):
        self.life()
        engine.refresh(self.user, today=TODAY)
        fresh = UserProfile.objects.get(pk=self.profile.pk)
        self.assertFalse(engine.needs_refresh(fresh, now=fresh.insights_generated_at + timedelta(minutes=30)))
        self.assertTrue(engine.needs_refresh(fresh, now=fresh.insights_generated_at + timedelta(hours=1, minutes=1)))
        midnight = timezone.localtime(fresh.insights_generated_at).replace(hour=23, minute=59) + timedelta(minutes=12)
        self.assertTrue(engine.needs_refresh(fresh, now=midnight))             # a new day: the pace has moved

    def test_a_change_to_the_persons_money_makes_the_next_visit_refresh(self):
        engine.refresh(self.user, today=TODAY)
        fresh = UserProfile.objects.get(pk=self.profile.pk)
        later = fresh.insights_generated_at + timedelta(minutes=11)
        self.assertFalse(engine.needs_refresh(fresh, now=later))
        self.spend(100, TODAY)
        self.assertTrue(engine.needs_refresh(UserProfile.objects.get(pk=self.profile.pk), now=later))
        engine.refresh(self.user, today=TODAY)                                   # and a refresh clears it again
        again = UserProfile.objects.get(pk=self.profile.pk)
        self.assertFalse(engine.needs_refresh(again, now=again.insights_generated_at + timedelta(minutes=11)))

    def test_so_does_a_change_to_a_budget_limit_and_saving_costs_no_extra_query(self):
        engine.refresh(self.user, today=TODAY)
        fresh = UserProfile.objects.get(pk=self.profile.pk)
        self.category('Food', 5000)
        self.assertTrue(engine.needs_refresh(fresh, now=fresh.insights_generated_at + timedelta(minutes=11)))
        with CaptureQueriesContext(connection) as queries:
            self.spend(100, TODAY)
        self.assertFalse([q for q in queries.captured_queries if 'insights' in q['sql']])


class FailureIsolationTests(StoredBase):
    def test_a_rule_that_raises_is_skipped_and_never_leaks_what_it_saw(self):
        @temp_rule(self, id='cashflow.boom', module=Module.CASHFLOW, surfaces={Surface.DASHBOARD},
                   sample=sampling.default_sample)
        def boom(ctx):
            raise KeyError('Dining ₹123456')
        self.life()
        with self.assertLogs('expenses.insights.engine', level=logging.WARNING) as logs:
            result = engine.refresh(self.user, today=TODAY)
        self.assertEqual(result.failed, ['cashflow.boom'])
        self.assertTrue(InsightState.objects.filter(user=self.user, rule_id='cashflow.pace_vs_usual').exists())
        text = '\n'.join(logs.output)
        self.assertIn('cashflow.boom', text)
        self.assertIn('KeyError', text)
        for secret in ('Dining', '123456', '₹'):
            self.assertNotIn(secret, text)
        self.assertTrue(all(record.exc_info is None for record in logs.records))


class ReadPathTests(StoredBase):
    def test_ranked_by_priority_and_capped_at_three_on_the_dashboard(self):
        for i, module in enumerate([Module.CASHFLOW, Module.BUDGETS, Module.EXPENSES, Module.RECURRING, Module.ACCOUNTS]):
            rule_id = f'{module.value}.rule{i}'
            self.store(rule_id, insight(rule_id=rule_id, module=module, priority=10 + i * 10))
        # the stored rule ids need registered rules, so register matching throwaways
        for i, module in enumerate([Module.CASHFLOW, Module.BUDGETS, Module.EXPENSES, Module.RECURRING, Module.ACCOUNTS]):
            temp_rule(self, id=f'{module.value}.rule{i}', module=module, surfaces={Surface.DASHBOARD},
                      sample=sampling.default_sample)(lambda ctx: [])
        shown = self.generate()
        self.assertEqual([p.insight.priority for p in shown], [50, 40, 30])

    def test_no_more_than_two_from_one_module_on_the_dashboard(self):
        for i in range(4):
            rule_id = f'budgets.rule{i}'
            temp_rule(self, id=rule_id, module=Module.BUDGETS, surfaces={Surface.DASHBOARD},
                      sample=sampling.default_sample)(lambda ctx: [])
            self.store(rule_id, insight(rule_id=rule_id, module=Module.BUDGETS, priority=80 - i))
        temp_rule(self, id='expenses.other', module=Module.EXPENSES, surfaces={Surface.DASHBOARD},
                  sample=sampling.default_sample)(lambda ctx: [])
        self.store('expenses.other', insight(rule_id='expenses.other', module=Module.EXPENSES, priority=10))
        self.assertEqual([p.insight.rule_id for p in self.generate()], ['budgets.rule0', 'budgets.rule1', 'expenses.other'])

    def test_a_module_page_shows_two_of_its_own(self):
        for i, module in enumerate([Module.BUDGETS, Module.BUDGETS, Module.BUDGETS, Module.EXPENSES]):
            rule_id = f'{module.value}.m{i}'
            temp_rule(self, id=rule_id, module=module, surfaces={Surface.MODULE}, sample=sampling.default_sample)(lambda ctx: [])
            self.store(rule_id, insight(rule_id=rule_id, module=module, priority=50 + i, surfaces={Surface.MODULE}))
        shown = self.generate(Surface.MODULE, modules={Module.BUDGETS})
        self.assertEqual(len(shown), 2)
        self.assertTrue(all(p.insight.module == Module.BUDGETS for p in shown))

    def test_the_same_insight_is_never_shown_twice_in_one_view(self):
        self.life()
        engine.refresh(self.user, today=TODAY)
        first = self.generate(now=NOW)
        seen = {(p.insight.rule_id, p.insight.dedupe_key) for p in first}
        again = self.generate(now=NOW, exclude=seen)
        self.assertFalse(seen & {(p.insight.rule_id, p.insight.dedupe_key) for p in again})

    def test_a_dismissed_insight_is_gone_and_returns_with_its_next_period(self):
        temp_rule(self, id='cashflow.cycle', module=Module.CASHFLOW, surfaces={Surface.DASHBOARD}, cooldown_days=0,
                  sample=sampling.default_sample)(lambda ctx: [])
        row = self.store('cashflow.cycle', insight(rule_id='cashflow.cycle', key='2026-10'))
        engine.dismiss(self.user, row.uuid)
        self.assertEqual(self.generate(now=timezone.now()), [])
        self.store('cashflow.cycle', insight(rule_id='cashflow.cycle', key='2026-11'))
        self.assertEqual([p.insight.dedupe_key for p in self.generate(now=timezone.now())], ['2026-11'])

    def test_snoozed_means_gone_until_the_date(self):
        temp_rule(self, id='cashflow.snz', module=Module.CASHFLOW, surfaces={Surface.DASHBOARD},
                  sample=sampling.default_sample)(lambda ctx: [])
        row = self.store('cashflow.snz', insight(rule_id='cashflow.snz'))
        self.assertTrue(engine.snooze(self.user, row.uuid, days=7))
        stored = InsightState.objects.get(pk=row.pk).snoozed_until
        self.assertEqual(self.generate(now=stored - timedelta(minutes=1)), [])
        self.assertEqual(len(self.generate(now=stored + timedelta(minutes=1))), 1)

    def test_acting_on_it_resolves_it(self):
        temp_rule(self, id='cashflow.act', module=Module.CASHFLOW, surfaces={Surface.DASHBOARD},
                  sample=sampling.default_sample)(lambda ctx: [])
        row = self.store('cashflow.act', insight(rule_id='cashflow.act'))
        self.assertEqual(engine.acted(self.user, row.uuid), 'cashflow.act')
        self.assertEqual(self.generate(), [])

    def test_cooldown_rests_a_rule_that_was_just_dismissed_but_not_what_was_already_there(self):
        temp_rule(self, id='cashflow.cool', module=Module.CASHFLOW, surfaces={Surface.DASHBOARD}, cooldown_days=10,
                  sample=sampling.default_sample)(lambda ctx: [])
        earlier = self.store('cashflow.cool', insight(rule_id='cashflow.cool', key='a'))
        self.store('cashflow.cool', insight(rule_id='cashflow.cool', key='b'))
        engine.dismiss(self.user, earlier.uuid)
        self.assertEqual([p.insight.dedupe_key for p in self.generate(now=timezone.now())], ['b'])
        self.store('cashflow.cool', insight(rule_id='cashflow.cool', key='c'))   # created after the dismissal
        self.assertEqual([p.insight.dedupe_key for p in self.generate(now=timezone.now())], ['b'])
        InsightState.objects.filter(pk=earlier.pk).update(dismissed_at=timezone.now() - timedelta(days=11))
        self.assertEqual(sorted(p.insight.dedupe_key for p in self.generate(now=timezone.now())), ['b', 'c'])

    def test_a_muted_rule_is_hidden(self):
        temp_rule(self, id='cashflow.mute', module=Module.CASHFLOW, surfaces={Surface.DASHBOARD},
                  sample=sampling.default_sample)(lambda ctx: [])
        self.store('cashflow.mute', insight(rule_id='cashflow.mute'))
        engine.feedback(self.user, 'cashflow.mute', False, profile=self.profile)
        self.assertEqual(len(self.generate()), 1)            # one "not helpful" is only noted
        engine.feedback(self.user, 'cashflow.mute', False, profile=self.profile)
        self.assertEqual(self.generate(), [])                # the second mutes it
        self.assertEqual(InsightFeedback.objects.get(user=self.user, rule_id='cashflow.mute').not_helpful_count, 0)

    def test_unknown_rules_and_old_payloads_are_skipped(self):
        InsightState.objects.create(user=self.user, rule_id='gone.rule', dedupe_key='x', payload={'rule_id': 'gone.rule'},
                                    valid_until=TODAY + timedelta(days=3))
        self.assertEqual(self.generate(), [])

    def test_not_helpful_for_an_unknown_rule_is_refused(self):
        self.assertFalse(engine.feedback(self.user, 'nope.nothing', False))


class PlanGatingTests(StoredBase):
    tier = 'FREE'

    def add_plus_rule(self, sensitive=False):
        @temp_rule(self, id='cashflow.plus_only', module=Module.CASHFLOW, surfaces={Surface.DASHBOARD},
                   min_plan=Plan.PLUS, preview=lambda: 'A deeper look at your cash flow',
                   sample=sampling.default_sample)
        def plus_only(ctx):
            return [insight(rule_id='cashflow.plus_only', sensitive=sensitive, message='You kept ₹18,000 this cycle.')]

    def test_a_free_person_gets_a_kind_preview_with_no_figures_and_a_link_to_pricing(self):
        self.add_plus_rule()
        engine.refresh(self.user, today=TODAY)
        row = InsightState.objects.get(user=self.user, rule_id='cashflow.plus_only')
        self.assertTrue(row.locked)
        self.assertNotIn('18,000', str(row.payload))
        [shown] = self.generate()
        self.assertTrue(shown.locked)
        self.assertEqual(shown.insight.action.url, '/pricing/')
        self.assertEqual(shown.insight.plan, Plan.PLUS)

    def test_a_locked_preview_is_shown_at_most_once_a_week(self):
        self.add_plus_rule()
        engine.refresh(self.user, today=TODAY)
        self.assertEqual(len(self.generate(now=NOW)), 1)
        self.assertEqual(self.generate(now=NOW + timedelta(days=3)), [])
        self.assertEqual(len(self.generate(now=NOW + timedelta(days=8))), 1)

    def test_a_sensitive_insight_has_no_preview_at_all(self):
        self.add_plus_rule(sensitive=True)
        engine.refresh(self.user, today=TODAY)
        self.assertFalse(InsightState.objects.filter(user=self.user, rule_id='cashflow.plus_only').exists())

    def test_upgrading_replaces_the_preview_with_the_real_thing(self):
        self.add_plus_rule()
        engine.refresh(self.user, today=TODAY)
        UserProfile.objects.filter(pk=self.profile.pk).update(tier='PLUS')
        profile = UserProfile.objects.get(pk=self.profile.pk)
        self.assertEqual(engine.generate(self.user, Surface.DASHBOARD, profile=profile, now=NOW), [])   # row is still the preview
        engine.refresh(self.user, today=TODAY, profile=profile)
        [shown] = engine.generate(self.user, Surface.DASHBOARD, profile=profile, now=NOW)
        self.assertFalse(shown.locked)
        self.assertIn('18,000', shown.insight.message)

    def test_the_migrated_rules_are_open_to_every_plan(self):
        self.life()
        engine.refresh(self.user, today=TODAY)
        locked = set(InsightState.objects.filter(user=self.user, locked=True).values_list('rule_id', flat=True))
        self.assertEqual(locked, {'accounts.idle_cash'})       # the one Plus rule that spoke; none of the migrated ones is locked
        self.assertGreaterEqual(len(self.generate(now=NOW)), 3)


class QueryCountTests(StoredBase):
    def test_reading_is_one_query_and_the_first_show_adds_one_write(self):
        self.life()
        engine.refresh(self.user, today=TODAY)
        profile = UserProfile.objects.get(pk=self.profile.pk)
        with self.assertNumQueries(2):             # the read, and the "first shown today" write
            engine.generate(self.user, Surface.DASHBOARD, profile=profile)
        with self.assertNumQueries(1):             # shown already today: just the read
            engine.generate(self.user, Surface.DASHBOARD, profile=profile)

    def test_acting_on_an_insight_is_two_queries(self):
        row = self.store('cashflow.test')
        with self.assertNumQueries(2):
            engine.dismiss(self.user, row.uuid)

    def test_refresh_stays_within_its_ceiling(self):
        self.life()
        with CaptureQueriesContext(connection) as quick:
            engine.refresh(self.user, today=TODAY, costs=('light',))
        self.assertLessEqual(len(quick), 16)        # measured: 15 (what a dashboard visit may run)
        with CaptureQueriesContext(connection) as small:
            engine.refresh(self.user, today=TODAY)
        self.assertLessEqual(len(small), 31)        # measured: 29 (the nightly run: heavy rules, net worth included)

    def test_refresh_does_not_grow_with_the_amount_of_data(self):
        self.life()
        for n in range(30):
            self.category(f'Cat{n}', 1000 + n)
            self.spend(10 + n, month_start(TODAY, 0), f'Cat{n}')
        for n in range(8):
            Account.objects.create(user=self.user, name=f'FD{n}', account_type='FIXED_DEPOSIT', currency='₹',
                                   balance=D('1000'), deposit_maturity_date=TODAY + timedelta(days=5 + n))
        with CaptureQueriesContext(connection) as big:
            engine.refresh(self.user, today=TODAY)
        self.assertLessEqual(len(big), 31)
