"""Income that pays back an expense (Income.refund_for_expense).

The trip dinner: you pay 20,000 for four, friends repay 15,000. Linked to the expense, the refund is not income and the
expense counts for what it really cost (5,000) in its own month, category and budget. Expenses nobody refunded, and
income nobody linked, behave exactly as they did before the link existed.
"""

from datetime import date, timedelta
from decimal import Decimal
from io import StringIO

from django.contrib.auth.models import User
from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from expenses.analytics.facts import load_facts
from expenses.budgets import monthly_spend_by_category, normalize_category
from expenses.forms import IncomeForm
from expenses.models import Account, CapitalEvent, Expense, Income, UserProfile
from expenses.savings import monthly_parts, monthly_savings, savings_for_period
from expenses.refund_targets import RECENT, search_refund_targets

D = Decimal
REFUND = 'Refund / Reimbursement'


def today():
    return timezone.localdate()


class Base(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('xr', password='x')
        profile, _ = UserProfile.objects.get_or_create(user=self.user, defaults={'currency': '₹'})
        profile.tier = 'PRO'
        profile.save()
        self.user.refresh_from_db()
        self.client.force_login(self.user)
        self.account = Account.objects.create(user=self.user, name='Bank', account_type='BANK', balance=0, currency='₹')
        cache.clear()

    def expense(self, amount, when=None, description='Trip dinner', category='Dining Out', user=None):
        return Expense.objects.create(user=user or self.user, date=when or today(), amount=D(str(amount)),
                                      description=description, category=category, currency='₹',
                                      account=self.account if user is None else None)

    def refund(self, expense, amount, when=None):
        return Income.objects.create(user=self.user, date=when or today(), amount=D(str(amount)), source_type=REFUND,
                                     currency='₹', account=self.account, refund_for_expense=expense)

    def income(self, amount, source_type='Salary', when=None):
        return Income.objects.create(user=self.user, date=when or today(), amount=D(str(amount)), source_type=source_type,
                                     currency='₹', account=self.account)

    def month(self):
        return today().replace(day=1), today()

    def refreshed(self, expense):
        expense.refresh_from_db()
        return expense.refunded_base


class TestRefundedTotalIsKeptInStep(Base):
    def test_a_refund_adds_to_the_expense(self):
        e = self.expense(20000)
        self.assertEqual(self.refreshed(e), D('0'))
        self.refund(e, 10000)
        self.refund(e, 5000)
        self.assertEqual(self.refreshed(e), D('15000'))
        self.assertEqual(e.net_base_amount, D('5000'))

    def test_editing_a_refund_updates_the_total(self):
        e = self.expense(20000)
        r = self.refund(e, 10000)
        r.amount = D('12000')
        r.save()
        self.assertEqual(self.refreshed(e), D('12000'))

    def test_moving_a_refund_to_another_expense_moves_the_total(self):
        a, b = self.expense(20000), self.expense(900, description='Taxi')
        r = self.refund(a, 5000)
        r.refund_for_expense = b
        r.save()
        self.assertEqual((self.refreshed(a), self.refreshed(b)), (D('0'), D('5000')))

    def test_unlinking_a_refund_clears_it(self):
        e = self.expense(20000)
        r = self.refund(e, 5000)
        r.refund_for_expense = None
        r.save()
        self.assertEqual(self.refreshed(e), D('0'))

    def test_deleting_a_refund_clears_it(self):
        e = self.expense(20000)
        r = self.refund(e, 5000)
        r.delete()
        self.assertEqual(self.refreshed(e), D('0'))

    def test_a_soft_deleted_refund_does_not_count(self):
        e = self.expense(20000)
        r = self.refund(e, 5000)
        r.is_deleted = True
        r.save()
        self.assertEqual(self.refreshed(e), D('0'))

    def test_editing_the_expense_does_not_overwrite_the_total(self):
        stale = self.expense(20000)                      # loaded before the refund existed
        self.refund(Expense.objects.get(pk=stale.pk), 15000)
        stale.description = 'Trip dinner (edited)'
        stale.save()
        self.assertEqual(self.refreshed(stale), D('15000'))

    def test_the_repair_command_fixes_a_drifted_total(self):
        e = self.expense(20000)
        self.refund(e, 15000)
        Expense.objects.filter(pk=e.pk).update(refunded_base=D('1'))
        out = StringIO()
        call_command('recompute_expense_refunds', stdout=out)
        self.assertEqual(self.refreshed(e), D('1'))      # without --apply it only reports
        self.assertIn('should be 15000', out.getvalue())
        call_command('recompute_expense_refunds', '--apply', stdout=StringIO())
        self.assertEqual(self.refreshed(e), D('15000'))


class TestSavings(Base):
    def test_unlinked_data_is_unchanged(self):
        self.income(50000)
        self.income(300, 'Cashback & Rewards')
        self.expense(20000)
        start, end = self.month()
        r = savings_for_period(self.user, start, end)
        self.assertEqual((r.income, r.operating_expenses, r.savings), (D('50300'), D('20000'), D('30300')))

    def test_the_trip_dinner_only_your_share_is_spent(self):
        e = self.expense(20000)
        self.income(50000)
        self.refund(e, 15000)
        start, end = self.month()
        r = savings_for_period(self.user, start, end)
        self.assertEqual((r.income, r.cashback_refund, r.operating_expenses), (D('50000'), D('0'), D('5000')))
        self.assertEqual(r.savings, D('45000'))

    def test_the_refund_comes_off_the_expense_s_month_not_the_refund_s(self):
        first = today().replace(day=1)
        earlier = first - timedelta(days=5)
        e = self.expense(20000, when=earlier)
        self.income(50000, when=earlier)
        self.refund(e, 15000, when=today())
        parts = monthly_parts(self.user, earlier.replace(day=1), today())
        self.assertEqual(parts[(earlier.year, earlier.month)]['expenses'], D('5000'))
        self.assertEqual(parts[(today().year, today().month)]['income'], D('0'))        # the refund is not income
        self.assertEqual(parts[(today().year, today().month)]['expenses'], D('0'))      # and spends nothing extra
        self.assertEqual(monthly_savings(self.user, earlier.replace(day=1), today())[(earlier.year, earlier.month)].savings,
                         D('45000'))

    def test_a_deleted_expense_releases_its_refund_back_to_income(self):
        e = self.expense(20000)
        refund = self.refund(e, 15000)
        e.delete()
        refund.refresh_from_db()
        self.assertIsNone(refund.refund_for_expense_id)         # the link goes with the expense
        start, end = self.month()
        r = savings_for_period(self.user, start, end)
        self.assertEqual((r.income, r.operating_expenses), (D('15000'), D('0')))

    def test_category_spend_and_budgets_use_the_net_cost(self):
        e = self.expense(20000, category='Dining Out')
        self.expense(1000, category='Food', description='Groceries')
        self.refund(e, 15000)
        by_category, _total = monthly_spend_by_category(self.user, today().year, today().month)
        self.assertEqual(by_category[normalize_category('Dining Out')], D('5000'))
        self.assertEqual(by_category[normalize_category('Food')], D('1000'))

    def test_analytics_facts_use_the_net_cost(self):
        e = self.expense(20000)
        self.income(50000)
        self.refund(e, 15000)
        start, end = self.month()
        summary = load_facts(self.user, start, end).summarize(start, end)
        self.assertEqual((summary.income, summary.spending, summary.saved), (D('50000'), D('5000'), D('45000')))

    def test_the_dashboard_shows_the_net_cost(self):
        e = self.expense(20000)
        self.income(50000)
        self.refund(e, 15000)
        hero = self.client.get(reverse('home')).context['hero_metrics']
        self.assertEqual((hero['saved'], hero['spent']), (D('45000'), D('5000')))

    def test_the_transactions_page_agrees(self):
        e = self.expense(20000)
        self.income(50000)
        self.refund(e, 15000)
        ctx = self.client.get(reverse('all-transactions'), {'time_period': 'all'}).context
        self.assertEqual((ctx['income_amount'], ctx['expense_amount'], ctx['net_remaining']),
                         (D('50000'), D('5000'), D('45000')))

    def test_the_expenses_page_total_is_net(self):
        e = self.expense(20000)
        self.refund(e, 15000)
        self.assertEqual(self.client.get(reverse('expense-list'), {'time_period': 'all'}).context['filtered_amount'], D('5000'))

    def test_the_account_balance_still_moves_by_the_gross_amounts(self):
        e = self.expense(20000)
        self.refund(e, 15000)
        self.account.refresh_from_db()
        self.assertEqual(self.account.balance, D('-5000'))      # -20,000 paid, +15,000 received: the real cash


class TestFormRules(Base):
    def data(self, **extra):
        data = {'date': today().isoformat(), 'amount': '15000', 'currency': '₹', 'account': self.account.pk,
                'source_type': REFUND, 'description': 'friends paid back'}
        data.update(extra)
        return data

    def test_an_own_expense_can_be_linked(self):
        e = self.expense(20000)
        form = IncomeForm(self.data(refund_for_expense=e.pk), user=self.user)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['refund_for_expense'], e)

    def test_another_users_expense_is_refused(self):
        other = User.objects.create_user('xr2', password='x')
        UserProfile.objects.get_or_create(user=other, defaults={'currency': '₹'})
        foreign = self.expense(500, user=other)
        form = IncomeForm(self.data(refund_for_expense=foreign.pk), user=self.user)
        self.assertFalse(form.is_valid())
        self.assertIn('refund_for_expense', form.errors)

    def test_a_deleted_expense_is_refused(self):
        e = self.expense(20000)
        Expense.objects.filter(pk=e.pk).update(is_deleted=True)
        form = IncomeForm(self.data(refund_for_expense=e.pk), user=self.user)
        self.assertFalse(form.is_valid())

    def test_the_link_is_dropped_for_other_source_types(self):
        e = self.expense(20000)
        form = IncomeForm(self.data(source_type='Salary', refund_for_expense=e.pk), user=self.user)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertIsNone(form.cleaned_data['refund_for_expense'])

    def test_one_refund_cannot_pay_back_both_an_expense_and_a_capital_event(self):
        e = self.expense(20000)
        event = CapitalEvent.objects.create(user=self.user, date=today(), amount=D('9'), subtype='other', currency='₹')
        form = IncomeForm(self.data(refund_for_expense=e.pk, refund_for_capital_event=event.pk), user=self.user)
        self.assertFalse(form.is_valid())
        self.assertIn('refund_for_expense', form.errors)

    def test_refunds_cannot_add_up_to_more_than_the_expense_cost(self):
        e = self.expense(20000)
        self.refund(e, 15000)
        form = IncomeForm(self.data(amount='6000', refund_for_expense=e.pk), user=self.user)
        self.assertFalse(form.is_valid())
        self.assertIn('amount', form.errors)
        self.assertIn('5,000.00', form.errors['amount'][0])
        self.assertTrue(IncomeForm(self.data(amount='5000', refund_for_expense=e.pk), user=self.user).is_valid())

    def test_editing_a_refund_does_not_count_it_against_itself(self):
        e = self.expense(20000)
        r = self.refund(e, 20000)
        form = IncomeForm(self.data(amount='20000', refund_for_expense=e.pk), instance=r, user=self.user)
        self.assertTrue(form.is_valid(), form.errors)

    def test_a_form_that_omits_the_field_keeps_an_existing_link(self):
        e = self.expense(20000)
        r = self.refund(e, 15000)
        form = IncomeForm(self.data(), instance=r, user=self.user)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['refund_for_expense'], e)

    def test_saving_through_the_form_updates_the_expense(self):
        e = self.expense(20000)
        form = IncomeForm(self.data(refund_for_expense=e.pk), user=self.user)
        self.assertTrue(form.is_valid(), form.errors)
        form.instance.user = self.user
        form.save()
        self.assertEqual(self.refreshed(e), D('15000'))


