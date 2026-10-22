"""The migrated rules against the dashboard's own insight code, on the same data.

`test_legacy_and_engine_agree_*` run the real dashboard view with the clock fixed, capture what its legacy code
concluded for each migrated insight, and compare it with the engine's rule: the numbers must be identical for a
person on calendar months. The differences that remain are listed in docs/insights-engine-plan.md and asserted here
so they cannot grow unnoticed.
"""
import logging
from datetime import datetime, timedelta
from decimal import Decimal
from unittest import mock

from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from expenses.insights import shadow
from expenses.models import Account, RecurringTransaction

from .base import D, InsightBase, month_start

FIXED = timezone.make_aware(datetime(2026, 10, 22, 12, 0))
TODAY = FIXED.date()


class DeltaTests(InsightBase):
    def test_delta_buckets(self):
        self.assertEqual(shadow.delta(None, None), 'exact')
        self.assertEqual(shadow.delta(set(), None), 'exact')
        self.assertEqual(shadow.delta({'a'}, {'a'}), 'exact')
        self.assertEqual(shadow.delta({'a'}, {'b'}), 'far')
        self.assertEqual(shadow.delta(None, {'a'}), 'shape')
        self.assertEqual(shadow.delta({'category': 'a', 'pct': 100}, {'category': 'a', 'pct': 103}), 'close')
        self.assertEqual(shadow.delta({'pct': 100}, {'pct': 150}), 'far')
        self.assertEqual(shadow.delta(Decimal('100'), Decimal('100.00')), 'exact')


class ParityBase(InsightBase):
    def run_home(self, **settings):
        """Render the dashboard with the clock fixed and shadow compare on; return (legacy, deltas)."""
        captured = {}
        real = shadow.compare

        def spy(user, legacy):
            captured['legacy'] = dict(legacy)
            captured['deltas'] = real(user, legacy)
            return captured['deltas']
        with override_settings(INSIGHTS_ENGINE_MODE='shadow', INSIGHTS_SHADOW_PERCENT=100, **settings), \
                mock.patch('django.utils.timezone.now', return_value=FIXED), \
                mock.patch('expenses.views.dashboard.insights_shadow.compare', side_effect=spy):
            response = self.client.get(reverse('home'))
        self.assertEqual(response.status_code, 200)
        return captured.get('legacy', {}), captured.get('deltas', {})

    def assert_agree(self, deltas, expected_rules, known=()):
        self.assertEqual(sorted(k for k, v in deltas.items() if v != 'exact' and k not in known), [], deltas)
        for rule in expected_rules:
            self.assertIn(rule, deltas)


class CalendarMonthParityTests(ParityBase):
    def test_legacy_and_engine_agree_when_every_rule_has_something_to_say(self):
        self.life(today=TODAY)
        self.category('Groceries', 10000)        # 12,000 spent: over
        self.category('Dining', 40000)
        Account.objects.create(user=self.user, name='Bank FD', account_type='FIXED_DEPOSIT', currency='₹',
                               balance=D('210000'), deposit_maturity_date=TODAY + timedelta(days=12))
        row = RecurringTransaction.objects.create(
            user=self.user, transaction_type='EXPENSE', amount=D('499'), currency='₹', description='Netflix',
            category='Dining', frequency='MONTHLY', start_date=TODAY - timedelta(days=28),
            last_processed_date=TODAY - timedelta(days=28), account=self.cash, is_active=True)
        RecurringTransaction.objects.filter(pk=row.pk).update(next_due_date=TODAY + timedelta(days=2))
        legacy, deltas = self.run_home()
        self.assertEqual(legacy['cashflow.pace_vs_usual'], {'pct': 72})
        self.assertEqual(legacy['budgets.limit_passed'], {'groceries'})
        self.assertEqual(legacy['cashflow.saving_streak'], 6)
        # momentum differs by design here: this person saves 22.8%, so the dashboard shows Super Saver (retired) instead
        self.assert_agree(deltas, ['cashflow.pace_vs_usual', 'budgets.limit_passed', 'budgets.limit_nearing',
                                   'expenses.category_above_usual', 'cashflow.saving_streak', 'cashflow.year_pace',
                                   'accounts.deposit_maturing', 'recurring.bills_soon'],
                         known=('cashflow.savings_momentum',))

    def test_legacy_and_engine_agree_on_a_limit_that_is_nearly_reached_and_one_running_out(self):
        self.life(today=TODAY)
        self.category('Groceries', 14000)       # 12,000 spent: 85.7%, nearing
        self.category('Transport', 5000)
        self.spend(4000, month_start(TODAY, 0), 'Transport')      # 80% after 22 days: runs out around the 28th
        legacy, deltas = self.run_home()
        self.assertEqual(legacy['budgets.limit_nearing'], {'groceries'})
        self.assertEqual(legacy['budgets.category_runout'], {'groceries', 'transport'})
        self.assert_agree(deltas, ['budgets.limit_nearing', 'budgets.category_runout', 'budgets.limit_passed'])

    def test_legacy_and_engine_agree_on_momentum_below_a_twenty_percent_rate(self):
        for back in range(1, 6):
            first = month_start(TODAY, back)
            self.income(60000, first)
            self.spend(55000 if back == 1 else 40000, first, 'Rent')
        self.income(60000, month_start(TODAY, 0))
        self.spend(50000, month_start(TODAY, 0), 'Rent')
        legacy, deltas = self.run_home()
        self.assertTrue(legacy['cashflow.savings_momentum'])
        self.assert_agree(deltas, ['cashflow.savings_momentum'])

    def test_legacy_and_engine_agree_when_there_is_nothing_to_say(self):
        self.income(60000, month_start(TODAY, 0))
        self.spend(1500, month_start(TODAY, 0), 'Rent')
        _legacy, deltas = self.run_home()
        self.assert_agree(deltas, [])


