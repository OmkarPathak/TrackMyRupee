"""Income that pays back a capital event (Income.refund_for_capital_event).

The trip case: you pay 20,000 for four people, it is logged as a capital event, and friends pay 15,000 back as a
"Refund / Reimbursement". Linked to the event, the refund is not income: it comes off the event, so only your own
5,000 share is ever counted. Anything NOT linked behaves exactly as it did before the link existed.
"""

from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from expenses.analytics.facts import load_facts
from expenses.forms import IncomeForm
from expenses.models import Account, CapitalEvent, Expense, Income, LoanRepayment, UserProfile
from expenses.savings import income_totals, monthly_parts, monthly_savings, period_totals, savings_for_period

D = Decimal
REFUND = 'Refund / Reimbursement'


def today():
    return timezone.localdate()


class RefundLinkTestBase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('rf', password='x')
        profile, _ = UserProfile.objects.get_or_create(user=self.user, defaults={'currency': '₹'})
        profile.tier = 'PRO'
        profile.save()
        self.user.refresh_from_db()
        self.client.force_login(self.user)
        self.account = Account.objects.create(user=self.user, name='Bank', account_type='BANK', balance=0, currency='₹')
        cache.clear()

    def capital(self, amount, excluded=True, when=None):
        return CapitalEvent.objects.create(
            user=self.user, date=when or today(), amount=D(str(amount)), subtype='other', currency='₹',
            account=self.account, exclude_from_averages=excluded)

    def income(self, amount, source_type='Salary', when=None, link=None):
        return Income.objects.create(
            user=self.user, date=when or today(), amount=D(str(amount)), source_type=source_type, currency='₹',
            account=self.account, refund_for_capital_event=link)

    def month(self):
        first = today().replace(day=1)
        return first, today()


class TestSavingsWithLinkedRefund(RefundLinkTestBase):
    def test_unlinked_data_is_unchanged(self):
        """Nothing is linked: every figure is what the old definition gives."""
        self.income(10000)
        self.income(300, 'Cashback & Rewards')
        self.income(200, REFUND)
        self.capital(500, excluded=False)
        start, end = self.month()
        r = savings_for_period(self.user, start, end)
        self.assertEqual((r.income, r.cashback_refund, r.capital_events), (D('10500'), D('500'), D('500')))
        self.assertEqual(r.savings, D('10000'))

    def test_trip_counted_event_only_your_share_is_spent(self):
        event = self.capital(20000, excluded=False)
        self.income(50000)
        self.income(15000, REFUND, link=event)
        start, end = self.month()
        r = savings_for_period(self.user, start, end)
        self.assertEqual(r.income, D('50000'))          # the refund is not income
        self.assertEqual(r.cashback_refund, D('0'))     # nor does it distort the rate denominator
        self.assertEqual(r.capital_events, D('5000'))   # 20,000 - 15,000
        self.assertEqual(r.savings, D('45000'))

    def test_trip_default_excluded_event_does_not_inflate_savings(self):
        """The reported bug: the default (excluded) event used to leave the 15k refund as pure extra savings."""
        event = self.capital(20000, excluded=True)
        self.income(50000)
        self.income(15000, REFUND, link=event)
        start, end = self.month()
        r = savings_for_period(self.user, start, end)
        self.assertEqual((r.income, r.capital_events, r.savings), (D('50000'), D('0'), D('50000')))

    def test_unlinked_refund_still_counts_as_income_as_before(self):
        self.income(50000)
        self.income(15000, REFUND)
        start, end = self.month()
        r = savings_for_period(self.user, start, end)
        self.assertEqual((r.income, r.cashback_refund), (D('65000'), D('15000')))

    def test_deleted_event_releases_the_refund_back_to_income(self):
        event = self.capital(20000)
        self.income(15000, REFUND, link=event)
        event.is_deleted = True
        event.save()
        start, end = self.month()
        self.assertEqual(savings_for_period(self.user, start, end).income, D('15000'))

    def test_period_totals_and_income_totals_agree(self):
        event = self.capital(20000, excluded=False)
        self.income(1000)
        self.income(15000, REFUND, link=event)
        qs = Income.objects.filter(user=self.user)
        self.assertEqual(income_totals(qs), (D('1000'), D('0')))
        t = period_totals(qs, Expense.objects.none(), LoanRepayment.objects.none(),
                          CapitalEvent.objects.filter(user=self.user))
        self.assertEqual((t['income'], t['cashback_refund'], t['refund_capital'], t['capital']),
                         (D('1000'), D('0'), D('15000'), D('5000')))

    def test_monthly_parts_match_the_period_figures(self):
        event = self.capital(20000, excluded=False)
        self.income(50000)
        self.income(15000, REFUND, link=event)
        start, end = self.month()
        key = (today().year, today().month)
        part = monthly_parts(self.user, start, end)[key]
        self.assertEqual((part['income'], part['cb_rf'], part['capital']), (D('50000'), D('0'), D('5000')))
        r = monthly_savings(self.user, start, end)[key]
        self.assertEqual(r.savings, savings_for_period(self.user, start, end).savings)

    def test_refund_in_a_later_month_comes_off_in_that_month(self):
        """Net is taken on the day the money arrived, as cash flow does."""
        first = today().replace(day=1)
        earlier = first - timedelta(days=5)
        event = self.capital(20000, excluded=False, when=earlier)
        self.income(15000, REFUND, link=event)
        parts = monthly_parts(self.user, earlier.replace(day=1), today())
        self.assertEqual(parts[(earlier.year, earlier.month)]['capital'], D('20000'))
        self.assertEqual(parts[(today().year, today().month)]['capital'], D('-15000'))


