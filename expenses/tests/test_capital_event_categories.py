"""Optional category on capital events: the form, the reports that use it, and the Include switch."""
import datetime
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from expenses.budgets import bulk_monthly_spend, monthly_spend_by_category, normalize_category
from expenses.forms import CapitalEventForm
from expenses.models import Account, CapitalEvent, Category, Expense, RecurringTransaction

D = Decimal


def today():
    return timezone.localdate()


class Base(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user('cap-cat', 'cap@example.com', 'pw')
        profile = self.user.profile
        profile.tier = 'PRO'
        profile.consent_granted = True
        profile.has_seen_tutorial = True
        profile.currency = '₹'
        profile.save()
        self.client.force_login(self.user)
        Account.objects.filter(user=self.user).delete()
        self.bank = Account.objects.create(user=self.user, name='Bank', account_type='SAVINGS_ACCOUNT',
                                           balance=D('1000000'), currency='₹')
        for name in ('Electronics', 'Food'):
            Category.objects.get_or_create(user=self.user, name=name)

    def event(self, amount=1000, category='', subtype='large_purchase', when=None, **kw):
        kw.setdefault('exclude_from_budget', False)
        kw.setdefault('exclude_from_averages', True)
        return CapitalEvent.objects.create(
            user=self.user, account=self.bank, amount=D(amount), base_amount=D(amount), date=when or today(),
            subtype=subtype, category=category, currency='₹', **kw)

    def form_data(self, **overrides):
        data = {'date': today().isoformat(), 'amount': '5000', 'currency': '₹', 'account': self.bank.pk,
                'subtype': 'large_purchase', 'category': '', 'note': 'Laptop',
                'exclude_from_averages': 'on', 'include_in_net_worth': 'on'}
        data.update(overrides)
        return data


class ModelAndFormTests(Base):
    def test_category_is_optional_and_reports_under_the_subtype_by_default(self):
        plain = self.event()
        self.assertEqual(plain.category, '')
        self.assertEqual(plain.report_category, 'Large Purchase')
        self.assertEqual(self.event(category='Electronics').report_category, 'Electronics')

    def test_form_accepts_one_of_the_users_categories_or_none(self):
        self.assertTrue(CapitalEventForm(self.form_data(category='Electronics'), user=self.user).is_valid())
        self.assertTrue(CapitalEventForm(self.form_data(category=''), user=self.user).is_valid())

    def test_form_rejects_a_category_the_user_does_not_have(self):
        other = User.objects.create_user('other-user', 'o@example.com', 'pw')
        Category.objects.create(user=other, name='Secret')
        form = CapitalEventForm(self.form_data(category='Secret'), user=self.user)
        self.assertFalse(form.is_valid())
        self.assertIn('category', form.errors)

    def test_editing_keeps_a_category_that_was_renamed_or_deleted(self):
        event = self.event(category='Old Name')
        form = CapitalEventForm(instance=event, user=self.user)
        self.assertIn('Old Name', [value for value, _label in form.fields['category'].choices])

    def test_create_view_saves_the_category(self):
        response = self.client.post(reverse('capital-event-create'), self.form_data(category='Electronics'))
        self.assertEqual(response.status_code, 302, getattr(response, 'context', None) and response.context['form'].errors)
        self.assertEqual(CapitalEvent.objects.get(user=self.user).category, 'Electronics')

    def test_form_page_shows_the_field(self):
        html = self.client.get(reverse('capital-event-create')).content.decode()
        self.assertIn('id="id_category"', html)
        self.assertIn('Electronics', html)


class BudgetAndDashboardTests(Base):
    def test_a_categorised_event_counts_under_its_category_not_its_subtype(self):
        self.event(amount=40000, category='Electronics')
        Expense.objects.create(user=self.user, account=self.bank, amount=500, base_amount=500, date=today(),
                               category='Electronics', currency='₹', exchange_rate=1)
        spend, grand = monthly_spend_by_category(self.user, today().year, today().month)
        self.assertEqual(spend[normalize_category('Electronics')], D('40500'))
        self.assertNotIn(normalize_category('Large Purchase'), spend)
        self.assertEqual(grand, D('40500'))

    def test_an_uncategorised_event_still_counts_under_its_subtype(self):
        self.event(amount=2000)
        spend, _grand = monthly_spend_by_category(self.user, today().year, today().month)
        self.assertEqual(spend[normalize_category('Large Purchase')], D('2000'))

    def test_events_excluded_from_budget_do_not_count_either_way(self):
        self.event(amount=2000, category='Electronics', exclude_from_budget=True)
        spend, grand = monthly_spend_by_category(self.user, today().year, today().month)
        self.assertEqual(spend, {})
        self.assertEqual(grand, D('0'))

    def test_bulk_spend_for_alerts_uses_the_category_too(self):
        self.event(amount=3000, category='Electronics')
        bulk = bulk_monthly_spend(today().year, today().month)
        self.assertEqual(bulk[(self.user.id, normalize_category('Electronics'))], D('3000'))

    def test_dashboard_breakdown_groups_a_categorised_event_under_its_category(self):
        self.event(amount=40000, category='Electronics')
        context = self.client.get(reverse('home'), {'time_period': 'this_month'}).context
        totals = {row['category']: row['total'] for row in context['category_data']}
        self.assertEqual(totals.get('Electronics'), 40000.0)
        self.assertNotIn('Large Purchase', totals)


class AnalyticsSwitchTests(Base):
    def cats(self, **params):
        params.setdefault('year', today().year)
        context = self.client.get(reverse('analytics'), params).context
        return dict(zip(context['cat_labels'], context['cat_data'])), context

    def setUp(self):
        super().setUp()
        when = datetime.date(today().year, 1, 15)
        Expense.objects.create(user=self.user, account=self.bank, amount=2000, base_amount=2000, date=when,
                               category='Electronics', currency='₹', exchange_rate=1)
        self.event(amount=90000, category='Electronics', when=when)
        self.event(amount=15000, subtype='medical_lump_sum', when=when)

    def test_switch_off_leaves_capital_events_out_of_the_category_chart(self):
        cats, context = self.cats()
        self.assertEqual(cats, {'Electronics': 2000.0})
        self.assertFalse(context['include_capital_events'])

    def test_switch_on_adds_them_under_their_category_or_event_type(self):
        cats, context = self.cats(include_capital_events='1')
        self.assertEqual(cats['Electronics'], 92000.0)          # expense and event share one slice
        self.assertEqual(cats['Medical Lump Sum'], 15000.0)     # no category: reported under the event type
        self.assertTrue(context['include_capital_events'])

    def test_largest_slice_comes_first(self):
        _cats, context = self.cats(include_capital_events='1')
        self.assertEqual(context['cat_labels'][0], 'Electronics')

    def test_deleted_events_and_other_years_are_ignored(self):
        CapitalEvent.objects.filter(subtype='medical_lump_sum').update(is_deleted=True)
        self.event(amount=777, category='Food', when=datetime.date(today().year - 1, 3, 3))
        cats, _context = self.cats(include_capital_events='1')
        self.assertNotIn('Medical Lump Sum', cats)
        self.assertNotIn('Food', cats)

    def test_header_controls_are_matching_pills_and_the_year_uses_the_standard_searchable_dropdown(self):
        html = self.client.get(reverse('analytics'), {'year': today().year}).content.decode()
        self.assertIn('class="tmr-toggle-chip', html)
        self.assertIn('class="tmr-year-pill"', html)
        # a plain form-select is what the app's searchable dropdown (with its search box) attaches to
        self.assertRegex(html, r'<select id="analyticsYear" name="year" class="form-select[^"]*"')

    def test_the_page_has_the_switch(self):
        html = self.client.get(reverse('analytics'), {'year': today().year}).content.decode()
        self.assertIn('name="include_capital_events"', html)
        on = self.client.get(reverse('analytics'), {'year': today().year, 'include_capital_events': '1'}).content.decode()
        self.assertRegex(on, r'id="includeCapitalEvents"[^>]*checked')
        self.assertIn('incl. capital events', on)


class ListAndConversionTests(Base):
    def test_list_shows_the_category_and_filters_by_it(self):
        self.event(amount=111, category='Electronics', note='Laptop')
        self.event(amount=222, note='Hospital', subtype='medical_lump_sum')
        page = self.client.get(reverse('capital-event-list'), {'time_period': 'all'}).content.decode()
        self.assertIn('Laptop', page)
        self.assertIn('Electronics', page)
        filtered = self.client.get(reverse('capital-event-list'), {'time_period': 'all', 'category': 'Electronics'})
        events = list(filtered.context['events'])
        self.assertEqual([e.note for e in events], ['Laptop'])

    def test_expense_converted_to_event_keeps_its_category(self):
        expense = Expense.objects.create(user=self.user, account=self.bank, amount=500, base_amount=500,
                                         date=today(), category='Electronics', description='Mouse',
                                         currency='₹', exchange_rate=1)
        self.client.post(reverse('expense-convert', args=[expense.pk]))
        self.assertEqual(CapitalEvent.objects.get(user=self.user).category, 'Electronics')

    def test_event_converted_to_expense_uses_its_category_else_the_event_type(self):
        with_cat = self.event(amount=500, category='Electronics', note='Mouse')
        without = self.event(amount=600, note='Chair')
        self.client.post(reverse('capital-event-convert', args=[with_cat.pk]))
        self.client.post(reverse('capital-event-convert', args=[without.pk]))
        categories = dict(Expense.objects.filter(user=self.user).values_list('description', 'category'))
        self.assertEqual(categories['Mouse'], 'Electronics')
        self.assertEqual(categories['Chair'], 'Large Purchase')

    def test_recurring_capital_events_carry_the_category(self):
        from expenses.views.mixins import process_user_recurring_transactions
        RecurringTransaction.objects.create(
            user=self.user, transaction_type='CAPITAL', amount=D('1200'), description='EMI gadget',
            category='Electronics', capital_subtype='large_purchase', currency='₹', account=self.bank,
            frequency='MONTHLY', start_date=today(), next_due_date=today())
        process_user_recurring_transactions(self.user, force=True)
        self.assertEqual(CapitalEvent.objects.get(user=self.user).category, 'Electronics')


class PlaceholderAndLayoutTests(Base):
    def test_empty_choice_is_a_placeholder_not_a_listed_option(self):
        form = CapitalEventForm(user=self.user)
        self.assertEqual(form.fields['category'].choices[0], ('', ''))
        self.assertEqual(form.fields['category'].widget.attrs['data-placeholder'], 'Select a category')
        html = self.client.get(reverse('capital-event-create')).content.decode()
        self.assertIn('data-placeholder="Select a category"', html)
        self.assertNotIn('No category', html)

    def test_searchable_select_skips_empty_options_and_can_clear_when_it_has_a_placeholder(self):
        html = self.client.get(reverse('capital-event-create')).content.decode()
        self.assertIn('dataset.placeholder', html)
        self.assertIn('searchable-select-clear', html)
        self.assertIn("(placeholder || opt.text.trim() === '---------')", html)

    def test_subtype_category_and_account_share_one_row(self):
        html = self.client.get(reverse('capital-event-create')).content.decode()
        row = html[html.index('Row 2: Subtype + Category + Account'):html.index('Loan link (shown only')]
        self.assertEqual(row.count('col-xl-4 col-md-6'), 3)
        for field in ('id_subtype', 'id_category', 'id_account'):
            self.assertIn(field, row)
