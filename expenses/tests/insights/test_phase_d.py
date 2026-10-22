"""Phase D: income, goals, transfers and investing, holdings, capital events, the calendar and net worth."""
from datetime import date, timedelta
from types import SimpleNamespace

from django.db import connection
from django.test import SimpleTestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from expenses import goal_progress
from expenses.analytics.facts import IncomeRow, load_facts, load_income_rows
from expenses.insights import engine, registry, sampling
from expenses.insights.context import GoalFacts, HoldingValue, InsightContext
from expenses.insights.sampling import TODAY as SAMPLE_TODAY
from expenses.insights.sampling import _goal, _income, _put
from expenses.insights.types import Module, Plan, Surface
from expenses.models import (
    Account,
    CapitalEvent,
    GoalContribution,
    Holding,
    InsightState,
    NetWorthSnapshot,
    RecurringTransaction,
    SavingsGoal,
    UserProfile,
    Valuation,
)

from .base import ENGINE_ON, TODAY, D, InsightBase

RULES = {r.id: r for r in registry.all_rules()}


def run(rule_id, ctx):
    rule = RULES[rule_id]
    with ctx.declared(rule.needs):
        return rule.evaluate(ctx)


class GoalProgressTests(SimpleTestCase):
    def test_the_highest_mark_reached(self):
        self.assertEqual([goal_progress.milestone_reached(p) for p in (10, 49.9, 50, 89, 90, 99.9, 100)],
                         [None, None, 50, 50, 90, 90, 100])

    def test_pace_needs_two_contributions_over_a_month(self):
        today = date(2026, 10, 22)
        rows = [(today - timedelta(days=ago), 5000) for ago in (80, 50, 20)]
        self.assertEqual(round(goal_progress.monthly_pace(rows, today)), 5700)     # 15,000 over the 80 days since the first
        self.assertIsNone(goal_progress.monthly_pace(rows[:1], today))
        self.assertIsNone(goal_progress.monthly_pace([(today - timedelta(days=10), 5), (today - timedelta(days=3), 5)], today))

    def test_pace_uses_only_the_last_three_months(self):
        today = date(2026, 10, 22)
        rows = [(today - timedelta(days=ago), 5000) for ago in (300, 280, 80, 50, 20)]
        self.assertEqual(round(goal_progress.monthly_pace(rows, today)), 5067)

    def test_months_to_finish_rounds_up_and_never_finishes_at_nothing(self):
        self.assertEqual(goal_progress.months_to_finish(60000, 6760), 9)
        self.assertEqual(goal_progress.months_to_finish(100, 5000), 1)
        self.assertIsNone(goal_progress.months_to_finish(0, 5000))
        self.assertIsNone(goal_progress.months_to_finish(5000, 0))

    def test_finish_date_is_the_first_of_a_month(self):
        self.assertEqual(goal_progress.finish_date(date(2026, 10, 22), 9), date(2027, 7, 1))
        self.assertEqual(goal_progress.finish_date(date(2026, 12, 5), 1), date(2027, 1, 1))


