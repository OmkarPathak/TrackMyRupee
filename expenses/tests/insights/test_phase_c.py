"""Phase C: recurring payments, loans, cards and accounts, and the lookahead before payday."""
from datetime import date, timedelta
from types import SimpleNamespace
from unittest import mock

from django.db import connection
from django.test import SimpleTestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from expenses.analytics.facts import LoanPaid, load_loan_paid
from expenses.insights import engine, prefs, registry, sampling
from expenses.insights.context import InsightContext
from expenses.insights.sampling import TODAY as SAMPLE_TODAY
from expenses.insights.sampling import _loan, _recurring, _row
from expenses.insights.types import Module, Plan, Surface
from expenses.models import (
    Account,
    AccountReconciliation,
    CapitalEvent,
    InsightState,
    Loan,
    LoanInterestRate,
    LoanRepayment,
    RecurringTransaction,
    UserProfile,
)
from expenses.services import LoanService

from .base import ENGINE_ON, TODAY, D, InsightBase

RULES = {r.id: r for r in registry.all_rules()}


def run(rule_id, ctx):
    rule = RULES[rule_id]
    with ctx.declared(rule.needs):
        return rule.evaluate(ctx)


class RecurringRuleTests(SimpleTestCase):
    def test_fixed_costs_are_the_monthly_equivalent_and_a_share_of_income(self):
        [found] = run('recurring.fixed_costs', sampling.scenario())
        self.assertEqual(found.numbers, {'monthly': '38699.00', 'items': 3, 'share': 64})
        self.assertIn('about ₹38.7k a month', found.message)

    def test_a_yearly_payment_counts_a_twelfth(self):
        items = [_recurring('Rent', 'EXPENSE', 20000), _recurring('Insurance', 'INSURANCE_PREMIUM', 12000, 'YEARLY')]
        [found] = run('recurring.fixed_costs', sampling.scenario(recurring_all=items))
        self.assertEqual(found.numbers['monthly'], '21000.00')

    def test_income_is_not_a_cost_and_one_payment_is_not_a_pattern(self):
        items = [_recurring('Rent', 'EXPENSE', 20000), _recurring('Salary', 'INCOME', 60000)]
        self.assertEqual(run('recurring.fixed_costs', sampling.scenario(recurring_all=items)), [])

    def test_no_income_history_says_nothing(self):
        ctx = sampling.scenario()
        for i in range(len(ctx.facts.income)):
            ctx.facts.income[i] = D('0')
        self.assertEqual(run('recurring.fixed_costs', ctx), [])

    def postings(self, *pairs):
        return sampling.scenario(recurring_all=[_recurring('Netflix', 'EXPENSE', 649)], postings={
            'Netflix (Recurring)': [(SAMPLE_TODAY - timedelta(days=d), D(a)) for d, a in pairs]})

    def test_price_change_up_and_down(self):
        [up] = run('recurring.price_change', self.postings((35, 499), (4, 649)))
        self.assertEqual(up.numbers, {'name': 'netflix', 'direction': 'up'})
        self.assertIn('went up from ₹499 to ₹649', up.message)
        [down] = run('recurring.price_change', self.postings((35, 649), (4, 499)))
        self.assertIn('came down', down.message)
        self.assertEqual(down.tone.value, 'positive')

    def test_a_small_wobble_an_old_change_and_a_single_posting_are_left_alone(self):
        self.assertEqual(run('recurring.price_change', self.postings((35, 499), (4, 510))), [])         # 2%
        self.assertEqual(run('recurring.price_change', self.postings((95, 499), (60, 649))), [])        # 60 days ago
        self.assertEqual(run('recurring.price_change', self.postings((4, 649))), [])

    def test_two_schedules_with_one_name_are_one_insight(self):
        ctx = self.postings((35, 499), (4, 649))
        ctx._cache['recurring_all'].append(_recurring('Netflix', 'EXPENSE', 649))
        self.assertEqual(len(run('recurring.price_change', ctx)), 1)

    def test_price_change_key_names_the_schedule_and_the_day_not_the_amounts(self):
        [found] = run('recurring.price_change', self.postings((35, 499), (4, 649)))
        self.assertRegex(found.dedupe_key, r'^price:[0-9a-f-]{36}:\d{4}-\d{2}-\d{2}$')

    def gym(self, *days, description='Gym fee', amount=1200):
        return sampling.scenario(rows=[_row(500 + i, SAMPLE_TODAY - timedelta(days=d), 'Health', amount,
                                            description=description) for i, d in enumerate(days)])

    def test_the_same_entry_three_times_over_two_months_looks_recurring(self):
        [found] = run('recurring.looks_recurring', self.gym(70, 40, 10))
        self.assertEqual(found.numbers, {'what': 'gym fee', 'n': 3})
        self.assertRegex(found.dedupe_key, r'^repeat:[0-9a-f]{12}:\d{4}-\d{2}$')      # a digest: no description in a key
        self.assertEqual(found.surfaces, frozenset({Surface.MODULE}))                  # the bell already has this nudge

    def test_not_when_it_is_too_few_one_month_or_already_recurring(self):
        self.assertEqual(run('recurring.looks_recurring', self.gym(40, 10)), [])
        self.assertEqual(run('recurring.looks_recurring', self.gym(9, 6, 3)), [])                      # all in October
        known = sampling.scenario(rows=self.gym(70, 40, 10)._cache['rows'], recurring_all=[_recurring('Gym fee', 'EXPENSE', 1200)])
        self.assertEqual(run('recurring.looks_recurring', known), [])
        self.assertEqual(run('recurring.looks_recurring', self.gym(70, 40, 10, description='Gym fee (Recurring)')), [])
        blank = self.gym(70, 40, 10)
        blank._cache['rows'] = [row._replace(description='') for row in blank._cache['rows']]
        self.assertEqual(run('recurring.looks_recurring', blank), [])


