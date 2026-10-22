"""Each rule: the happy path, not enough data, and the edge cases (zero budget, multi-currency, a salary cycle that
crosses a month boundary, a deleted account, one month of data)."""
from datetime import date, timedelta
from types import SimpleNamespace

from django.test import SimpleTestCase
from django.utils import translation

from expenses.analytics.facts import Facts
from expenses.insights import registry, sampling
from expenses.insights.context import InsightContext, UndeclaredNeed
from expenses.insights.format import Fmt
from expenses.models import Account, RecurringTransaction

from .base import TODAY, D, InsightBase, month_start

RULES = {r.id: r for r in registry.all_rules()}


def run(rule_id, ctx):
    rule = RULES[rule_id]
    with ctx.declared(rule.needs):
        return rule.evaluate(ctx)


def bare(**kw):
    """A context with the usual history but only the pieces a test names."""
    ctx = sampling.scenario(**kw)
    return ctx


def empty_facts(ctx, history=0):
    first = ctx.cycle_of(ctx.keys_back(5)[-1])[0]
    first = min(first, ctx.cycle_of(date(ctx.key.year, 1, 1))[0])
    ctx._cache['facts'] = Facts(first=first, last=ctx.cycle[1])
    ctx._cache.pop('summaries', None)
    return ctx._cache['facts']


class PaceVsUsualTests(SimpleTestCase):
    rule = 'cashflow.pace_vs_usual'

    def test_happy_path_numbers(self):
        [insight] = run(self.rule, bare())
        self.assertEqual(insight.numbers['pct'], 72)
        self.assertEqual(insight.numbers['usual'], '44000.00')
        self.assertEqual(insight.dedupe_key, '2026-10')
        self.assertEqual(insight.valid_until, date(2026, 10, 31))

    def test_nothing_without_an_earlier_cycle(self):
        ctx = bare()
        facts = empty_facts(ctx)
        facts.expense_cats[facts.index(ctx.cycle[0])] = {'dining': D('30000')}
        self.assertEqual(run(self.rule, ctx), [])           # one cycle of data only: we need one more to compare

    def test_small_spending_is_left_alone(self):
        ctx = bare()
        facts = empty_facts(ctx)
        first = ctx.cycle_of(ctx.keys_back(1)[0])[0]
        facts.expense_cats[facts.index(first)] = {'dining': D('500')}
        facts.expense_cats[facts.index(ctx.cycle[0])] = {'dining': D('900')}
        self.assertEqual(run(self.rule, ctx), [])           # under the 1,000 floor, whatever the ratio

    def test_exactly_at_the_threshold_is_not_a_mention(self):
        ctx = bare(today=date(2026, 10, 31))                # last day: projected == spent
        facts = empty_facts(ctx)
        prior = ctx.cycle_of(ctx.keys_back(1)[0])[0]
        facts.expense_cats[facts.index(prior)] = {'dining': D('40000')}
        facts.expense_cats[facts.index(ctx.cycle[0])] = {'dining': D('50000')}    # exactly 1.25 x
        self.assertEqual(run(self.rule, ctx), [])
        facts.expense_cats[facts.index(ctx.cycle[0])] = {'dining': D('50001')}
        ctx._cache.pop('summaries')
        self.assertEqual(len(run(self.rule, ctx)), 1)

    def test_a_salary_cycle_that_crosses_a_month_boundary(self):
        ctx = sampling.scenario(salary_day=25, today=date(2026, 10, 30))
        self.assertEqual(ctx.cycle, (date(2026, 10, 25), date(2026, 11, 24)))
        self.assertEqual((ctx.key, ctx.elapsed_days, ctx.cycle_days), (date(2026, 11, 1), 6, 31))
        self.assertTrue(ctx.is_cycle)

    def test_amounts_follow_the_display_currency(self):
        ctx = bare(currency='$')
        [insight] = run(self.rule, ctx)
        self.assertIn('$44k', insight.message)
        self.assertNotIn('₹', insight.message + ''.join(e.value for e in insight.evidence))