class IncomeRuleTests(SimpleTestCase):
    def test_salary_share_and_what_the_rest_did(self):
        [found] = run('income.steady_vs_variable', sampling.income_sample())
        self.assertEqual(found.numbers, {'share': 87, 'low': '6000', 'high': '21000'})
        self.assertIn('rest varied between ₹6k and ₹21k a cycle', found.message)

    def test_one_cycle_of_other_income_says_what_it_came_to(self):
        ctx = sampling.scenario(income_rows=[_income(date(2026, 7, 1), 60000), _income(date(2026, 8, 1), 60000),
                                             _income(date(2026, 9, 1), 60000), _income(date(2026, 8, 9), 30000, 'Business')])
        [found] = run('income.steady_vs_variable', ctx)
        self.assertIn('came to around ₹10k a cycle', found.message)

    def test_all_salary_or_no_salary_is_not_a_story(self):
        only_salary = sampling.scenario(income_rows=[_income(date(2026, m, 1), 60000) for m in (7, 8, 9)])
        self.assertEqual(run('income.steady_vs_variable', only_salary), [])
        none = sampling.scenario(income_rows=[_income(date(2026, m, 1), 60000, 'Business') for m in (7, 8, 9)])
        self.assertEqual(run('income.steady_vs_variable', none), [])

    def test_it_is_nightly_plus_with_a_figure_free_preview(self):
        rule = RULES['income.steady_vs_variable']
        self.assertEqual((rule.cost, rule.min_plan, rule.min_history), ('heavy', Plan.PLUS, 3))
        self.assertEqual(str(rule.preview()), 'How steady your income is')

    def test_salary_late_after_three_days_with_none_this_cycle(self):
        [found] = run('income.salary_late', sampling.salary_late_sample())
        self.assertEqual(found.numbers, {'day': 15, 'late_by': 7})
        self.assertEqual(found.tone.value, 'gentle_attention')
        self.assertNotIn(Surface.PUSH, found.surfaces)
        self.assertFalse(found.sensitive)

    def test_not_late_yet_arrived_or_long_past(self):
        rows = [_income(date(2026, 8, 15), 60000), _income(date(2026, 9, 15), 60000)]
        self.assertEqual(run('income.salary_late', sampling.scenario(today=date(2026, 10, 17), income_rows=rows)), [])
        self.assertEqual(run('income.salary_late', sampling.scenario(income_rows=rows + [_income(date(2026, 10, 16), 60000)])), [])
        first = [_income(date(2026, 8, 1), 60000), _income(date(2026, 9, 1), 60000)]
        self.assertEqual(run('income.salary_late', sampling.scenario(income_rows=first)), [])         # 21 days: past the window

    def test_the_day_is_learned_from_two_months_of_salary_not_one_and_not_other_income(self):
        one = sampling.scenario(income_rows=[_income(date(2026, 9, 15), 60000)])
        self.assertEqual(run('income.salary_late', one), [])
        other = sampling.scenario(income_rows=[_income(date(2026, 8, 15), 9000, 'Business'), _income(date(2026, 9, 15), 9000, 'Business')])
        self.assertEqual(run('income.salary_late', other), [])

    def test_a_set_salary_day_is_the_expected_day(self):
        rows = [_income(date(2026, 8, 5), 60000), _income(date(2026, 9, 5), 60000)]
        ctx = sampling.scenario(salary_day=5, today=date(2026, 10, 9), income_rows=rows)
        [found] = run('income.salary_late', ctx)
        self.assertEqual(found.numbers, {'day': 5, 'late_by': 4})
        self.assertEqual(run('income.salary_late', sampling.scenario(salary_day=5, today=date(2026, 10, 9),
                                                                    income_rows=rows + [_income(date(2026, 10, 6), 60000)])), [])

    def test_a_long_quiet_spell_after_a_regular_run_is_sensitive_with_no_preview(self):
        [found] = run('income.gap', sampling.income_gap_sample())
        self.assertEqual(found.numbers, {'days': 113})
        self.assertTrue(found.sensitive)
        self.assertIsNone(RULES['income.gap'].preview)
        self.assertEqual(found.surfaces, frozenset({Surface.MODULE}))

    def test_a_recent_income_or_too_little_history_is_no_gap(self):
        recent = sampling.scenario(income_rows=[_income(date(2026, m, 1), 60000) for m in (7, 8, 9, 10)])
        self.assertEqual(run('income.gap', recent), [])
        short = sampling.scenario(income_rows=[_income(date(2026, 6, 1), 60000), _income(date(2026, 7, 1), 60000)])
        self.assertEqual(run('income.gap', short), [])
        self.assertEqual(run('income.gap', sampling.scenario()), [])