class LoanRuleTests(SimpleTestCase):
    def test_interest_paid_this_year_in_the_right_year_for_the_currency(self):
        [rupees] = run('loans.interest_paid_year', sampling.loans_sample())
        self.assertIn('financial year', rupees.message)
        self.assertTrue(rupees.sensitive)
        ctx = sampling.scenario(currency='$', loans=[_loan('Home Loan', 5000000, 4200000, '8.5')])
        [other] = run('loans.interest_paid_year', ctx)
        self.assertNotIn('financial year', other.message)
        self.assertEqual(ctx.financial_year, (date(2026, 1, 1), date(2026, 12, 31)))
        self.assertEqual(sampling.loans_sample().financial_year, (date(2026, 4, 1), date(2027, 3, 31)))

    def test_the_financial_year_turns_over_in_april(self):
        self.assertEqual(sampling.scenario(today=date(2027, 3, 31)).financial_year, (date(2026, 4, 1), date(2027, 3, 31)))
        self.assertEqual(sampling.scenario(today=date(2027, 4, 1)).financial_year, (date(2027, 4, 1), date(2028, 3, 31)))

    def test_nothing_without_repayments_this_year(self):
        ctx = sampling.loans_sample()
        ctx._cache['loan_paid'] = LoanPaid(D('0'), D('0'), 0)
        self.assertEqual(run('loans.interest_paid_year', ctx), [])

    def test_one_extra_emi_uses_the_loan_services_estimate(self):
        ctx = sampling.loans_sample()
        [found] = run('loans.extra_emi_effect', ctx)
        expected = LoanService.calculate_extra_emi_savings(ctx.loans[0])
        self.assertEqual(found.numbers['months_saved'], expected['months_saved'])
        self.assertIn('years earlier', found.message)         # 30 months or more reads better in years
        self.assertTrue(found.sensitive)
        self.assertEqual(found.surfaces & {Surface.PUSH, Surface.BELL}, frozenset())

    def test_extra_emi_skips_loans_that_are_not_amortising_or_not_started_paying(self):
        loan = _loan('Bridge', 500000, 500000, '9')
        self.assertEqual(run('loans.extra_emi_effect', sampling.scenario(loans=[loan])), [])          # nothing paid yet
        interest_only = _loan('Interest', 500000, 400000, '9')
        interest_only.repayment_type = 'INTEREST_ONLY'
        self.assertEqual(run('loans.extra_emi_effect', sampling.scenario(loans=[interest_only])), [])

    def test_months_read_as_months_under_a_year_and_two_months_is_not_worth_a_mention(self):
        [found] = run('loans.extra_emi_effect', sampling.scenario(loans=[_loan('Car Loan', 800000, 600000, '10', months=60, started=12)]))
        self.assertEqual(found.numbers['months_saved'], 3)
        self.assertIn('about 3 months earlier', found.message)
        short = sampling.scenario(loans=[_loan('Car Loan', 300000, 250000, '11', months=36, started=6)])      # saves 2 months
        self.assertEqual(run('loans.extra_emi_effect', short), [])

    def test_a_gap_of_a_point_or_more_between_two_loans(self):
        [found] = run('loans.rate_gap', sampling.loans_sample())
        self.assertEqual(found.numbers, {'high': 'car loan', 'low': 'home loan'})
        self.assertIn('9.9%', found.message)
        self.assertIn('1.4 points', found.message)
        self.assertEqual(RULES['loans.rate_gap'].min_plan, Plan.PRO)

    def test_no_gap_to_speak_of_or_only_one_loan(self):
        close = sampling.scenario(loans=[_loan('A', 500000, 400000, '8.5'), _loan('B', 500000, 400000, '9.4')])
        self.assertEqual(run('loans.rate_gap', close), [])
        self.assertEqual(run('loans.rate_gap', sampling.scenario(loans=[_loan('A', 500000, 400000, '8.5')])), [])

    def test_a_share_of_the_principal_just_repaid(self):
        [found] = run('loans.milestone_ahead', sampling.loans_sample())
        self.assertEqual(found.numbers, {'loan': 'car loan', 'kind': 'pct', 'milestone': 50})
        self.assertEqual(found.dedupe_key.split(':')[0], 'pct50')

    def test_the_final_year_comes_before_a_share(self):
        loan = _loan('Car Loan', 800000, 100000, '9', months=40, started=30)
        [found] = run('loans.milestone_ahead', sampling.scenario(loans=[loan]))
        self.assertEqual(found.numbers['kind'], 'final-year')
        self.assertIn('months left', found.message)

    def test_a_recorded_rate_change_comes_first(self):
        change = SimpleNamespace(effective_date=SAMPLE_TODAY + timedelta(days=12), interest_rate=D('9.25'))
        loan = _loan('Home Loan', 5000000, 4200000, '8.5', extra_rates=(change,))
        [found] = run('loans.milestone_ahead', sampling.scenario(loans=[loan]))
        self.assertEqual(found.numbers['kind'], 'rate')
        self.assertIn('from 8.5% to', found.message)

    def test_a_loan_between_milestones_says_nothing(self):
        loan = _loan('Home Loan', 5000000, 3000000, '8.5')            # 40% repaid (between 25 and 50), 200 months left
        self.assertEqual(run('loans.milestone_ahead', sampling.scenario(loans=[loan])), [])

    def test_every_loan_insight_about_debt_is_sensitive(self):
        for rule_id in ('loans.interest_paid_year', 'loans.extra_emi_effect', 'loans.rate_gap'):
            for insight in run(rule_id, sampling.loans_sample()):
                self.assertTrue(insight.sensitive, rule_id)
                self.assertIsNone(insight.share_text)
                self.assertNotIn(Surface.PUSH, insight.surfaces)