class TestAccountBalanceUnchanged(RefundLinkTestBase):
    def test_linked_refund_still_credits_the_account(self):
        event = self.capital(20000)
        before = Account.objects.get(pk=self.account.pk).balance
        self.income(15000, REFUND, link=event)
        self.assertEqual(Account.objects.get(pk=self.account.pk).balance - before, D('15000'))


class TestAnalyticsFacts(RefundLinkTestBase):
    def test_facts_net_the_refund_off_the_event(self):
        event = self.capital(20000, excluded=False)
        self.income(50000)
        self.income(15000, REFUND, link=event)
        start, end = self.month()
        summary = load_facts(self.user, start, end).summarize(start, end)
        self.assertEqual(summary.income, D('50000'))
        self.assertEqual(summary.savings.capital_events, D('5000'))

    def test_toggle_on_counts_an_excluded_event_net_of_its_refund(self):
        event = self.capital(20000, excluded=True)
        self.income(50000)
        self.income(15000, REFUND, link=event)
        start, end = self.month()
        off = load_facts(self.user, start, end).summarize(start, end)
        on = load_facts(self.user, start, end, include_capital_events=True).summarize(start, end)
        self.assertEqual((off.saved, on.saved), (D('50000'), D('45000')))


class TestIncomeForm(RefundLinkTestBase):
    def data(self, **extra):
        data = {'date': today().isoformat(), 'amount': '15000', 'currency': '₹', 'account': self.account.pk,
                'source_type': REFUND, 'description': 'trip'}
        data.update(extra)
        return data

    def test_link_is_saved_for_a_refund(self):
        event = self.capital(20000)
        form = IncomeForm(self.data(refund_for_capital_event=event.pk), user=self.user)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.save(commit=False).refund_for_capital_event, event)

    def test_link_is_dropped_for_other_source_types(self):
        event = self.capital(20000)
        form = IncomeForm(self.data(source_type='Salary', refund_for_capital_event=event.pk), user=self.user)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertIsNone(form.save(commit=False).refund_for_capital_event)

    def test_another_users_event_cannot_be_linked(self):
        other = User.objects.create_user('rf2', password='x')
        UserProfile.objects.get_or_create(user=other, defaults={'currency': '₹'})
        foreign = CapitalEvent.objects.create(user=other, date=today(), amount=D('100'), subtype='other', currency='₹')
        form = IncomeForm(self.data(refund_for_capital_event=foreign.pk), user=self.user)
        self.assertFalse(form.is_valid())
        self.assertIn('refund_for_capital_event', form.errors)

    def test_a_form_that_omits_the_field_leaves_an_existing_link_alone(self):
        event = self.capital(20000)
        income = self.income(15000, REFUND, link=event)
        data = self.data()  # an older page / client: no refund_for_capital_event key at all
        form = IncomeForm(data, instance=income, user=self.user)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.save(commit=False).refund_for_capital_event, event)

    def test_create_page_renders_the_field_and_list_shows_the_badge(self):
        event = self.capital(20000)
        self.income(15000, REFUND, link=event)
        page = self.client.get(reverse('income-create'))
        self.assertContains(page, 'refund_for_capital_event')
        listing = self.client.get(reverse('income-list'))
        self.assertContains(listing, 'Pays back a big purchase')

    def test_dashboard_still_renders_with_a_linked_refund(self):
        event = self.capital(20000, excluded=False)
        self.income(50000)
        self.income(15000, REFUND, link=event)
        resp = self.client.get(reverse('home'))
        self.assertEqual(resp.status_code, 200)
        hero = resp.context['hero_metrics']
        self.assertEqual((hero['saved'], hero['spent']), (D('45000'), D('5000')))