class GoalRuleTests(SimpleTestCase):
    def test_a_goal_on_track_at_its_recent_pace(self):
        [found] = run('goals.projected_finish', sampling.goals_sample())
        self.assertEqual(found.title, 'Goa trip is on track for July')
        self.assertIn('reaches ₹1L around July 2027', found.message)
        self.assertEqual(found.numbers['months'], 9)

    def test_a_goal_that_lands_after_its_date_is_said_gently(self):
        gf = _goal('Goa trip', 100000, [(100, 20000), (90, 5000), (60, 5000), (30, 5000), (3, 5000)], target_date=date(2027, 2, 1))
        [found] = run('goals.projected_finish', sampling.scenario(goals=[gf]))
        self.assertEqual(found.title, 'Goa trip may finish a little later')
        self.assertIn('a little after your February 2027 date', found.message)
        self.assertEqual(found.tone.value, 'gentle_attention')

    def test_a_date_met_with_room_to_spare_is_on_track(self):
        gf = _goal('Goa trip', 100000, [(100, 20000), (90, 5000), (60, 5000), (30, 5000), (3, 5000)], target_date=date(2028, 1, 1))
        [found] = run('goals.projected_finish', sampling.scenario(goals=[gf]))
        self.assertIn('on track', found.title)

    def test_locked_complete_and_too_new_goals_say_nothing(self):
        gf = sampling.goals_sample().goals[0]
        locked = GoalFacts(gf.goal, gf.contributions, True)
        self.assertEqual(run('goals.projected_finish', sampling.scenario(goals=[locked])), [])
        done = _goal('Done', 1000, [(100, 600), (50, 600)])
        self.assertEqual(run('goals.projected_finish', sampling.scenario(goals=[done])), [])
        new = _goal('New', 100000, [(10, 5000), (3, 5000)])
        self.assertEqual(run('goals.projected_finish', sampling.scenario(goals=[new])), [])

    def test_a_goal_that_has_gone_quiet_gets_no_finish_date_or_small_change(self):
        quiet = sampling.goal_stalled_sample()
        self.assertEqual(run('goals.projected_finish', quiet), [])
        self.assertEqual(run('goals.small_change', quiet), [])
        self.assertEqual(len(run('goals.stalled', quiet)), 1)

    def test_a_goal_decades_away_says_nothing(self):
        slow = _goal('House', 100000000, [(100, 500), (60, 500), (30, 500)])
        self.assertEqual(run('goals.projected_finish', sampling.scenario(goals=[slow])), [])

    def test_a_small_change_brings_the_finish_forward(self):
        [found] = run('goals.small_change', sampling.goals_sample())
        self.assertEqual(found.numbers, {'goal': 1, 'extra': '3000', 'brought_forward': 2})
        self.assertIn('Adding ₹3k a month', found.message)
        self.assertIn('the current pace is fine too', found.message)

    def test_a_goal_nearly_done_has_no_change_worth_suggesting(self):
        near = _goal('Goa trip', 31000, [(100, 20000), (90, 5000), (60, 5000)])
        self.assertEqual(run('goals.small_change', sampling.scenario(goals=[near])), [])

    def test_quiet_for_forty_five_days_after_two_contributions(self):
        [found] = run('goals.stalled', sampling.goal_stalled_sample())
        self.assertEqual(found.numbers, {'goal': 1, 'days': 60})
        self.assertIn('stands at 38% of ₹40k', found.message)

    def test_recent_contributions_or_a_single_one_are_not_a_pause(self):
        recent = _goal('Goa trip', 40000, [(100, 5000), (80, 5000), (30, 5000)])
        self.assertEqual(run('goals.stalled', sampling.scenario(goals=[recent])), [])
        single = _goal('Goa trip', 40000, [(100, 5000)])
        self.assertEqual(run('goals.stalled', sampling.scenario(goals=[single])), [])

    def test_a_mark_reached_in_the_last_two_weeks(self):
        [found] = run('goals.milestone', sampling.goal_milestone_sample())
        self.assertEqual(found.title, 'Goa trip is halfway')
        self.assertEqual(found.numbers, {'goal': 1, 'threshold': 50})
        self.assertEqual(found.dedupe_key.split(':')[1], '50')
        self.assertEqual(found.valid_until, SAMPLE_TODAY - timedelta(days=5) + timedelta(days=14))

    def test_ninety_and_complete(self):
        nearly = _goal('Goa trip', 100000, [(60, 60000), (4, 32000)])
        [found] = run('goals.milestone', sampling.scenario(goals=[nearly]))
        self.assertEqual((found.title, found.numbers['threshold']), ('Goa trip is nearly there', 90))
        done = _goal('Goa trip', 100000, [(60, 60000), (2, 40000)])
        [found] = run('goals.milestone', sampling.scenario(goals=[done]))
        self.assertEqual((found.title, found.numbers['threshold']), ('Goa trip is complete', 100))
        self.assertIn('all of ₹1L', found.message)

    def test_an_old_mark_or_contributions_that_do_not_add_up_are_left_alone(self):
        old = _goal('Goa trip', 100000, [(90, 30000), (40, 22000)])
        self.assertEqual(run('goals.milestone', sampling.scenario(goals=[old])), [])
        gf = _goal('Goa trip', 100000, [(60, 30000), (5, 22000)])
        gf.goal.current_amount = D('60000')                 # something other than a contribution moved it
        self.assertEqual(run('goals.milestone', sampling.scenario(goals=[gf])), [])

    def test_the_marks_are_the_ones_the_bell_uses(self):
        self.assertEqual(goal_progress.MILESTONES, (100, 90, 50))
        self.assertEqual(RULES['goals.milestone'].min_plan, Plan.FREE)
        self.assertNotIn(Surface.BELL, RULES['goals.milestone'].surfaces)