class TestPicker(Base):
    """One search over the expenses and the capital events: the latest few without a query, the matches with one."""

    def search(self, q=''):
        return self.client.get(reverse('refund-target-search'), {'q': q}).json()

    def event(self, amount, note='Laptop', when=None, category='', subtype='large_purchase', user=None):
        return CapitalEvent.objects.create(user=user or self.user, date=when or today(), amount=D(str(amount)), note=note,
                                           category=category, subtype=subtype, currency='₹')

    def test_it_opens_on_the_latest_five_of_both_kinds_newest_first(self):
        for i in range(4):
            self.expense(100 + i, when=today() - timedelta(days=i * 2), description=f'Expense {i}')
        for i in range(4):
            self.event(900 + i, note=f'Event {i}', when=today() - timedelta(days=i * 2 + 1))
        data = self.search()
        self.assertTrue(data['recent'])
        self.assertEqual(len(data['results']), RECENT)
        self.assertEqual([(r['kind'], r['description']) for r in data['results']],
                         [('expense', 'Expense 0'), ('event', 'Event 0'), ('expense', 'Expense 1'),
                          ('event', 'Event 1'), ('expense', 'Expense 2')])

    def test_typing_searches_both_kinds(self):
        self.expense(1234, description='Swiggy biryani', category='Dining Out', when=date(2026, 10, 12))
        self.event(86000, note='Laptop purchase', category='Electronics', when=date(2026, 9, 3))
        self.event(500, note='Wedding gift', subtype='gift_given', when=date(2026, 8, 1))
        names = lambda q: [r['description'] for r in self.search(q)['results']]      # noqa: E731
        self.assertEqual(names('swiggy'), ['Swiggy biryani'])
        self.assertEqual(names('laptop'), ['Laptop purchase'])
        self.assertEqual(names('electronics'), ['Laptop purchase'])            # an event's category
        self.assertEqual(names('gift given'), ['Wedding gift'])                # an event's type
        self.assertEqual(names('86,000'), ['Laptop purchase'])                 # amount, commas ignored
        self.assertEqual(names('2026-10'), ['Swiggy biryani'])                 # date
        self.assertEqual(names('purchase 86000'), ['Laptop purchase'])         # every word has to match
        self.assertEqual(names('swiggy laptop'), [])
        self.assertEqual({r['kind'] for r in self.search('2026')['results']}, {'expense', 'event'})

    def test_a_row_says_what_it_is_and_what_is_already_refunded(self):
        e = self.expense(20000)
        self.refund(e, 15000)
        event = self.event(9000, note='Trip stay')
        Income.objects.create(user=self.user, date=today(), amount=D('4000'), source_type=REFUND, currency='₹',
                              account=self.account, refund_for_capital_event=event)
        rows = {r['kind']: r for r in self.search()['results']}
        self.assertEqual((rows['expense']['id'], rows['expense']['refunded']), (e.pk, True))
        self.assertIn('15,000', rows['expense']['refunded_label'])
        self.assertIn('20,000', rows['expense']['amount_label'])
        self.assertEqual((rows['event']['id'], rows['event']['refunded']), (event.pk, True))
        self.assertIn('4,000', rows['event']['refunded_label'])

    def test_an_event_without_a_note_is_titled_by_its_type(self):
        self.event(700, note='', subtype='medical_lump_sum')
        self.assertEqual(self.search()['results'][0]['description'], 'Medical Lump Sum')

    def test_it_only_shows_the_users_own_live_items(self):
        other = User.objects.create_user('xr3', password='x')
        UserProfile.objects.get_or_create(user=other, defaults={'currency': '₹'})
        self.expense(10, description='Mine')
        self.event(11, note='My event')
        self.expense(20, description='Theirs', user=other)
        self.event(21, note='Their event', user=other)
        gone = self.expense(30, description='Deleted')
        Expense.objects.filter(pk=gone.pk).update(is_deleted=True)
        dead = self.event(31, note='Deleted event')
        CapitalEvent.objects.filter(pk=dead.pk).update(is_deleted=True)
        self.assertEqual(sorted(r['description'] for r in self.search()['results']), ['Mine', 'My event'])
        self.assertEqual(self.search('their')['results'], [])

    def test_it_needs_a_login(self):
        self.client.logout()
        self.assertEqual(self.client.get(reverse('refund-target-search')).status_code, 401)

    def test_the_search_takes_two_queries_whatever_it_finds(self):
        for i in range(5):
            self.expense(100 + i, description=f'Item {i}')
            self.event(900 + i, note=f'Item event {i}')
        with self.assertNumQueries(2):          # the expenses, and the events with what is refunded on each
            search_refund_targets(self.user, 'item')