class CardRuleTests(SimpleTestCase):
    def card(self, owed, billing_in=6, limit='80000', today=SAMPLE_TODAY):
        from expenses.models import Account
        day = (today + timedelta(days=billing_in)).day
        card = Account(id=7, name='Travel Card', account_type='CREDIT_CARD', currency='₹',
                       credit_limit=D(limit) if limit else None, credit_card_billing_day=day)
        return sampling.scenario(today=today, cards=[(card, D(owed))])

    def test_billing_soon_with_the_share_of_the_limit(self):
        [found] = run('accounts.card_billing_soon', self.card('50000'))
        self.assertIn('63% of its limit', found.message)
        self.assertTrue(found.sensitive)
        self.assertEqual(found.dedupe_key.split(':')[1], '2026-10-28')

    def test_without_a_limit_the_sentence_leaves_it_out(self):
        [found] = run('accounts.card_billing_soon', self.card('50000', limit=None))
        self.assertNotIn('limit', found.message)

    def test_not_when_nothing_is_owed_or_the_date_is_far(self):
        self.assertEqual(run('accounts.card_billing_soon', self.card('0')), [])
        self.assertEqual(run('accounts.card_billing_soon', self.card('50000', billing_in=11)), [])

    def test_the_billing_day_rolls_into_the_next_month_and_short_months(self):
        december = self.card('1000', billing_in=5, today=date(2026, 12, 28))
        [found] = run('accounts.card_billing_soon', december)
        self.assertEqual(found.dedupe_key.split(':')[1], '2027-01-02')
        card = Account(id=7, name='C', account_type='CREDIT_CARD', currency='₹', credit_card_billing_day=31)
        ctx = sampling.scenario(today=date(2027, 2, 25), cards=[(card, D('900'))])
        [feb] = run('accounts.card_billing_soon', ctx)
        self.assertEqual(feb.dedupe_key.split(':')[1], '2027-02-28')

    def test_utilisation_is_half_the_limit_or_more(self):
        [found] = run('accounts.card_utilisation', self.card('50000'))
        self.assertEqual(found.numbers['pct'], 62)
        self.assertEqual(run('accounts.card_utilisation', self.card('39999')), [])
        self.assertEqual(len(run('accounts.card_utilisation', self.card('40000'))), 1)
        self.assertEqual(run('accounts.card_utilisation', self.card('50000', limit=None)), [])

    def test_only_the_busiest_card_is_mentioned(self):
        ctx = self.card('50000')
        other = Account(id=8, name='Spare Card', account_type='CREDIT_CARD', currency='₹', credit_limit=D('10000'),
                        credit_card_billing_day=1)
        ctx._cache['cards'].append((other, D('9000')))
        [found] = run('accounts.card_utilisation', ctx)
        self.assertEqual(found.numbers['card'], 'spare card')

    def test_a_card_in_another_currency_is_shown_in_its_own(self):
        from expenses.models import Account
        card = Account(id=9, name='US Card', account_type='CREDIT_CARD', currency='$', credit_limit=D('5000'),
                       credit_card_billing_day=(SAMPLE_TODAY + timedelta(days=3)).day)
        [found] = run('accounts.card_utilisation', sampling.scenario(cards=[(card, D('3000'))]))
        self.assertIn('$5k', found.message)
        self.assertNotIn('₹', found.message)