class InvestingRuleTests(SimpleTestCase):
    def test_the_share_of_income_against_their_own_usual(self):
        [found] = run('transfers.invested_share', sampling.invested_share_sample())
        self.assertEqual(found.numbers, {'share': 7, 'usual': 15})
        self.assertIn('About 7% of income went into investments this cycle, against your usual 15%.', found.message)

    def test_a_small_difference_an_early_cycle_or_nothing_invested_says_nothing(self):
        ctx = sampling.invested_share_sample()
        _put(ctx.facts, ctx.cycle[0], invested=4100)         # 9,000 of 70,000 is 12.9%: within five points of the usual 15%
        ctx._cache.pop('summaries', None)
        self.assertEqual(run('transfers.invested_share', ctx), [])
        early = sampling.scenario(today=date(2026, 10, 8))
        self.assertEqual(run('transfers.invested_share', early), [])
        self.assertEqual(run('transfers.invested_share', sampling.scenario()), [])


class HoldingRuleTests(SimpleTestCase):
    def test_a_sip_due_days_ago_and_not_recorded(self):
        [found] = run('holdings.sip_missed', sampling.sip_missed_sample())
        self.assertIn('Your ₹5k SIP (Nifty fund SIP) was due on 5 Oct and is not recorded yet.', found.message)
        self.assertEqual(found.dedupe_key.split(':')[1], '2026-10-05')

    def test_not_yet_late_ended_or_not_an_investment(self):
        ctx = sampling.sip_missed_sample()
        sip = ctx.recurring_all[0]
        sip.first_due_date = lambda: SAMPLE_TODAY - timedelta(days=2)
        self.assertEqual(run('holdings.sip_missed', ctx), [])
        sip.first_due_date = lambda: SAMPLE_TODAY - timedelta(days=17)
        sip.end_date = SAMPLE_TODAY - timedelta(days=30)
        self.assertEqual(run('holdings.sip_missed', ctx), [])
        sip.end_date = None
        sip.to_account.account_type = 'SAVINGS_ACCOUNT'
        self.assertEqual(run('holdings.sip_missed', ctx), [])
        sip.to_account.account_type = 'MUTUAL_FUND'
        sip.to_account.is_active = False
        self.assertEqual(run('holdings.sip_missed', ctx), [])

    def test_concentration_by_kind_and_by_holding(self):
        [found] = run('holdings.concentration', sampling.concentration_sample())
        self.assertEqual(found.numbers, {'one': 67, 'kind': 92})
        self.assertIn('Mutual funds are about 92% of your invested money and Nifty fund about 67%.', found.message)
        self.assertIn('just a picture of your mix', found.message)

    def held(self, *rows):
        return sampling.scenario(holdings=[HoldingValue(SimpleNamespace(instrument_name=n, instrument_type=k), D(v)) for n, k, v in rows])

    def test_one_kind_only_and_one_holding_only(self):
        [kind] = run('holdings.concentration', self.held(('A', 'MF', 300), ('B', 'MF', 300), ('C', 'MF', 300), ('D', 'STOCK', 100)))
        self.assertEqual(kind.message.split(' are ')[0], 'Mutual funds')
        self.assertNotIn(' and A ', kind.message)
        [one] = run('holdings.concentration', self.held(('Big', 'MF', 450), ('B', 'STOCK', 300), ('C', 'NPS', 250)))
        self.assertTrue(one.message.startswith('Big is about 45% of your invested money.'))

    def test_a_spread_portfolio_or_fewer_than_three_holdings_says_nothing(self):
        self.assertEqual(run('holdings.concentration', self.held(('A', 'MF', 300), ('B', 'STOCK', 350), ('C', 'NPS', 350))), [])
        self.assertEqual(run('holdings.concentration', self.held(('A', 'MF', 900), ('B', 'MF', 100))), [])

    def test_no_advice_words_in_any_holding_insight(self):
        for sample in (sampling.concentration_sample(), sampling.sip_missed_sample()):
            for rule_id in ('holdings.concentration', 'holdings.sip_missed'):
                for found in run(rule_id, sample):
                    for word in ('should', 'rebalance', 'diversify', 'buy', 'sell'):
                        self.assertNotIn(word, found.message.lower())