class SavingStreakAndMomentumTests(SimpleTestCase):
    def test_streak_counts_this_cycle_and_up_to_five_before(self):
        [insight] = run('cashflow.saving_streak', bare())
        self.assertEqual(insight.numbers['streak'], 6)

    def test_streak_stops_at_the_first_month_that_did_not_save(self):
        ctx = bare()
        facts = ctx.facts
        two_back = ctx.cycle_of(ctx.keys_back(2)[1])[0]
        facts.income[facts.index(two_back)] = D('0')
        [insight] = run('cashflow.saving_streak', ctx)
        self.assertEqual(insight.numbers['streak'], 2)

    def test_no_streak_of_one_and_none_in_a_month_in_the_red(self):
        ctx = bare()
        facts = ctx.facts
        facts.income[facts.index(ctx.cycle[0])] = D('0')
        self.assertEqual(run('cashflow.saving_streak', ctx), [])

    def test_momentum_needs_savings_above_a_positive_previous_cycle(self):
        [insight] = run('cashflow.savings_momentum', bare())
        self.assertEqual(insight.numbers['pct'], 60)
        ctx = bare()
        prev = ctx.cycle_of(ctx.keys_back(1)[0])[0]
        ctx.facts.income[ctx.facts.index(prev)] = D('30000')       # last cycle was a shortfall
        self.assertEqual(run('cashflow.savings_momentum', ctx), [])

    def test_momentum_wording_follows_months_or_cycles(self):
        month = run('cashflow.savings_momentum', bare())[0].message
        cycle = run('cashflow.savings_momentum', bare(salary_day=5))[0].message
        self.assertIn('last month', month)
        self.assertIn('previous cycle', cycle)


class YearPaceTests(SimpleTestCase):
    def test_projects_the_year_to_date_average(self):
        [insight] = run('cashflow.year_pace', bare())
        self.assertEqual(insight.numbers['kept'], '178000.00')
        self.assertEqual(insight.numbers['projected'], '213600.00')      # 178000 / 10 * 12

    def test_not_in_the_first_month_of_the_year(self):
        self.assertEqual(run('cashflow.year_pace', bare(today=date(2026, 1, 20))), [])

    def test_not_when_the_year_so_far_has_saved_nothing(self):
        ctx = bare()
        for i in range(len(ctx.facts.income)):
            ctx.facts.income[i] = D('0')
        self.assertEqual(run('cashflow.year_pace', ctx), [])


class CategoryAboveUsualTests(SimpleTestCase):
    rule = 'expenses.category_above_usual'

    def test_happy_path(self):
        [insight] = run(self.rule, bare())
        self.assertEqual(insight.numbers, {'category': 'dining', 'pct': 144})
        self.assertEqual(insight.evidence[0].link.split('?')[0], '/transactions/')
        self.assertIn('category=Dining', insight.evidence[0].link)

    def test_nothing_with_no_spending(self):
        ctx = bare()
        empty_facts(ctx)
        self.assertEqual(run(self.rule, ctx), [])

    def test_nothing_when_the_category_has_no_earlier_months(self):
        ctx = bare()
        facts = empty_facts(ctx)
        facts.expense_cats[facts.index(ctx.cycle[0])] = {'dining': D('9000')}
        self.assertEqual(run(self.rule, ctx), [])

    def test_ten_percent_above_is_the_line(self):
        ctx = bare()
        facts = empty_facts(ctx)
        for key in ctx.keys_back(3):
            facts.expense_cats[facts.index(ctx.cycle_of(key)[0])] = {'dining': D('10000')}
        facts.expense_cats[facts.index(ctx.cycle[0])] = {'dining': D('11000')}
        self.assertEqual(run(self.rule, ctx), [])
        facts.expense_cats[facts.index(ctx.cycle[0])] = {'dining': D('11001')}
        ctx._cache.pop('summaries')
        self.assertEqual(len(run(self.rule, ctx)), 1)

    def test_a_long_category_name_still_fits_the_title(self):
        ctx = bare()
        facts = ctx.facts
        facts.names['dining'] = 'Eating out and takeaway and entertainment for the family'
        [insight] = run(self.rule, ctx)
        self.assertLessEqual(len(insight.title.split()), 8)


def budget_ctx(rows, **kw):
    return sampling.scenario(budgets=rows, **kw)