class CashRuleTests(SimpleTestCase):
    def test_cash_well_above_the_persons_own_target(self):
        [found] = run('accounts.idle_cash', sampling.idle_cash_sample())
        self.assertEqual(found.numbers, {'months': 10, 'target': 6})
        self.assertIn('6-month target', found.message)

    def test_below_one_and_a_half_times_the_target_says_nothing(self):
        ctx = sampling.scenario(cash=SimpleNamespace(idle=D('390000')))         # 8.9 months against 6
        self.assertEqual(run('accounts.idle_cash', ctx), [])

    def test_a_smaller_target_of_their_own_changes_what_is_worth_saying(self):
        ctx = sampling.scenario(cash=SimpleNamespace(idle=D('200000')))         # 4.5 months
        self.assertEqual(run('accounts.idle_cash', ctx), [])
        ctx.profile.insights_prefs = {'emergency_months': 3}
        [found] = run('accounts.idle_cash', ctx)
        self.assertEqual(found.numbers['target'], 3)

    def test_no_cash_or_no_history_says_nothing(self):
        self.assertEqual(run('accounts.idle_cash', sampling.scenario(cash=SimpleNamespace(idle=D('0')))), [])
        ctx = sampling.idle_cash_sample()
        for i in range(len(ctx.facts.expense_cats)):
            ctx.facts.expense_cats[i] = None
        ctx._cache.pop('summaries', None)
        self.assertEqual(run('accounts.idle_cash', ctx), [])

    def reconcile(self, last_days_ago, account_type='SAVINGS_ACCOUNT'):
        account = Account(id=5, name='Savings Account', account_type=account_type, currency='₹')
        return sampling.scenario(accounts_all=[account], reconciliations={5: SAMPLE_TODAY - timedelta(days=last_days_ago)})

    def test_a_lapsed_reconciliation(self):
        [found] = run('accounts.not_reconciled', self.reconcile(74))
        self.assertIn('74 days ago', found.message)
        self.assertEqual(run('accounts.not_reconciled', self.reconcile(59)), [])
        self.assertEqual(len(run('accounts.not_reconciled', self.reconcile(60))), 1)

    def test_never_reconciled_or_not_reconcilable_says_nothing(self):
        account = Account(id=5, name='Savings Account', account_type='SAVINGS_ACCOUNT', currency='₹')
        self.assertEqual(run('accounts.not_reconciled', sampling.scenario(accounts_all=[account])), [])
        self.assertEqual(run('accounts.not_reconciled', self.reconcile(100, account_type='FIXED_DEPOSIT')), [])

    def test_the_oldest_lapse_comes_first(self):
        ctx = self.reconcile(70)
        ctx._cache['accounts_all'].append(Account(id=6, name='Older', account_type='SAVINGS_ACCOUNT', currency='₹'))
        ctx._cache['reconciliations'][6] = SAMPLE_TODAY - timedelta(days=200)
        [found] = run('accounts.not_reconciled', ctx)
        self.assertEqual(found.numbers['name'], 'older')