class CapitalAndCalendarRuleTests(SimpleTestCase):
    def test_the_savings_rate_with_and_without_a_one_off(self):
        [found] = run('capital_events.rate_effect', sampling.rate_effect_sample())
        self.assertEqual(found.numbers, {'rate': 23, 'with': 6})
        self.assertIn('Your savings rate is 23%, or 6% with Roof repair (₹12k) counted.', found.message)
        self.assertIn('kept out of averages by default', found.message)

    def test_a_small_effect_or_no_one_off_says_nothing(self):
        ctx = sampling.scenario()
        _put(ctx.facts, ctx.cycle[0], extra=500, extra_name='Roof repair')
        ctx._cache.pop('summaries', None)
        self.assertEqual(run('capital_events.rate_effect', ctx), [])
        self.assertEqual(run('capital_events.rate_effect', sampling.scenario()), [])

    def test_the_switch_does_not_reload_or_change_the_original(self):
        ctx = sampling.rate_effect_sample()
        facts = ctx.facts
        other = facts.with_capital_events(True)
        self.assertIs(other.income, facts.income)
        self.assertFalse(facts.include_capital_events)
        self.assertTrue(other.include_capital_events)
        self.assertLess(other.summarize(*ctx.cycle).rate, facts.summarize(*ctx.cycle).rate)

    def test_a_week_heavier_than_a_typical_one(self):
        [found] = run('calendar.heavy_week', sampling.heavy_week_sample())
        self.assertIn('About ₹10k is scheduled over the next 7 days, around 2 times a typical week for you.', found.message)
        self.assertEqual(found.surfaces, frozenset({Surface.DASHBOARD, Surface.MODULE, Surface.BELL, Surface.PUSH}))
        self.assertTrue(RULES['calendar.heavy_week'].push)

    def test_loan_instalments_transfers_and_far_dates_do_not_count(self):
        ctx = sampling.heavy_week_sample()

        def item(day, kind, type_, amount):
            return {'description': 'x', 'amount': D(amount), 'kind': kind, 'type': type_, 'is_salary': False, 'date': day}
        soon, later = SAMPLE_TODAY + timedelta(days=2), SAMPLE_TODAY + timedelta(days=9)
        ctx._cache['pending'] = {soon: [item(soon, 'out', 'LOAN', 90000), item(soon, 'invest', 'TRANSFER', 90000),
                                        item(soon, 'out', 'CAPITAL', 90000)], later: [item(later, 'out', 'EXPENSE', 90000)]}
        self.assertEqual(run('calendar.heavy_week', ctx), [])

    def test_a_normal_week_or_too_little_history_says_nothing(self):
        ctx = sampling.heavy_week_sample()
        soon = SAMPLE_TODAY + timedelta(days=2)
        ctx._cache['pending'] = {soon: [{'description': 'x', 'amount': D(3000), 'kind': 'out', 'type': 'EXPENSE',
                                         'is_salary': False, 'date': soon}]}
        self.assertEqual(run('calendar.heavy_week', ctx), [])
        bare = sampling.scenario(pending=sampling.heavy_week_sample().pending)
        self.assertEqual(run('calendar.heavy_week', bare), [])

    def test_the_weekly_key_is_one_per_week(self):
        [found] = run('calendar.heavy_week', sampling.heavy_week_sample())
        self.assertRegex(found.dedupe_key, r'^\d{4}-W\d{2}$')


class NetWorthRuleTests(SimpleTestCase):
    def scenario(self, before, now, **kw):
        return sampling.scenario(networth=(SimpleNamespace(total=D(before)), SimpleNamespace(total=D(now))), **kw)

    def test_up_with_what_was_kept_and_changes_in_value(self):
        [found] = run('networth.what_moved', sampling.net_worth_sample())
        self.assertEqual(found.numbers, {'change': '26000.00', 'kept': '16000.00'})
        self.assertIn('It is up about ₹26k this cycle: roughly ₹16k from what you kept and ₹10k from changes in value.', found.message)
        self.assertEqual(found.module, Module.ACCOUNTS)

    def test_up_by_less_than_was_kept_and_down(self):
        [less] = run('networth.what_moved', self.scenario(900000, 910000))
        self.assertIn('you kept ₹16k, and changes in value took back ₹6k', less.message)
        [down] = run('networth.what_moved', self.scenario(900000, 880000))
        self.assertIn('It is down about ₹20k this cycle: you kept ₹16k, and changes in value took away ₹36k.', down.message)

    def test_early_in_the_cycle_a_tiny_move_or_nothing_kept_says_nothing(self):
        self.assertEqual(run('networth.what_moved', self.scenario(900000, 926000, today=date(2026, 10, 5))), [])
        self.assertEqual(run('networth.what_moved', self.scenario(900000, 900500)), [])
        self.assertEqual(run('networth.what_moved', self.scenario(0, 26000)), [])
        ctx = self.scenario(900000, 926000)
        for i in range(len(ctx.facts.income)):
            ctx.facts.income[i] = D('0')
        ctx._cache.pop('summaries', None)
        self.assertEqual(run('networth.what_moved', ctx), [])

    def test_without_a_snapshot_from_the_start_of_the_cycle_it_says_nothing(self):
        ctx = sampling.net_worth_sample()
        ctx._cache['networth'] = (None, None)
        self.assertEqual(run('networth.what_moved', ctx), [])

    def test_it_is_nightly_and_plus(self):
        rule = RULES['networth.what_moved']
        self.assertEqual((rule.cost, rule.min_plan), ('heavy', Plan.PLUS))


