"""Phase B: the budget and expense rules, the weekly pool for Free, the page strips, and the facts they read."""
from datetime import date, datetime, timedelta
from unittest import mock

from django.db import connection
from django.test import SimpleTestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone, translation

from expenses.analytics.facts import Facts, load_expense_rows
from expenses.budget_page import build_budget_page
from expenses.budget_suggestions import find_suggestion
from expenses.insights import engine, registry, sampling
from expenses.insights.context import InsightContext
from expenses.insights.sampling import TODAY as SAMPLE_TODAY
from expenses.insights.sampling import _row
from expenses.insights.types import Module, Surface
from expenses.models import Expense, InsightState, UserProfile
from expenses.tests.budget_helpers import backdate_limits

from .base import ENGINE_ON, TODAY, D, InsightBase, month_start, temp_rule
from .test_engine import NOW, StoredBase, insight

RULES = {r.id: r for r in registry.all_rules()}


def run(rule_id, ctx):
    rule = RULES[rule_id]
    with ctx.declared(rule.needs):
        return rule.evaluate(ctx)


def at(day, hour=12, minute=0):
    return timezone.make_aware(datetime(day.year, day.month, day.day, hour, minute))


class PossibleDuplicateTests(SimpleTestCase):
    rule = 'expenses.possible_duplicate'
    DAY = SAMPLE_TODAY - timedelta(days=1)

    def ctx(self, *rows):
        return sampling.scenario(recent=list(rows))

    def pair(self, **second):
        a = _row(1, self.DAY, 'Food', 499, description='Netflix', made=at(self.DAY))
        b_args = dict(id=2, day=self.DAY, category='Food', amount=499, description='Netflix', made=at(self.DAY, 12, 5))
        b_args.update(second)
        return a, _row(**b_args)

    def test_two_alike_entries_close_together(self):
        [found] = run(self.rule, self.ctx(*self.pair()))
        self.assertEqual(found.numbers['ids'], [1, 2])
        self.assertRegex(found.dedupe_key, r'^dup:\d+:\d+$')          # ids only: no amount, no description in a key
        self.assertIn('2 entries', found.message)

    def test_not_alike_enough(self):
        for change in ({'amount': 500}, {'account': 2}, {'description': 'Hotstar'}):
            self.assertEqual(run(self.rule, self.ctx(*self.pair(**change))), [], change)

    def test_a_day_apart_is_two_purchases(self):
        self.assertEqual(run(self.rule, self.ctx(*self.pair(made=at(self.DAY, 12, 0) + timedelta(hours=25)))), [])

    def test_postings_by_the_app_and_imports_are_left_alone(self):
        self.assertEqual(run(self.rule, self.ctx(*self.pair(description='Netflix (Recurring)'),
                                                  )), [])
        a, b = self.pair(batch=True)
        self.assertEqual(run(self.rule, self.ctx(a, b)), [])

    def test_three_alike_entries_are_one_insight(self):
        a, b = self.pair()
        c = _row(3, self.DAY, 'Food', 499, description='Netflix', made=at(self.DAY, 12, 9))
        [found] = run(self.rule, self.ctx(a, b, c))
        self.assertIn('3 entries', found.message)

    def test_nothing_without_recent_entries(self):
        self.assertEqual(run(self.rule, self.ctx()), [])