class TestScreens(Base):
    def test_add_refund_opens_the_income_form_linked_to_the_expense(self):
        e = self.expense(20000)
        form = self.client.get(reverse('income-create'), {'refund_for_expense': str(e.uuid)}).context['form']
        self.assertEqual(form.initial['refund_for_expense'], e.pk)
        self.assertEqual(form.initial['source_type'], REFUND)
        self.assertEqual(form.initial['account'], self.account.pk)
        self.assertEqual(form.refund_choice['description'], 'Trip dinner')
        self.assertEqual(form.refund_choice['kind'], 'expense')

    def test_the_picker_starts_on_a_capital_event_opened_from_one(self):
        event = CapitalEvent.objects.create(user=self.user, date=today(), amount=D('20000'), subtype='other', note='Goa trip stay',
                                            currency='₹', account=self.account)
        form = self.client.get(reverse('income-create'), {'refund_for': str(event.uuid)}).context['form']
        self.assertEqual((form.refund_choice['kind'], form.refund_choice['id']), ('event', event.pk))

    def test_nothing_linked_means_no_choice(self):
        self.assertIsNone(self.client.get(reverse('income-create')).context['form'].refund_choice)

    def test_add_refund_ignores_junk_and_other_users_expenses(self):
        other = User.objects.create_user('xr4', password='x')
        UserProfile.objects.get_or_create(user=other, defaults={'currency': '₹'})
        foreign = self.expense(50, user=other)
        for raw in (str(foreign.uuid), 'not-a-uuid', '99999999'):
            page = self.client.get(reverse('income-create'), {'refund_for_expense': raw})
            self.assertEqual(page.status_code, 200)
            self.assertNotIn('refund_for_expense', page.context['form'].initial)

    def test_the_income_form_has_the_picker(self):
        page = self.client.get(reverse('income-create'))
        self.assertContains(page, 'id="refund-search"')
        self.assertContains(page, 'js/refund_picker.js')

    def test_the_form_posts_the_link_exactly_once(self):
        # Two inputs of the same name would be sent together and the empty one would win, quietly dropping the link.
        page = self.client.get(reverse('income-create'), {'refund_for_expense': str(self.expense(20000).uuid)})
        self.assertContains(page, 'name="refund_for_expense"', count=1)
        self.assertContains(page, 'name="refund_for_capital_event"', count=1)

    def test_the_expense_list_shows_refunded_and_net(self):
        e = self.expense(20000)
        self.refund(e, 15000)
        page = self.client.get(reverse('expense-list'), {'time_period': 'all'})
        self.assertContains(page, 'refunded')
        self.assertContains(page, 'Net')
        self.assertContains(page, f'refund_for_expense={e.uuid}', count=2)      # desktop menu and mobile menu

    def test_an_expense_with_no_refund_looks_as_before(self):
        self.expense(20000)
        page = self.client.get(reverse('expense-list'), {'time_period': 'all'})
        self.assertNotContains(page, 'refunded')

    def test_the_edit_page_lists_the_refunds(self):
        e = self.expense(20000)
        self.refund(e, 15000)
        page = self.client.get(reverse('expense-edit', args=[e.uuid]))
        self.assertEqual(len(page.context['refunds']), 1)
        self.assertEqual((page.context['refunded_total'], page.context['net_total']), (D('15000'), D('5000')))
        self.assertContains(page, 'Refunded so far')