class BudgetRuleTests(SimpleTestCase):
    def test_passed_names_the_largest_and_counts_the_others_that_are_fine(self):
        [insight] = run('budgets.limit_passed', budget_ctx([('Groceries', 10000, 12000), ('Dining', 30000, 22000),
                                                            ('Travel', 8000, 100)]))
        self.assertEqual(insight.numbers['categories'], ['groceries'])
        self.assertIn('2 other categories', insight.detail)
        self.assertEqual(insight.dedupe_key, '2026-10')

    def test_several_passed_limits_speak_once(self):
        [insight] = run('budgets.limit_passed', budget_ctx([('A', 100, 300), ('B', 100, 200), ('C', 100, 50)]))
        self.assertEqual(insight.numbers['categories'], ['a', 'b'])
        self.assertIn('2 categories', insight.title)
        self.assertIn('largest being A', insight.message)

    def test_exactly_at_the_limit_is_nearing_not_passed(self):
        rows = [('Dining', 1000, 1000)]
        self.assertEqual(run('budgets.limit_passed', budget_ctx(rows)), [])
        self.assertEqual(len(run('budgets.limit_nearing', budget_ctx(rows))), 1)

    def test_nearing_is_eighty_five_percent_and_never_with_a_passed_limit(self):
        self.assertEqual(run('budgets.limit_nearing', budget_ctx([('Dining', 1000, 849)])), [])
        self.assertEqual(len(run('budgets.limit_nearing', budget_ctx([('Dining', 1000, 850)]))), 1)
        self.assertEqual(run('budgets.limit_nearing', budget_ctx([('Dining', 1000, 900), ('Rent', 100, 500)])), [])

    def test_a_zero_or_missing_budget_never_speaks(self):
        rows = [('Dining', 0, 5000), ('Travel', 1, 0)]
        ctx = budget_ctx(rows)
        ctx._cache['budgets']['categories'][1]['limit'] = None
        ctx._cache['budgets']['categories'][1]['percent'] = None
        for rule in ('budgets.limit_passed', 'budgets.limit_nearing', 'budgets.category_runout'):
            self.assertEqual(run(rule, ctx), [], rule)

    def test_only_the_current_cycle_is_read(self):
        ctx = budget_ctx([('Dining', 100, 500)])
        ctx._cache['budgets']['phase'] = 'past'
        self.assertEqual(run('budgets.limit_passed', ctx), [])

    def test_runout_gives_the_date_and_leaves_out_categories_already_over(self):
        [insight] = run('budgets.category_runout', budget_ctx([('Transport', 5000, 4000), ('Dining', 100, 500)]))
        self.assertEqual(insight.numbers['categories'], ['transport'])
        self.assertIn('27 Oct', insight.message)         # 22 days gone, 4000 of 5000 used: the limit is reached on day 28 (the 27th)

    def test_runout_is_quiet_for_a_category_on_a_slow_pace(self):
        self.assertEqual(run('budgets.category_runout', budget_ctx([('Transport', 5000, 1000)])), [])

    def test_evidence_links_to_the_cycles_transactions(self):
        [insight] = run('budgets.limit_passed', budget_ctx([('Groceries', 10000, 12000)]))
        link = insight.evidence[0].link
        self.assertIn('start_date=2026-10-01', link)
        self.assertIn('end_date=2026-10-31', link)
        self.assertIn('category=Groceries', link)


class DepositAndBillsTests(SimpleTestCase):
    def deposit(self, **kw):
        base = dict(uuid='11111111-1111-1111-1111-111111111111', name='Bank FD', currency='₹',
                    balance=D('210000'), deposit_maturity_date=TODAY + timedelta(days=10))
        base.update(kw)
        return SimpleNamespace(**base)

    def test_one_insight_per_deposit_in_its_own_currency(self):
        ctx = bare(maturing=[self.deposit(), self.deposit(uuid='22222222-2222-2222-2222-222222222222', name='US Bond',
                                                           currency='$', balance=D('2500'))])
        out = run('accounts.deposit_maturing', ctx)
        self.assertEqual({i.numbers['name'] for i in out}, {'bank fd', 'us bond'})
        self.assertIn('$2.5k', [i for i in out if 'US Bond' in i.message][0].message)

    def test_nothing_without_a_maturing_deposit(self):
        self.assertEqual(run('accounts.deposit_maturing', bare(maturing=[])), [])

    def test_bills_one_or_many(self):
        one = bare(bills=[SimpleNamespace(description='Netflix', amount=D('499'), base_amount=D('499'),
                                          next_due_date=TODAY + timedelta(days=1))])
        [insight] = run('recurring.bills_soon', one)
        self.assertIn('Netflix', insight.message)
        [many] = run('recurring.bills_soon', bare())
        self.assertIn('2 payments', many.message)
        self.assertEqual(run('recurring.bills_soon', bare(bills=[])), [])

    def test_a_long_bill_description_is_trimmed(self):
        bill = SimpleNamespace(description='x' * 200, amount=D('1'), base_amount=None,
                               next_due_date=TODAY + timedelta(days=1))
        [insight] = run('recurring.bills_soon', bare(bills=[bill]))
        self.assertLess(len(insight.evidence[0].label), 45)