class KnownDifferenceTests(ParityBase):
    """The places where the engine deliberately differs from the dashboard, each a decision in the plan."""

    def test_momentum_also_speaks_for_a_rate_of_twenty_percent_or_more(self):
        self.life(today=TODAY)           # a 22.8% rate: the dashboard shows Super Saver (retired) instead
        legacy, deltas = self.run_home()
        self.assertFalse(legacy['cashflow.savings_momentum'])
        self.assertEqual(deltas['cashflow.savings_momentum'], 'shape')

    def test_the_year_projection_waits_for_a_second_month(self):
        # in January the dashboard projects from a single month; the engine says "we need one more month"
        from expenses.insights import registry, sampling
        ctx = sampling.scenario(today=datetime(2026, 1, 20).date())
        with ctx.declared(registry.get('cashflow.year_pace').needs):
            self.assertEqual(registry.get('cashflow.year_pace').evaluate(ctx), [])

    def test_income_that_recurs_is_not_a_payment_due(self):
        row = RecurringTransaction.objects.create(
            user=self.user, transaction_type='INCOME', amount=D('60000'), currency='₹', description='Salary',
            category='Salary', frequency='MONTHLY', start_date=TODAY - timedelta(days=28),
            last_processed_date=TODAY - timedelta(days=28), account=self.cash, is_active=True)
        RecurringTransaction.objects.filter(pk=row.pk).update(next_due_date=TODAY + timedelta(days=2))
        self.income(1000, month_start(TODAY, 0))
        legacy, deltas = self.run_home()
        self.assertEqual(legacy['recurring.bills_soon'], [('Salary', (TODAY + timedelta(days=2)).isoformat())])
        self.assertEqual(deltas['recurring.bills_soon'], 'shape')


class ShadowPrivacyTests(ParityBase):
    def test_shadow_records_only_rule_ids_and_how_close_they_were(self):
        self.life(today=TODAY)
        self.category('Groceries', 10000)
        with self.assertLogs('expenses.insights.shadow', level=logging.INFO) as logs:
            with mock.patch('expenses.insights.telemetry.ph_capture') as capture:
                self.run_home()
        text = '\n'.join(logs.output)
        self.assertIn('insight shadow rule_id=cashflow.pace_vs_usual delta=exact', text)
        for secret in ('₹', 'Groceries', 'groceries', 'Dining', '12000', '72'):
            self.assertNotIn(secret, text)
        for call in capture.call_args_list:
            properties = call.args[2]
            self.assertLessEqual(set(properties), {'rule_id', 'matched', 'delta'})

    def test_shadow_never_breaks_the_dashboard(self):
        self.life(today=TODAY)
        with override_settings(INSIGHTS_ENGINE_MODE='shadow', INSIGHTS_SHADOW_PERCENT=100), \
                mock.patch('django.utils.timezone.now', return_value=FIXED), \
                mock.patch('expenses.insights.shadow.registry.get', side_effect=RuntimeError('Dining ₹1')):
            self.assertEqual(self.client.get(reverse('home')).status_code, 200)

    @override_settings(INSIGHTS_ENGINE_MODE='shadow', INSIGHTS_SHADOW_PERCENT=0)
    def test_only_a_sample_of_people_is_compared(self):
        self.assertFalse(shadow.should_compare(self.user))
        with override_settings(INSIGHTS_SHADOW_PERCENT=100):
            self.assertTrue(shadow.should_compare(self.user))
        with override_settings(INSIGHTS_SHADOW_PERCENT=100, INSIGHTS_ENGINE_MODE='off'):
            self.assertFalse(shadow.should_compare(self.user))