class PhaseDRulesAreRegisteredTests(SimpleTestCase):
    def test_every_rule_is_there_with_its_plan_and_cost(self):
        expected = {
            'income.steady_vs_variable': (Plan.PLUS, 'heavy'), 'income.salary_late': (Plan.PLUS, 'light'),
            'income.gap': (Plan.PLUS, 'heavy'), 'goals.projected_finish': (Plan.FREE, 'light'),
            'goals.small_change': (Plan.PLUS, 'light'), 'goals.stalled': (Plan.PLUS, 'light'),
            'goals.milestone': (Plan.FREE, 'light'), 'transfers.invested_share': (Plan.PLUS, 'light'),
            'holdings.sip_missed': (Plan.PLUS, 'light'), 'holdings.concentration': (Plan.PLUS, 'heavy'),
            'capital_events.rate_effect': (Plan.PLUS, 'light'), 'calendar.heavy_week': (Plan.PLUS, 'light'),
            'networth.what_moved': (Plan.PLUS, 'heavy'),
        }
        for rule_id, (plan, cost) in expected.items():
            self.assertEqual((RULES[rule_id].min_plan, RULES[rule_id].cost), (plan, cost), rule_id)

    def test_goals_and_holdings_rules_cost_nothing_for_people_without_them(self):
        for rule_id in ('goals.projected_finish', 'goals.small_change', 'goals.stalled', 'goals.milestone'):
            self.assertEqual(RULES[rule_id].requires, frozenset({'goals'}), rule_id)
        self.assertEqual(RULES['holdings.concentration'].requires, frozenset({'holdings'}))
        self.assertEqual(RULES['holdings.sip_missed'].requires, frozenset({'recurring'}))
        self.assertEqual(RULES['calendar.heavy_week'].requires, frozenset({'recurring'}))

    def test_every_module_now_has_a_rule_except_import(self):
        self.assertEqual({m.value for m in Module} - {m.value for m in registry.modules_with_rules()}, {'import', 'analytics'})


class FactsAdditionsTests(InsightBase):
    def test_income_rows_come_oldest_first_in_one_query(self):
        self.income(60000, TODAY - timedelta(days=40))
        self.income(5000, TODAY - timedelta(days=70), 'Business')
        with self.assertNumQueries(1):
            rows = load_income_rows(self.user, TODAY - timedelta(days=100), TODAY)
        self.assertEqual(rows, [IncomeRow(TODAY - timedelta(days=70), 'Business', D('5000.00')),
                                IncomeRow(TODAY - timedelta(days=40), 'Salary', D('60000.00'))])

    def test_the_capital_switch_on_real_facts(self):
        CapitalEvent.objects.create(user=self.user, date=TODAY - timedelta(days=2), amount=D('42000'), currency='₹',
                                    subtype='home_repair', account=self.cash, exclude_from_averages=True)
        self.income(70000, TODAY - timedelta(days=5))
        facts = load_facts(self.user, TODAY - timedelta(days=10), TODAY)
        start, end = TODAY - timedelta(days=10), TODAY
        self.assertEqual(facts.summarize(start, end).capital_extra, D('0'))
        counted = facts.with_capital_events(True).summarize(start, end)
        self.assertEqual(counted.capital_extra, D('42000'))
        self.assertLess(counted.rate, facts.summarize(start, end).rate)