class FormatTests(SimpleTestCase):
    def test_rupee_amounts_use_thousands_lakhs_and_crores(self):
        fmt = Fmt('₹')
        self.assertEqual([fmt.approx(v) for v in (842, 9200, 140000, 25000000, 99999)],
                         ['₹842', '₹9.2k', '₹1.4L', '₹2.5Cr', '₹1L'])        # 99,999 rounds up into the next unit
        self.assertEqual(fmt.approx(-9200), '−₹9.2k')

    def test_other_currencies_use_thousands_millions_and_billions(self):
        fmt = Fmt('$')
        self.assertEqual([fmt.approx(v) for v in (250000, 2500000, 3000000000)], ['$250k', '$2.5M', '$3B'])

    def test_money_is_exact_with_lakh_grouping(self):
        self.assertEqual(Fmt('₹').money(123456), '₹1,23,456')
        self.assertEqual(Fmt('$').money(123456), '$123,456')

    def test_hindi_and_marathi_digits_and_words(self):
        with translation.override('hi'):
            self.assertEqual(Fmt('₹').approx(140000), '₹१.४ लाख')
            self.assertEqual(Fmt('₹').percent(40), '४०%')
        with translation.override('mr'):
            self.assertEqual(Fmt('₹').approx(9200), '₹९.२ हजार')

    def test_titles_get_a_short_name(self):
        self.assertEqual(Fmt.short('Groceries'), 'Groceries')
        self.assertTrue(Fmt.short('one two three four five').endswith('…'))
        self.assertLessEqual(len(Fmt.short('x' * 80)), 28)


class DeclaredNeedsTests(SimpleTestCase):
    def test_reading_a_loader_the_rule_did_not_declare_fails(self):
        ctx = bare()
        with ctx.declared({'budgets'}):
            self.assertTrue(ctx.budgets)
            with self.assertRaises(UndeclaredNeed):
                self.assertTrue(ctx.facts)

    def test_every_rule_runs_with_only_what_it_declared(self):
        for rule in registry.all_rules():
            ctx = rule.sample('en')
            with ctx.declared(rule.needs):
                rule.evaluate(ctx)          # would raise UndeclaredNeed otherwise


class RealLoaderTests(InsightBase):
    """The same rules reading the real database through the context's loaders."""

    def ctx(self):
        return InsightContext(self.user, today=TODAY, profile=self.profile)

    def test_the_scenario_in_the_database_matches_the_synthetic_one(self):
        self.life()
        ctx = self.ctx()
        for rule_id, field, expected in [
            ('cashflow.pace_vs_usual', 'pct', 72), ('cashflow.saving_streak', 'streak', 6),
            ('cashflow.savings_momentum', 'pct', 60), ('expenses.category_above_usual', 'pct', 144),
        ]:
            [insight] = run(rule_id, ctx)
            self.assertEqual(insight.numbers[field], expected, rule_id)

    def test_a_user_with_a_single_month_of_data_hears_nothing(self):
        self.income(60000, month_start(TODAY, 0))
        self.spend(5000, month_start(TODAY, 0))
        ctx = self.ctx()
        self.assertEqual(ctx.history_cycles(), 0)
        for rule_id in ('cashflow.pace_vs_usual', 'cashflow.saving_streak', 'cashflow.savings_momentum',
                        'expenses.category_above_usual'):
            self.assertEqual(run(rule_id, ctx), [], rule_id)

    def test_a_maturing_deposit_is_found_and_a_closed_or_far_one_is_not(self):
        def fd(name, days, active=True):
            return Account.objects.create(user=self.user, name=name, account_type='FIXED_DEPOSIT', currency='₹',
                                          balance=D('50000'), is_active=active,
                                          deposit_maturity_date=TODAY + timedelta(days=days))
        fd('Soon', 10)
        fd('Far', 90)
        fd('Closed', 5, active=False)         # a deleted (deactivated) account says nothing
        out = run('accounts.deposit_maturing', self.ctx())
        self.assertEqual([i.numbers['name'] for i in out], ['soon'])

    def test_bills_leave_out_income_and_beyond_a_week(self):
        def rec(description, days, kind='EXPENSE'):
            row = RecurringTransaction.objects.create(
                user=self.user, transaction_type=kind, amount=D('100'), currency='₹', description=description,
                category='Food', frequency='MONTHLY', start_date=TODAY - timedelta(days=20),
                last_processed_date=TODAY - timedelta(days=20), account=self.cash, is_active=True)
            RecurringTransaction.objects.filter(pk=row.pk).update(next_due_date=TODAY + timedelta(days=days))
            return row
        rec('Rent', 3)
        rec('Salary', 2, 'INCOME')
        rec('Later', 12)
        [insight] = run('recurring.bills_soon', self.ctx())
        self.assertEqual(insight.numbers['items'], [['Rent', (TODAY + timedelta(days=3)).isoformat()]])

    def test_budget_rules_read_the_budget_page(self):
        self.category('Groceries', 10000)
        self.spend(12000, month_start(TODAY, 0), 'Groceries')
        [insight] = run('budgets.limit_passed', self.ctx())
        self.assertEqual(insight.numbers['categories'], ['groceries'])