class UnusualAmountTests(SimpleTestCase):
    rule = 'expenses.unusual_amount'

    def ctx(self, amount, prior=12, spread=True, **extra):
        rows = [_row(i, SAMPLE_TODAY - timedelta(days=15 + 12 * i if spread else 8 + i // 2), 'Groceries', 1000)
                for i in range(1, prior + 1)]
        rows.append(_row(500, SAMPLE_TODAY - timedelta(days=1), 'Groceries', amount, **extra))
        return sampling.scenario(rows=rows)

    def test_happy_path(self):
        [found] = run(self.rule, self.ctx(6400))
        self.assertEqual(found.numbers, {'id': 500, 'times': 6})
        self.assertEqual(found.dedupe_key, 'big:500')

    def test_exactly_three_times_the_usual_is_the_line(self):
        self.assertEqual(run(self.rule, self.ctx(2999)), [])
        self.assertEqual(len(run(self.rule, self.ctx(3000))), 1)

    def test_needs_ten_earlier_entries_over_two_cycles(self):
        self.assertEqual(run(self.rule, self.ctx(6400, prior=9)), [])
        self.assertEqual(run(self.rule, self.ctx(6400, spread=False)), [])        # all in one cycle: not yet "usual"

    def test_a_duplicate_is_not_also_called_unusual(self):
        ctx = self.ctx(6400)
        ctx._cache['rows'].append(_row(501, SAMPLE_TODAY - timedelta(days=1), 'Groceries', 6400))
        self.assertEqual(run(self.rule, ctx), [])

    def test_entries_the_app_posted_are_skipped(self):
        self.assertEqual(run(self.rule, self.ctx(6400, description='Rent (Recurring)')), [])

    def test_only_the_last_week_is_considered(self):
        ctx = self.ctx(6400)
        ctx._cache['rows'][-1] = _row(500, SAMPLE_TODAY - timedelta(days=9), 'Groceries', 6400)
        self.assertEqual(run(self.rule, ctx), [])

    def test_it_is_a_nightly_rule_for_plus(self):
        rule = RULES[self.rule]
        self.assertEqual((rule.cost, int(rule.min_plan)), ('heavy', 1))


class WeekdayAndTimingTests(SimpleTestCase):
    def test_a_busy_weekday_is_named_in_the_readers_language(self):
        rule = RULES['expenses.busiest_weekday']
        for lang, name in (('en', 'Saturday'), ('hi', 'शनिवार'), ('mr', 'शनिवार')):
            with translation.override(lang):
                ctx = sampling.weekday_sample(lang)
                with ctx.declared(rule.needs):
                    [found] = rule.evaluate(ctx)
            self.assertIn(name, found.title)
            self.assertEqual(found.numbers['weekday'], 5)

    def test_even_spending_has_no_busiest_day(self):
        ctx = sampling.weekday_sample()
        for back in range(1, 71):
            day = SAMPLE_TODAY - timedelta(days=back)
            ctx.facts.expense_cats[ctx.facts.index(day)] = {'dining': D('900')}
        self.assertEqual(run('expenses.busiest_weekday', ctx), [])

    def test_too_few_days_with_spending_say_nothing(self):
        ctx = sampling.weekday_sample()
        for back in range(1, 71):
            day = SAMPLE_TODAY - timedelta(days=back)
            if day.weekday() != 5:
                ctx.facts.expense_cats[ctx.facts.index(day)] = None
        self.assertEqual(run('expenses.busiest_weekday', ctx), [])        # only Saturdays: 8 days, not enough to see a pattern

    def test_timing_needs_three_cycles_each_front_loaded(self):
        [found] = run('expenses.spending_timing', sampling.scenario())
        self.assertEqual(found.numbers['pct'], 100)
        ctx = sampling.scenario()
        first = ctx.cycle_of(ctx.keys_back(2)[1])[0]
        ctx.facts.expense_cats[ctx.facts.index(first)] = None
        ctx.facts.expense_cats[ctx.facts.index(first) + 20] = {'rent': D('50000')}        # late in that cycle
        ctx._cache.pop('summaries', None)
        self.assertEqual(run('expenses.spending_timing', ctx), [])

    def test_timing_wording_follows_months_or_cycles(self):
        month = run('expenses.spending_timing', sampling.scenario())[0].message
        cycle = run('expenses.spending_timing', sampling.scenario(salary_day=5))[0].message
        self.assertIn('last 3 months', month)
        self.assertIn('last 3 cycles', cycle)


class CategoryChangeTests(SimpleTestCase):
    rule = 'expenses.category_change'

    def ctx_with_travel_still_in_use(self):
        ctx = sampling.scenario()
        ctx.facts.expense_cats[ctx.facts.index(ctx.cycle[0])]['travel'] = D('3000')      # less than the 9,000 of last time
        ctx._cache.pop('summaries', None)
        return ctx

    def test_one_up_and_one_down_against_the_same_point_last_time(self):
        found = {i.numbers['direction']: i for i in run(self.rule, self.ctx_with_travel_still_in_use())}
        self.assertEqual((found['up'].numbers['category'], found['down'].numbers['category']), ('dining', 'travel'))
        self.assertEqual(found['down'].tone.value, 'positive')
        self.assertIn('same point last time', ' '.join(e.label.lower() for e in found['up'].evidence))

    def test_a_category_with_nothing_yet_is_a_bill_not_paid_not_a_saving(self):
        found = {i.numbers['direction'] for i in run(self.rule, sampling.scenario())}      # travel: 9,000 last time, 0 so far
        self.assertEqual(found, {'up'})

    def test_not_in_the_first_week(self):
        self.assertEqual(run(self.rule, sampling.scenario(today=date(2026, 10, 5))), [])

    def test_a_small_move_in_a_big_total_is_not_news(self):
        ctx = sampling.scenario()
        facts = ctx.facts
        before = ctx.cycle_of(ctx.keys_back(1)[0])[0]
        facts.expense_cats[facts.index(before)] = {'rent': D('20000'), 'dining': D('9000'), 'groceries': D('12000')}
        facts.expense_cats[facts.index(ctx.cycle[0])] = {'rent': D('20000'), 'dining': D('9900'), 'groceries': D('12000')}
        ctx._cache.pop('summaries', None)
        self.assertEqual(run(self.rule, ctx), [])        # +10% of dining, but only 900 of 41,000

    def test_nothing_to_compare_with_says_nothing(self):
        ctx = sampling.scenario()
        before = ctx.cycle_of(ctx.keys_back(1)[0])[0]
        ctx.facts.expense_cats[ctx.facts.index(before)] = None
        ctx._cache.pop('summaries', None)
        self.assertEqual(run(self.rule, ctx), [])


class LimitPatternTests(SimpleTestCase):
    def test_over_each_of_three_budget_months(self):
        [found] = run('budgets.limit_often_over', sampling.often_over_sample())
        self.assertEqual(found.numbers, {'category': 'dining'})
        self.assertEqual([e.label for e in found.evidence], ['Jul', 'Aug', 'Sep'])
        self.assertIn('₹9,000 of ₹8,000', found.evidence[0].value)

    def test_one_month_inside_the_limit_breaks_the_pattern(self):
        ctx = sampling.often_over_sample()
        ctx.budget_history.spend[ctx.budget_history.months[1]]['dining'] = D('7000')
        self.assertEqual(run('budgets.limit_often_over', ctx), [])

    def test_a_month_without_a_limit_breaks_it_too(self):
        ctx = sampling.often_over_sample()
        del ctx.budget_history.limits[ctx.budget_history.months[0]]['dining']
        self.assertEqual(run('budgets.limit_often_over', ctx), [])

    def test_a_zero_limit_is_no_limit(self):
        ctx = sampling.often_over_sample()
        for month in ctx.budget_history.months:
            ctx.budget_history.limits[month]['dining'] = D('0')
        self.assertEqual(run('budgets.limit_often_over', ctx), [])

    def test_under_means_seventy_percent_or_less_every_time(self):
        self.assertEqual(len(run('budgets.limit_often_under', sampling.often_under_sample())), 1)
        ctx = sampling.often_under_sample()                   # limit 8000
        for month in ctx.budget_history.months:
            ctx.budget_history.spend[month]['travel'] = D('5600')
        self.assertEqual(len(run('budgets.limit_often_under', ctx)), 1)         # exactly 70%
        ctx.budget_history.spend[ctx.budget_history.months[2]]['travel'] = D('5601')
        self.assertEqual(run('budgets.limit_often_under', ctx), [])

    def test_both_are_plus_and_nightly_with_a_figure_free_preview(self):
        for rule_id in ('budgets.limit_often_over', 'budgets.limit_often_under', 'expenses.unusual_amount'):
            rule = RULES[rule_id]
            self.assertEqual((rule.cost, int(rule.min_plan)), ('heavy', 1))
            self.assertNotRegex(str(rule.preview()), r'\d')


class RoomToShareTests(InsightBase):
    def months_ago(self, n):
        return month_start(TODAY, n)

    def setup_history(self):
        food, fuel = self.category('Food', 5000), self.category('Fuel', 10000)
        backdate_limits(self.user, self.months_ago(5))
        self.spend(5000, self.months_ago(0), 'Food')
        for n in (1, 2, 3):
            self.spend(300, self.months_ago(n).replace(day=5), 'Fuel')
        return food, fuel

    def test_it_is_the_budget_pages_own_suggestion(self):
        food, fuel = self.setup_history()
        page = build_budget_page(self.user, month_start(TODAY, 0), TODAY)
        legacy = find_suggestion(self.user, month_start(TODAY, 0), page['categories'])
        ctx = InsightContext(self.user, today=TODAY, profile=self.profile)
        [found] = run('budgets.room_to_share', ctx)
        self.assertEqual(found.dedupe_key, legacy['key'])
        self.assertEqual(found.numbers, {'donor': legacy['donor_id'], 'needy': legacy['needy_id'],
                                         'amount': legacy['amount']})

    def test_a_dismissal_on_the_budget_page_is_respected(self):
        from expenses.models import BudgetSuggestionDismissal
        self.setup_history()
        page = build_budget_page(self.user, month_start(TODAY, 0), TODAY)
        BudgetSuggestionDismissal.objects.create(
            user=self.user, key=find_suggestion(self.user, month_start(TODAY, 0), page['categories'])['key'])
        self.assertEqual(run('budgets.room_to_share', InsightContext(self.user, today=TODAY, profile=self.profile)), [])

    def test_nothing_when_no_category_needs_attention(self):
        self.category('Fuel', 10000)
        backdate_limits(self.user, self.months_ago(5))
        self.assertEqual(run('budgets.room_to_share', InsightContext(self.user, today=TODAY, profile=self.profile)), [])

    def test_it_skips_the_history_queries_when_nothing_could_be_suggested(self):
        self.category('Fuel', 10000)
        ctx = InsightContext(self.user, today=TODAY, profile=self.profile)
        with CaptureQueriesContext(connection) as queries:
            run('budgets.room_to_share', ctx)
        self.assertLessEqual(len(queries), 3)                  # the Budget page's own three

    def test_it_reads_like_the_banner_in_both_languages(self):
        self.assertEqual(len(run('budgets.room_to_share', sampling.room_to_share_sample())), 1)


class RunoutHistoryTests(SimpleTestCase):
    def test_what_helped_comes_from_cycles_that_stayed_within_the_limit(self):
        [found] = run('budgets.category_runout', sampling.runout_sample())
        self.assertIn('about ₹685 a week', found.detail)           # 3,000 over 31 days, a week's worth

    def test_nothing_is_claimed_when_no_earlier_cycle_stayed_within(self):
        ctx = sampling.scenario(budgets=[('Transport', 5000, 4000)], history_spend={'Transport': 9000})
        [found] = run('budgets.category_runout', ctx)
        self.assertIsNone(found.detail)


class FactsAdditionTests(InsightBase):
    def test_expense_rows_come_back_in_one_query_without_models(self):
        self.spend(100, TODAY, 'Food')
        self.spend(200, TODAY - timedelta(days=1), 'Food')
        with self.assertNumQueries(1):
            rows = load_expense_rows(self.user, TODAY - timedelta(days=3), TODAY)
        self.assertEqual([(r.amount, r.category, r.from_import) for r in rows],
                         [(D('200.00'), 'Food', False), (D('100.00'), 'Food', False)])

    def test_daily_expenses_leave_out_interest_and_capital_events(self):
        facts = Facts(first=date(2026, 10, 1), last=date(2026, 10, 3))
        facts.expense_cats[0] = {'food': D('10'), 'rent': D('5')}
        facts.interest[0] = D('100')
        facts.capital_counted[0] = D('1000')
        self.assertEqual(facts.daily_expenses(date(2026, 10, 1), date(2026, 10, 3)), [D('15'), D('0'), D('0')])
        self.assertEqual(facts.daily_spending(date(2026, 10, 1), date(2026, 10, 1)), [D('1115')])


class WeeklyPoolTests(StoredBase):
    tier = 'FREE'

    def pool_rule(self, rule_id, pool='weekly'):
        temp_rule(self, id=rule_id, module=Module.EXPENSES, surfaces={Surface.DASHBOARD}, pool=pool,
                  sample=sampling.default_sample)(lambda ctx: [])
        return self.store(rule_id, insight(rule_id=rule_id, module=Module.EXPENSES, priority=40, key=rule_id))

    def shown(self, now=NOW, **kw):
        return [p.insight.rule_id for p in self.generate(now=now, **kw)]

    def test_a_free_person_gets_one_pool_observation_a_week(self):
        self.pool_rule('expenses.pool_a')
        self.pool_rule('expenses.pool_b')
        first = self.shown()
        self.assertEqual(len(first), 1)
        self.assertEqual(self.shown(now=NOW + timedelta(days=3)), first)          # it stays all week
        later = self.shown(now=NOW + timedelta(days=8))
        self.assertEqual(len(later), 1)
        self.assertNotEqual(later, first)                                        # then the next one gets its turn
        self.assertEqual(self.shown(now=NOW + timedelta(days=17)), [])           # nothing left unseen

    def test_rules_outside_the_pool_are_not_limited(self):
        self.pool_rule('expenses.other_a', pool=None)
        self.pool_rule('expenses.other_b', pool=None)
        self.assertEqual(len(self.shown()), 2)

    def test_plus_sees_every_pool_rule(self):
        UserProfile.objects.filter(pk=self.profile.pk).update(tier='PLUS')
        self.pool_rule('expenses.pool_a')
        self.pool_rule('expenses.pool_b')
        self.assertEqual(len(self.shown()), 2)

    def test_the_real_pool_is_the_weekday_and_timing_observations(self):
        pooled = sorted(r.id for r in registry.all_rules() if r.pool == 'weekly')
        self.assertEqual(pooled, ['expenses.busiest_weekday', 'expenses.spending_timing'])


@ENGINE_ON
class StripTests(InsightBase):
    def get_strip(self, modules):
        return self.client.get(reverse('insights-panel') + f'?surface=module&modules={modules}&strip=1')

    def test_a_strip_with_nothing_to_say_is_empty(self):
        self.assertEqual(self.get_strip('budgets').content, b'')

    def test_a_strip_shows_only_its_pages_modules_in_a_card(self):
        self.life()
        self.category('Groceries', 10000)
        with mock.patch('django.utils.timezone.localdate', return_value=TODAY):
            html = self.get_strip('expenses,cashflow').content.decode()
        self.assertIn('card shadow-sm', html)
        self.assertIn('data-bs-toggle="collapse"', html)               # one compact line each, opened on a click
        self.assertIn('.insight-title { font-weight: 400; }', html)    # not bold on a phone
        self.assertIn('@media (min-width: 768px) { .insight-title { font-weight: 600; } }', html)
        self.assertIn('.insight-row:not(.collapsed) .insight-summary { display: none !important; }', html)   # not said twice
        self.assertNotIn('class="btn-action-outline"', html.split('insight-body-')[0])        # nothing but the line until it is opened
        self.assertIn('mt-3 mb-3', html)                              # breathing room from what is above it
        self.assertNotIn('btn-action-outline', html)                  # the next step is a quiet link here, not a button
        self.assertIn('bi-arrow-right', html)
        self.assertIn('Spending is running a little higher', html)
        self.assertNotIn('Groceries went past its limit', html)       # that belongs to the Budget page
        self.assertNotIn('Groceries went past its limit', self.get_strip('budgets').content.decode())    # which already says it

    def test_the_budget_strip_leaves_out_what_the_page_already_says(self):
        with mock.patch('expenses.insights.views.engine.generate', return_value=[]) as generate:
            self.get_strip('budgets')
            self.assertEqual(generate.call_args.kwargs['skip_rules'],
                             {'budgets.limit_passed', 'budgets.limit_nearing', 'budgets.room_to_share'})
            self.get_strip('expenses,cashflow')
            self.assertEqual(generate.call_args.kwargs['skip_rules'], set())

    def test_room_to_share_is_still_offered_on_the_dashboard(self):
        for name, limit in (('Food', 5000), ('Fuel', 10000)):
            self.category(name, limit)
        backdate_limits(self.user, self.month(5))
        self.spend(5000, month_start(TODAY, 0), 'Food')
        for n in (1, 2, 3):
            self.spend(300, month_start(TODAY, n).replace(day=5), 'Fuel')
        engine.refresh(self.user, today=TODAY)
        rules = [p.insight.rule_id for p in engine.generate(self.user, Surface.MODULE, modules={Module.BUDGETS}, limit=10,
                                                           profile=UserProfile.objects.get(pk=self.profile.pk))]
        self.assertIn('budgets.room_to_share', rules)

    def month(self, back):
        return month_start(TODAY, back)

    def test_pages_embed_their_strip_only_when_the_engine_is_on(self):
        self.category('Food', 5000)
        budget = self.client.get(reverse('budget')).content.decode()
        self.assertIn('modules=budgets&strip=1', budget)
        expenses = self.client.get(reverse('expense-list')).content.decode()
        self.assertIn('modules=expenses,cashflow&strip=1', expenses)
        with override_settings(INSIGHTS_ENGINE_MODE='off'):
            self.assertNotIn('strip=1', self.client.get(reverse('budget')).content.decode())
            self.assertNotIn('strip=1', self.client.get(reverse('expense-list')).content.decode())


class NightlyOnlyTests(InsightBase):
    def test_a_dashboard_visit_never_runs_the_nightly_rules(self):
        self.life()
        engine.refresh(self.user, today=TODAY, costs=('light',))
        rules = set(InsightState.objects.filter(user=self.user).values_list('rule_id', flat=True))
        self.assertFalse(rules & {'budgets.limit_often_over', 'budgets.limit_often_under', 'expenses.unusual_amount'})

    def test_the_nightly_run_reads_history_only_once_for_all_the_rules_that_need_it(self):
        self.category('Dining', 8000)
        self.life()
        with CaptureQueriesContext(connection) as queries:
            engine.refresh(self.user, today=TODAY)
        limits = [q for q in queries.captured_queries if 'budgetlimitversion' in q['sql']]
        self.assertLessEqual(len(limits), 2)         # the Budget page's own, and the one history query

    def test_a_row_that_is_a_duplicate_shows_up_after_a_dashboard_refresh(self):
        made = timezone.now()
        for _ in range(2):
            Expense.objects.create(user=self.user, date=timezone.localdate(), amount=D('499'), category='Food',
                                   currency='₹', account=self.cash, payment_method='Cash', description='Netflix')
        self.income(1, month_start(timezone.localdate(), 0))
        engine.refresh(self.user, costs=('light',))
        self.assertTrue(InsightState.objects.filter(user=self.user, rule_id='expenses.possible_duplicate').exists())
        self.assertLess(made, timezone.now() + timedelta(seconds=1))