class RealLoaderTests(InsightBase):
    def ctx(self):
        return InsightContext(self.user, today=TODAY, profile=self.profile)

    def goal(self, name='Goa trip', target=100000, created=None):
        goal = SavingsGoal.objects.create(user=self.user, name=name, target_amount=D(target), currency='₹')
        if created:
            SavingsGoal.objects.filter(pk=goal.pk).update(created_at=created)
        return goal

    def contribute(self, goal, amount, days_ago):
        return GoalContribution.objects.create(goal=goal, amount=D(amount), date=TODAY - timedelta(days=days_ago))

    def test_the_inventory_knows_goals_and_holdings(self):
        with self.assertNumQueries(1):
            self.assertEqual(self.ctx().inventory, {'loans': False, 'cards': False, 'recurring': False, 'goals': False,
                                                    'holdings': False})
        self.goal()
        account = Account.objects.create(user=self.user, name='Fund', account_type='MUTUAL_FUND', currency='₹', balance=D('0'))
        Holding.objects.create(account=account, instrument_name='Nifty fund', instrument_type='MF', units=D('10'),
                               avg_cost=D('100'), currency='₹')
        self.assertEqual(self.ctx().inventory['goals'], True)
        self.assertEqual(self.ctx().inventory['holdings'], True)

    def test_goals_arrive_with_their_contributions_in_two_queries_and_the_plan_limit(self):
        first, second = self.goal('One'), self.goal('Two', created=TODAY - timedelta(days=5))
        self.contribute(first, 5000, 40)
        removed = self.contribute(first, 700, 30)
        GoalContribution.objects.filter(pk=removed.pk).update(is_deleted=True)
        self.contribute(second, 100, 3)
        ctx = self.ctx()
        with self.assertNumQueries(2):
            goals = ctx.goals
        by_name = {g.goal.name: g for g in goals}
        self.assertEqual(by_name['One'].contributions, [(TODAY - timedelta(days=40), D('5000.00'))])
        self.assertFalse(any(g.locked for g in goals))                     # Pro: unlimited
        UserProfile.objects.filter(pk=self.profile.pk).update(tier='FREE')
        profile = UserProfile.objects.get(pk=self.profile.pk)
        ordered = InsightContext(self.user, today=TODAY, profile=profile).goals
        self.assertEqual([(g.goal.name, g.locked) for g in ordered], [('Two', False), ('One', True)])

    def test_holdings_are_valued_from_the_latest_valuation_or_their_cost_in_one_query(self):
        account = Account.objects.create(user=self.user, name='Fund', account_type='MUTUAL_FUND', currency='₹', balance=D('0'))
        priced = Holding.objects.create(account=account, instrument_name='Nifty fund', instrument_type='MF', units=D('10'),
                                        avg_cost=D('100'), currency='₹')
        Valuation.objects.create(holding=priced, value=D('900'), as_of_date=TODAY - timedelta(days=30))
        Valuation.objects.create(holding=priced, value=D('1500'), as_of_date=TODAY - timedelta(days=2))
        by_nav = Holding.objects.create(account=account, instrument_name='Index fund', instrument_type='MF', units=D('4'),
                                        avg_cost=D('100'), currency='₹')
        Valuation.objects.create(holding=by_nav, value=D('1'), unit_nav=D('250'), as_of_date=TODAY)
        Holding.objects.create(account=account, instrument_name='Unpriced', instrument_type='STOCK', units=D('2'),
                               avg_cost=D('50'), currency='₹')
        Holding.objects.create(account=account, instrument_name='Closed', instrument_type='STOCK', units=D('2'),
                               avg_cost=D('50'), currency='₹', is_active=False)
        ctx = self.ctx()
        with self.assertNumQueries(1):
            values = {h.holding.instrument_name: h.value for h in ctx.holdings}
        self.assertEqual(values, {'Nifty fund': D('1500.00'), 'Index fund': D('1000.00'), 'Unpriced': D('100.00')})

    def test_a_missed_sip_on_a_real_recurring_transfer(self):
        fund = Account.objects.create(user=self.user, name='Fund', account_type='MUTUAL_FUND', currency='₹', balance=D('0'))
        RecurringTransaction.objects.create(user=self.user, transaction_type='TRANSFER', amount=D('5000'), currency='₹',
                                            description='Nifty SIP', frequency='MONTHLY', start_date=TODAY - timedelta(days=12),
                                            from_account=self.cash, to_account=fund, is_active=True)
        [found] = run('holdings.sip_missed', self.ctx())
        self.assertIn('Nifty SIP', found.message)
        self.assertEqual(found.numbers['due'], (TODAY - timedelta(days=12)).isoformat())

    def test_the_net_worth_pair_is_the_snapshot_before_the_cycle_and_today(self):
        self.assertEqual(self.ctx().networth, (None, None))                      # no snapshot yet: nothing to compare with
        NetWorthSnapshot.objects.create(user=self.user, as_of_date=date(2026, 9, 29), total_net_worth=D('900000'),
                                        total_assets=D('900000'), total_liabilities=D('0'))
        NetWorthSnapshot.objects.create(user=self.user, as_of_date=date(2026, 9, 1), total_net_worth=D('5'),
                                        total_assets=D('5'), total_liabilities=D('0'))
        before, now = self.ctx().networth
        self.assertEqual(before.total, D('900000'))
        self.assertEqual(now.day, TODAY)

    def test_a_snapshot_too_far_back_does_not_stand_for_the_cycle_start(self):
        NetWorthSnapshot.objects.create(user=self.user, as_of_date=date(2026, 9, 20), total_net_worth=D('900000'),
                                        total_assets=D('900000'), total_liabilities=D('0'))
        self.assertEqual(self.ctx().networth, (None, None))

    def test_a_quick_refresh_stays_light_for_someone_with_none_of_it(self):
        self.life()
        with CaptureQueriesContext(connection) as queries:
            engine.refresh(self.user, today=TODAY, costs=('light',))
        sql = ' '.join(q['sql'] for q in queries.captured_queries)
        for table in ('expenses_goalcontribution', 'expenses_valuation'):
            self.assertNotIn(f'FROM "{table}"', sql)
        self.assertLessEqual(len(queries), 17)