class LowPointTests(SimpleTestCase):
    rule = 'cashflow.low_point_before_salary'

    def test_a_dip_before_the_salary_arrives(self):
        [found] = run(self.rule, sampling.low_point_sample())
        self.assertEqual(found.numbers['short'], False)
        self.assertFalse(found.sensitive)
        self.assertEqual(found.dedupe_key, '2026-11-05')
        self.assertIn('dip to about', found.message)
        self.assertIn('salary on 5 Nov', found.message)

    def test_a_balance_that_could_run_short_is_sensitive_and_worded_more_gently(self):
        ctx = sampling.low_point_sample()
        ctx._cache['cash'] = SimpleNamespace(idle=D('12000'))
        [found] = run(self.rule, ctx)
        self.assertTrue(found.sensitive)
        self.assertIn('run a little short', found.message)
        self.assertNotIn('₹', found.message.split('around')[0])      # no figure for the shortfall itself
        self.assertEqual(found.surfaces & {Surface.PUSH, Surface.BELL}, frozenset())
        self.assertIsNone(found.share_text)

    def test_plenty_of_cash_says_nothing(self):
        ctx = sampling.low_point_sample()
        ctx._cache['cash'] = SimpleNamespace(idle=D('200000'))
        self.assertEqual(run(self.rule, ctx), [])

    def test_no_known_payday_or_one_tomorrow_says_nothing(self):
        ctx = sampling.low_point_sample()
        ctx._cache['pending'] = {d: [i for i in items if not i['is_salary']] for d, items in ctx._cache['pending'].items()}
        self.assertEqual(run(self.rule, ctx), [])
        soon = sampling.low_point_sample()
        payday = SAMPLE_TODAY + timedelta(days=1)
        soon._cache['pending'] = {payday: [{'description': 'x', 'amount': D(1), 'kind': 'in', 'type': 'INCOME',
                                            'is_salary': True, 'date': payday}]}
        self.assertEqual(run(self.rule, soon), [])

    def test_the_persons_own_salary_day_is_used_when_nothing_is_scheduled(self):
        ctx = sampling.low_point_sample()
        ctx.salary_day = 4
        ctx._cache['pending'] = {k: [i for i in v if not i['is_salary']] for k, v in ctx._cache['pending'].items()}
        [found] = run(self.rule, ctx)
        self.assertEqual(found.dedupe_key, '2026-11-04')

    def test_no_cash_held_says_nothing(self):
        ctx = sampling.low_point_sample()
        ctx._cache['cash'] = SimpleNamespace(idle=D('0'))
        self.assertEqual(run(self.rule, ctx), [])

    def test_a_scheduled_income_before_payday_lifts_the_balance(self):
        ctx = sampling.low_point_sample()
        day = SAMPLE_TODAY + timedelta(days=7)
        ctx._cache['pending'][day] = [{'description': 'x', 'amount': D('50000'), 'kind': 'in', 'type': 'INCOME',
                                       'is_salary': False, 'date': day}]
        self.assertEqual(run(self.rule, ctx), [])

    def test_it_is_nightly_plus_and_has_a_figure_free_preview(self):
        rule = RULES[self.rule]
        self.assertEqual((rule.cost, int(rule.min_plan)), ('heavy', 1))
        self.assertNotRegex(str(rule.preview()), r'\d')


