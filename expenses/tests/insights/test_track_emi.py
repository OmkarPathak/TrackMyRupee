"""loans.track_emi: an EMI that is paid with no loan behind it gets one calm pointer to Loans."""
from datetime import date, timedelta
from decimal import Decimal

from django.test import SimpleTestCase
from django.urls import reverse

from expenses.analytics.facts import ExpenseRow
from expenses.insights import registry, sampling
from expenses.insights.types import Plan

D = Decimal
RULE = registry.get('loans.track_emi')


def evaluate(plan=Plan.FREE, **kw):
    return RULE.evaluate(sampling.scenario(plan=plan, **kw))


def row(day, description, amount='12000', category='Bills'):
    return ExpenseRow(id=1, date=day, category=category, amount=D(amount), account_id=None,
                      created_at=None, description=description, from_import=False)


class TrackEmiTests(SimpleTestCase):
    def test_a_recurring_emi_with_no_loan_gets_the_pointer(self):
        found = evaluate()
        self.assertEqual(len(found), 1)
        self.assertIn('18.2k', found[0].message)

    def test_free_is_pointed_at_pricing_and_told_loans_are_in_plus(self):
        insight = evaluate(Plan.FREE)[0]
        self.assertEqual(insight.action.url, reverse('pricing'))
        self.assertIn('Plus plan', insight.message)

    def test_plus_is_pointed_at_the_loan_form(self):
        insight = evaluate(Plan.PLUS)[0]
        self.assertEqual(insight.action.url, reverse('loan-create'))
        self.assertNotIn('Plus plan', insight.message)

    def test_nothing_is_said_when_there_is_a_loan(self):
        self.assertEqual(evaluate(loans=[sampling._loan('Home Loan', 5000000, 4200000, '8.5')]), [])

    def test_nothing_is_said_without_anything_that_looks_like_an_emi(self):
        self.assertEqual(evaluate(recurring_all=[sampling._recurring('Netflix', 'EXPENSE', 499)]), [])

    def test_a_recurring_expense_named_like_an_emi_counts(self):
        insight = evaluate(recurring_all=[sampling._recurring('Car EMI', 'EXPENSE', 9000)])
        self.assertEqual(len(insight), 1)
        self.assertIn('9k', insight[0].message)

    def test_expenses_named_like_an_emi_in_two_months_count(self):
        today = sampling.TODAY
        rows = [row(today - timedelta(days=5), 'Bike loan EMI'), row(today - timedelta(days=36), 'Bike loan EMI')]
        self.assertEqual(len(evaluate(recurring_all=[], rows=rows)), 1)

    def test_one_such_expense_is_not_enough(self):
        rows = [row(sampling.TODAY - timedelta(days=5), 'Bike loan EMI')]
        self.assertEqual(evaluate(recurring_all=[], rows=rows), [])

    def test_the_word_must_stand_alone(self):
        rows = [row(sampling.TODAY - timedelta(days=5), 'Seemingly one'), row(sampling.TODAY - timedelta(days=36), 'Seemingly one')]
        self.assertEqual(evaluate(recurring_all=[], rows=rows), [])


class TrackEmiEndToEndTests(__import__('expenses.tests.insights.base', fromlist=['InsightBase']).InsightBase):
    """A Free person who pays an EMI by hand, with no loan: the pointer reaches the dashboard strip."""
    tier = 'FREE'

    def test_a_free_person_with_an_emi_entry_gets_the_pointer_on_the_dashboard(self):
        from expenses.insights import engine
        from expenses.insights.types import Surface
        from expenses.tests.insights.base import TODAY, month_start
        for back in (0, 1):
            self.spend(12000, month_start(TODAY, back), 'Bike loan EMI')
        engine.refresh(self.user, today=TODAY)
        shown = engine.generate(self.user, Surface.DASHBOARD)
        ids = [item.insight.rule_id if hasattr(item, 'insight') else item.rule_id for item in shown]
        self.assertIn('loans.track_emi', ids)