class StripsAndSettingsTests(InsightBase):
    def test_each_page_embeds_its_strip_only_when_the_engine_is_on(self):
        self.goal = SavingsGoal.objects.create(user=self.user, name='Goa trip', target_amount=D('1000'), currency='₹')
        pages = (('income-list', 'income'), ('goal-list', 'goals'), ('transfer-list', 'transfers'),
                 ('capital-event-list', 'capital_events'), ('holding-list', 'holdings'), ('calendar', 'calendar'))
        with override_settings(INSIGHTS_ENGINE_MODE='on'):
            for name, modules in pages:
                html = self.client.get(reverse(name)).content.decode()
                self.assertIn(f'modules={modules}&strip=1', html, name)
        with override_settings(INSIGHTS_ENGINE_MODE='off'):
            for name, _modules in pages:
                self.assertNotIn('strip=1', self.client.get(reverse(name)).content.decode(), name)

    @ENGINE_ON
    def test_the_strip_for_a_new_module_answers_and_says_nothing_when_empty(self):
        for modules in ('income', 'goals', 'transfers', 'holdings', 'capital_events', 'calendar'):
            response = self.client.get(reverse('insights-panel') + f'?surface=module&modules={modules}&strip=1')
            self.assertEqual(response.status_code, 200, modules)
            self.assertEqual(response.content, b'', modules)

    @ENGINE_ON
    def test_a_salary_gone_quiet_shows_in_the_income_strip_and_a_free_plan_sees_a_figure_free_line(self):
        for month in (7, 8, 9):
            self.income(60000, date(2026, month, 15))
            self.spend(5000, date(2026, month, 3))
        engine.refresh(self.user, today=TODAY, profile=UserProfile.objects.get(pk=self.profile.pk))
        row = InsightState.objects.get(user=self.user, rule_id='income.salary_late')
        self.assertFalse(row.locked)
        self.assertEqual(row.payload['action'][1], reverse('income-create'))
        UserProfile.objects.filter(pk=self.profile.pk).update(tier='FREE')
        InsightState.objects.filter(user=self.user).delete()
        engine.refresh(self.user, today=TODAY, profile=UserProfile.objects.get(pk=self.profile.pk))
        locked = InsightState.objects.get(user=self.user, rule_id='income.salary_late')
        self.assertTrue(locked.locked)
        self.assertEqual(locked.payload['title'], 'Salary has not arrived yet')
        self.assertNotRegex(str(locked.payload), r'\d{2},\d{3}')

    def test_the_bell_still_announces_goal_marks_by_its_own_slug(self):
        from expenses.management.commands.send_notifications import MILESTONE_LABELS
        self.assertEqual(sorted(MILESTONE_LABELS), sorted(goal_progress.MILESTONES))

    def test_the_new_rules_are_documented(self):
        text = (__import__('pathlib').Path(__file__).resolve().parents[3] / 'docs' / 'insights-rules.md').read_text()
        for rule in registry.all_rules():
            self.assertIn(f'`{rule.id}`', text, rule.id)