class FactsAdditionsTests(InsightBase):
    def test_loan_interest_and_principal_paid_between_two_dates(self):
        loan = Loan.objects.create(user=self.user, name='L', loan_type='PERSONAL', initial_principal=D('100000'),
                                   duration_months=12, start_date=TODAY - timedelta(days=90), currency='₹')
        for day, principal, interest in ((TODAY - timedelta(days=40), '9000', '1000'), (TODAY - timedelta(days=10), '9100', '900')):
            LoanRepayment.objects.create(loan=loan, amount=D(principal) + D(interest), principal_portion=D(principal),
                                         interest_portion=D(interest), date=day, from_account=self.cash)
        with self.assertNumQueries(1):
            paid = load_loan_paid(self.user, TODAY - timedelta(days=60), TODAY)
        self.assertEqual((paid.interest, paid.principal, paid.repayments), (D('1900'), D('18100'), 2))
        self.assertEqual(load_loan_paid(self.user, TODAY - timedelta(days=20), TODAY).repayments, 1)
        self.assertEqual(load_loan_paid(self.user, TODAY + timedelta(days=1), TODAY + timedelta(days=9)).repayments, 0)


class RealLoaderTests(InsightBase):
    def ctx(self):
        return InsightContext(self.user, today=TODAY, profile=self.profile)

    def loan(self, **kw):
        defaults = dict(user=self.user, name='Home Loan', loan_type='HOME', initial_principal=D('1000000'),
                        duration_months=120, start_date=TODAY - timedelta(days=400), currency='₹')
        defaults.update(kw)
        loan = Loan.objects.create(**defaults)
        LoanInterestRate.objects.create(loan=loan, interest_rate=D('8.5'), effective_date=loan.start_date)
        return loan

    def test_the_inventory_says_what_the_person_has_in_one_query(self):
        with self.assertNumQueries(1):
            self.assertEqual(self.ctx().inventory, {'loans': False, 'cards': False, 'recurring': False})
        self.loan()
        Account.objects.create(user=self.user, name='Card', account_type='CREDIT_CARD', currency='₹',
                               balance=D('0'), credit_card_billing_day=5)
        RecurringTransaction.objects.create(user=self.user, transaction_type='EXPENSE', amount=D('100'), currency='₹',
                                            description='x', category='Food', frequency='MONTHLY',
                                            start_date=TODAY - timedelta(days=5), account=self.cash, is_active=True)
        self.assertEqual(self.ctx().inventory, {'loans': True, 'cards': True, 'recurring': True})

    def test_loans_arrive_totalled_and_the_loan_services_maths_runs_without_more_queries(self):
        loan = self.loan()
        LoanRepayment.objects.create(loan=loan, amount=D('13000'), principal_portion=D('6000'), interest_portion=D('7000'),
                                     date=TODAY - timedelta(days=20), from_account=self.cash)
        CapitalEvent.objects.create(user=self.user, date=TODAY - timedelta(days=30), amount=D('50000'), currency='₹',
                                    subtype='loan_prepayment', linked_loan=loan, account=self.cash)
        ctx = self.ctx()
        with CaptureQueriesContext(connection) as queries:
            [loaded] = ctx.loans
            LoanService.calculate_extra_emi_savings(loaded)
            LoanService.current_rate(loaded)
        self.assertEqual(len(queries), 2)                    # the loans, and their rates
        fresh = Loan.objects.get(pk=loan.pk)
        self.assertEqual(loaded._cached_summary['remaining_principal'], LoanService.get_loan_summary(fresh)['remaining_principal'])
        self.assertEqual(loaded._cached_summary['remaining_principal'], 944000.0)

    def test_extra_emi_on_a_real_loan(self):
        loan = self.loan()
        LoanRepayment.objects.create(loan=loan, amount=D('13000'), principal_portion=D('6000'), interest_portion=D('7000'),
                                     date=TODAY - timedelta(days=20), from_account=self.cash)
        with mock.patch('django.utils.timezone.localdate', return_value=TODAY):
            [found] = run('loans.extra_emi_effect', self.ctx())
        self.assertGreaterEqual(found.numbers['months_saved'], 3)

    def test_a_card_owes_what_the_ledger_says(self):
        Account.objects.create(user=self.user, name='Travel Card', account_type='CREDIT_CARD', currency='₹',
                               balance=D('-30000'), credit_limit=D('50000'), credit_card_billing_day=(TODAY + timedelta(days=4)).day)
        [(card, owed)] = self.ctx().cards
        self.assertEqual((card.name, owed), ('Travel Card', D('30000')))
        [found] = run('accounts.card_utilisation', self.ctx())
        self.assertEqual(found.numbers['pct'], 60)

    def test_a_deleted_account_says_nothing(self):
        card = Account.objects.create(user=self.user, name='Old Card', account_type='CREDIT_CARD', currency='₹',
                                      balance=D('-30000'), credit_limit=D('50000'), credit_card_billing_day=10)
        card.is_active = False
        card.save()
        self.assertEqual(self.ctx().cards, [])
        self.assertEqual(self.ctx().inventory['cards'], False)

    def test_reconciliations_read_the_latest_statement_date_per_account(self):
        account = Account.objects.create(user=self.user, name='Savings', account_type='SAVINGS_ACCOUNT', currency='₹', balance=D('0'))
        for days in (120, 70):
            AccountReconciliation.objects.create(user=self.user, account=account, statement_date=TODAY - timedelta(days=days),
                                                 statement_balance=D('0'), app_balance=D('0'), difference=D('0'), status='MATCHED')
        self.assertEqual(self.ctx().reconciliations, {account.pk: TODAY - timedelta(days=70)})
        [found] = run('accounts.not_reconciled', self.ctx())
        self.assertIn('70 days ago', found.message)

    def test_postings_are_read_by_the_description_the_app_gives_them(self):
        RecurringTransaction.objects.create(user=self.user, transaction_type='EXPENSE', amount=D('649'), currency='₹',
                                            description='Netflix', category='Food', frequency='MONTHLY',
                                            start_date=TODAY - timedelta(days=90), account=self.cash, is_active=True)
        self.spend(499, TODAY - timedelta(days=35), 'Food').__class__.objects.filter(user=self.user).update(description='Netflix (Recurring)')
        self.spend(649, TODAY - timedelta(days=4), 'Food').__class__.objects.filter(user=self.user, amount=D('649')).update(description='Netflix (Recurring)')
        [found] = run('recurring.price_change', self.ctx())
        self.assertIn('went up', found.message)

    def test_a_person_with_none_of_it_costs_no_queries_for_it(self):
        self.life()
        with CaptureQueriesContext(connection) as queries:
            engine.refresh(self.user, today=TODAY, costs=('light',))
        sql = ' '.join(q['sql'] for q in queries.captured_queries)
        for table in ('expenses_accountreconciliation', 'expenses_loaninterestrate', 'expenses_recurringtransaction.id'):
            self.assertNotIn(table, sql)
        self.assertLessEqual(len(queries), 16)