class TestTranslated(TestCase):
    """The words of the expense refund picker read in Hindi and Marathi, not English."""

    STRINGS = [
        'Refund for expense', 'Refunded (base currency)', 'Recent', 'Matches', 'No matching expenses or capital events',
        'Not linked to anything', 'Counts as income', 'Unlink this refund', 'Paid back to you against this expense',
        'A refund pays back either a capital event or an expense, not both.',
        'Search your expenses and capital events to link this refund to one. It is then taken off that expense or event, '
        'so its month, category and budget show what it really cost, instead of counting as income.',
        'Nothing has been paid back against this expense yet. If someone repays you for part of it, add it as a refund '
        'and only your own share stays counted.',
        'Refunds cannot add up to more than the expense cost. At most %(left)s can still be refunded against it.',
    ]

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        from django.utils.translation import trans_real

        from expenses.tests.test_translations import compile_catalogues
        compile_catalogues()
        # gettext keeps what it loaded per process: another test may already have read the catalogue before this compile
        trans_real._translations = {}
        trans_real._default = None

    def test_every_string_is_translated_into_devanagari_and_keeps_its_placeholder(self):
        import re

        from django.utils import translation
        for lang in ('hi', 'mr'):
            with translation.override(lang):
                for msg in self.STRINGS:
                    shown = translation.gettext(msg)
                    self.assertNotEqual(shown, msg, (lang, msg))
                    self.assertTrue(re.search('[ऀ-ॿ]', shown), (lang, msg))
                    self.assertEqual('%(left)s' in shown, '%(left)s' in msg, (lang, msg))