class SettingsAndStripsTests(InsightBase):
    def test_the_cushion_target_is_a_choice_from_a_short_list(self):
        prefs.save(self.profile, enabled=True, modules_off=[], notify='normal', push=True, emergency_months=9)
        self.assertEqual(prefs.get(UserProfile.objects.get(pk=self.profile.pk))['emergency_months'], 9)
        self.client.post(reverse('insights-settings'), {'enabled': 'on', 'notify': 'normal', 'emergency_months': '7'})
        self.assertEqual(prefs.get(UserProfile.objects.get(pk=self.profile.pk))['emergency_months'], 9)     # not on the list: kept
        self.client.post(reverse('insights-settings'), {'enabled': 'on', 'notify': 'normal', 'emergency_months': '12'})
        self.assertEqual(prefs.get(UserProfile.objects.get(pk=self.profile.pk))['emergency_months'], 12)
        self.client.post(reverse('insights-settings'), {'enabled': 'on', 'notify': 'normal'})
        self.assertEqual(prefs.get(UserProfile.objects.get(pk=self.profile.pk))['emergency_months'], 12)    # absent: kept

    def test_the_settings_page_offers_it_in_the_readers_language(self):
        html = self.client.get(reverse('profile-settings')).content.decode()
        self.assertIn('name="emergency_months"', html)
        self.assertIn('6 months of spending', html)
        UserProfile.objects.filter(pk=self.profile.pk).update(language='hi')
        hindi = self.client.get(reverse('profile-settings'), HTTP_ACCEPT_LANGUAGE='hi').content.decode()
        self.assertIn('६ महीनों का खर्च', hindi)

    @ENGINE_ON
    def test_the_other_pages_embed_their_strip_only_when_the_engine_is_on(self):
        Loan.objects.create(user=self.user, name='Home Loan', loan_type='HOME', initial_principal=D('1000000'),
                            duration_months=120, start_date=TODAY - timedelta(days=400), currency='₹')
        for name, modules in (('recurring-list', 'recurring'), ('loan-list', 'loans'), ('account-list', 'accounts')):
            html = self.client.get(reverse(name)).content.decode()
            self.assertIn(f'modules={modules}&strip=1', html, name)
        with override_settings(INSIGHTS_ENGINE_MODE='off'):
            for name in ('recurring-list', 'loan-list', 'account-list'):
                self.assertNotIn('strip=1', self.client.get(reverse(name)).content.decode(), name)

    @ENGINE_ON
    def test_each_strip_leaves_out_what_its_page_already_says(self):
        from expenses.insights.views import STRIP_SKIPS
        self.assertEqual(STRIP_SKIPS[Module.RECURRING], {'recurring.bills_soon'})
        self.assertEqual(STRIP_SKIPS[Module.ACCOUNTS], {'accounts.card_billing_soon'})
        with mock.patch('expenses.insights.views.engine.generate', return_value=[]) as generate:
            for modules, expected in (('recurring', {'recurring.bills_soon'}), ('loans', set()),
                                      ('accounts', {'accounts.card_billing_soon'})):
                self.client.get(reverse('insights-panel') + f'?surface=module&modules={modules}&strip=1')
                self.assertEqual(generate.call_args.kwargs['skip_rules'], expected, modules)

    def test_a_plus_rule_is_a_figure_free_preview_for_free_and_a_sensitive_one_is_not_shown_at_all(self):
        UserProfile.objects.filter(pk=self.profile.pk).update(tier='FREE')
        for name in ('Rent', 'Phone'):
            RecurringTransaction.objects.create(user=self.user, transaction_type='EXPENSE', amount=D('1000'), currency='₹',
                                                description=name, category='Food', frequency='MONTHLY',
                                                start_date=TODAY - timedelta(days=5), account=self.cash, is_active=True)
        self.life()
        engine.refresh(self.user, today=TODAY, profile=UserProfile.objects.get(pk=self.profile.pk))
        row = InsightState.objects.get(user=self.user, rule_id='recurring.fixed_costs')
        self.assertTrue(row.locked)
        self.assertNotRegex(str(row.payload), r'\d{2},\d{3}')
        self.assertEqual(row.payload['title'], 'A look at your fixed costs')

    def test_the_new_rules_are_documented(self):
        text = (__import__('pathlib').Path(__file__).resolve().parents[3] / 'docs' / 'insights-rules.md').read_text()
        for rule in registry.all_rules():
            self.assertIn(f'`{rule.id}`', text, rule.id)
