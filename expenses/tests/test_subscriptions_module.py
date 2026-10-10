"""Behavioural tests for Subscriptions (recurring transactions): the date engine, the form, the
posting engine for every transaction type, plan locks, and the list / create / edit / delete views."""

import calendar
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

from dateutil.relativedelta import relativedelta
from django.contrib.auth.models import User
from django.contrib.messages import get_messages
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from expenses.forms import RecurringTransactionForm
from expenses.models import (
    Account,
    CapitalEvent,
    Category,
    Expense,
    FXRate,
    Income,
    Loan,
    LoanInterestRate,
    LoanRepayment,
    PhysicalAsset,
    RecurringTransaction,
    Transfer,
    UserProfile,
)
from expenses.recurring_utils import (
    calculate_recurring_equivalents,
    get_recurring_month_occurrence_amount,
)
from expenses.views.mixins import process_user_recurring_transactions
from finance_tracker.plans import get_limit

D = Decimal
FREQUENCIES = [value for value, _label in RecurringTransaction.FREQUENCY_CHOICES]


def today():
    return timezone.localdate()


def seed_fx():
    for (src, dst), rate in {('USD', 'INR'): '80', ('INR', 'USD'): '0.0125'}.items():
        FXRate.objects.update_or_create(from_currency=src, to_currency=dst, as_of_date=today(),
                                        defaults={'rate': D(rate), 'source': 'test'})
    cache.clear()


class SubBase(TestCase):
    tier = 'PRO'

    def setUp(self):
        self.user = self.make_user('sub-user', self.tier)
        self.client.force_login(self.user)
        self.cash = Account.objects.create(user=self.user, name='Cash', account_type='CASH_WALLET',
                                           balance=D('100000.00'), currency='₹')
        self.bank = Account.objects.create(user=self.user, name='Bank', account_type='SAVINGS_ACCOUNT',
                                           balance=D('100000.00'), currency='₹')
        for name in ('Food', 'Bills'):
            Category.objects.get_or_create(user=self.user, name=name)
        cache.clear()

    def make_user(self, name, tier='PRO', currency='₹'):
        user = User.objects.create_user(username=name, password='pass')
        profile, _ = UserProfile.objects.get_or_create(user=user, defaults={'currency': currency})
        profile.tier = tier
        profile.currency = currency
        profile.save()
        user.refresh_from_db()
        return user

    def rt(self, transaction_type='EXPENSE', amount='100', frequency='MONTHLY', start=None, user=None, **kw):
        values = dict(user=user or self.user, transaction_type=transaction_type, amount=D(str(amount)), currency='₹',
                      description=kw.pop('description', 'Netflix'), frequency=frequency,
                      start_date=start or today(), account=self.cash)
        if transaction_type == 'EXPENSE':
            values.setdefault('category', 'Food')
        if transaction_type == 'INCOME':
            values.setdefault('source', 'Salary')
        values.update(kw)
        return RecurringTransaction.objects.create(**values)

    def bal(self, account):
        account.refresh_from_db()
        return account.balance

    def run_engine(self, **kw):
        process_user_recurring_transactions(self.user, force=True, **kw)

    def messages(self, response):
        return [str(m) for m in get_messages(response.wsgi_request)]


# ---------------------------------------------------------------------------
# Date engine
# ---------------------------------------------------------------------------
class TestNextDate(TestCase):
    nxt = staticmethod(RecurringTransaction.get_next_date)

    def sequence(self, start, frequency, count=6, **flags):
        out, cur = [], start
        for _ in range(count):
            cur = self.nxt(cur, frequency, start, **flags)
            out.append(cur)
        return out

    def test_day_based_frequencies(self):
        start = date(2026, 1, 1)
        self.assertEqual(self.sequence(start, 'DAILY', 3), [date(2026, 1, 2), date(2026, 1, 3), date(2026, 1, 4)])
        self.assertEqual(self.sequence(start, 'WEEKLY', 2), [date(2026, 1, 8), date(2026, 1, 15)])
        self.assertEqual(self.sequence(start, 'BIWEEKLY', 3), [date(2026, 1, 15), date(2026, 1, 29), date(2026, 2, 12)])

    def test_monthly_keeps_the_day(self):
        self.assertEqual(self.sequence(date(2026, 1, 15), 'MONTHLY', 3),
                         [date(2026, 2, 15), date(2026, 3, 15), date(2026, 4, 15)])

    def test_monthly_from_the_31st_clamps_then_recovers(self):
        self.assertEqual(self.sequence(date(2026, 1, 31), 'MONTHLY', 5),
                         [date(2026, 2, 28), date(2026, 3, 31), date(2026, 4, 30), date(2026, 5, 31), date(2026, 6, 30)])

    def test_monthly_across_a_year_boundary(self):
        self.assertEqual(self.sequence(date(2026, 11, 20), 'MONTHLY', 3),
                         [date(2026, 12, 20), date(2027, 1, 20), date(2027, 2, 20)])

    def test_quarterly_from_the_31st_never_skips_a_quarter(self):
        """Regression: Jan 31 used to jump to Jul 31 because April has no 31st."""
        self.assertEqual(self.sequence(date(2026, 1, 31), 'QUARTERLY', 5),
                         [date(2026, 4, 30), date(2026, 7, 31), date(2026, 10, 31), date(2027, 1, 31), date(2027, 4, 30)])

    def test_semiannual_from_the_31st_stays_semiannual(self):
        """Regression: Aug 31 used to become yearly because February has no 31st."""
        self.assertEqual(self.sequence(date(2026, 8, 31), 'SEMIANNUALLY', 4),
                         [date(2027, 2, 28), date(2027, 8, 31), date(2028, 2, 29), date(2028, 8, 31)])

    def test_quarterly_and_semiannual_regular_days(self):
        self.assertEqual(self.sequence(date(2026, 1, 15), 'QUARTERLY', 3),
                         [date(2026, 4, 15), date(2026, 7, 15), date(2026, 10, 15)])
        self.assertEqual(self.sequence(date(2026, 3, 10), 'SEMIANNUALLY', 3),
                         [date(2026, 9, 10), date(2027, 3, 10), date(2027, 9, 10)])

    def test_yearly_including_leap_day(self):
        self.assertEqual(self.sequence(date(2026, 6, 5), 'YEARLY', 3), [date(2027, 6, 5), date(2028, 6, 5), date(2029, 6, 5)])
        self.assertEqual(self.sequence(date(2024, 2, 29), 'YEARLY', 4),
                         [date(2025, 2, 28), date(2026, 2, 28), date(2027, 2, 28), date(2028, 2, 29)])

    def test_last_day_of_month(self):
        self.assertEqual(self.sequence(date(2026, 1, 31), 'MONTHLY', 3, is_last_day_of_month=True),
                         [date(2026, 2, 28), date(2026, 3, 31), date(2026, 4, 30)])

    def test_last_working_day_of_month(self):
        got = self.sequence(date(2026, 1, 30), 'MONTHLY', 4, is_last_working_day=True)
        self.assertEqual(got, [date(2026, 2, 27), date(2026, 3, 31), date(2026, 4, 30), date(2026, 5, 29)])
        for d in got:
            self.assertLess(d.weekday(), 5)

    def test_last_working_day_happens_exactly_once_per_month(self):
        """Regression: every weekday among the last three days matched, so bills posted 2-3 times a month."""
        seq = self.sequence(date(2026, 1, 30), 'MONTHLY', 36, is_last_working_day=True)
        months = [(d.year, d.month) for d in seq]
        self.assertEqual(len(months), len(set(months)), 'two occurrences in one month')
        for d in seq:
            last_day = calendar.monthrange(d.year, d.month)[1]
            self.assertLess(d.weekday(), 5)
            self.assertTrue(all(date(d.year, d.month, x).weekday() >= 5 for x in range(d.day + 1, last_day + 1)),
                            f'{d} is not the last weekday of its month')

    def test_result_is_always_strictly_after_current_and_never_before_start(self):
        for frequency in FREQUENCIES:
            for start in (date(2026, 1, 1), date(2026, 1, 29), date(2026, 1, 31), date(2024, 2, 29), date(2026, 12, 31)):
                cur = start
                for _ in range(30):
                    nxt = self.nxt(cur, frequency, start)
                    with self.subTest(frequency=frequency, start=start, cur=cur):
                        self.assertGreater(nxt, cur)
                        self.assertGreaterEqual(nxt, start)
                    cur = nxt

    def test_month_step_frequencies_have_no_drift_after_many_cycles(self):
        start = date(2026, 1, 31)
        cur = start
        for _ in range(48):
            cur = self.nxt(cur, 'MONTHLY', start)
        self.assertEqual(cur, date(2030, 1, 31))
        cur = start
        for _ in range(16):
            cur = self.nxt(cur, 'QUARTERLY', start)
        self.assertEqual(cur, date(2030, 1, 31))

    def test_asking_from_before_the_start_returns_the_start(self):
        self.assertEqual(self.nxt(date(2026, 1, 1), 'MONTHLY', date(2026, 3, 15)), date(2026, 3, 15))
        self.assertEqual(self.nxt(date(2026, 1, 1), 'QUARTERLY', date(2026, 3, 15)), date(2026, 3, 15))


class TestScheduleModel(SubBase):
    def test_first_due_date_is_the_start_date(self):
        rt = self.rt(start=today() + timedelta(days=5))
        self.assertEqual(rt.next_due_date, today() + timedelta(days=5))

    def test_next_due_follows_the_last_processed_date(self):
        rt = self.rt(start=date(2026, 1, 15), last_processed_date=date(2026, 3, 15))
        self.assertEqual(rt.next_due_date, date(2026, 4, 15))

    def test_next_due_is_none_after_the_end_date(self):
        rt = self.rt(start=date(2026, 1, 15), last_processed_date=date(2026, 3, 15), end_date=date(2026, 4, 1))
        self.assertIsNone(rt.next_due_date)
        rt = self.rt(start=date(2026, 1, 15), last_processed_date=date(2026, 3, 15), end_date=date(2026, 4, 15), description='b')
        self.assertEqual(rt.next_due_date, date(2026, 4, 15))

    def test_a_last_processed_date_before_the_start_is_ignored(self):
        rt = self.rt(start=date(2026, 5, 1), last_processed_date=date(2026, 1, 1))
        self.assertEqual(rt.next_due_date, date(2026, 5, 1))

    def test_partial_saves_still_refresh_next_due_and_currency_columns(self):
        seed_fx()
        rt = self.rt(start=date(2026, 1, 1), last_processed_date=date(2026, 1, 1))
        rt.currency = '$'
        rt.amount = D('10')
        rt.last_processed_date = date(2026, 2, 1)
        rt.save(update_fields=['currency', 'amount', 'last_processed_date'])
        rt.refresh_from_db()
        self.assertEqual(rt.next_due_date, date(2026, 3, 1))
        self.assertEqual((rt.exchange_rate, rt.base_amount), (D('80'), D('800.00')))

    def test_same_currency_base_amount(self):
        rt = self.rt(amount='99.99')
        self.assertEqual((rt.exchange_rate, rt.base_amount), (D('1.0'), D('99.99')))

    def test_foreign_currency_base_amount(self):
        seed_fx()
        rt = self.rt(amount='10', currency='$')
        self.assertEqual((rt.exchange_rate, rt.base_amount), (D('80'), D('800.00')))

    def test_category_and_source_are_stripped(self):
        rt = self.rt(category='  Food ', source='  Salary ')
        self.assertEqual((rt.category, rt.source), ('Food', 'Salary'))

    def test_database_rule_blocks_identical_active_schedules_but_not_inactive_ones(self):
        from django.db import IntegrityError, transaction
        self.rt()
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.rt(account=self.bank)         # the DB rule ignores the account
        self.rt(is_active=False)               # inactive duplicates are fine

    def test_equivalents_for_every_frequency(self):
        expected = {
            'DAILY': (D('3000'), D('36500')), 'WEEKLY': (D('400'), D('5200')), 'BIWEEKLY': (D('200'), D('2600')),
            'MONTHLY': (D('100'), D('1200')), 'QUARTERLY': (D('100') / 3, D('400')),
            'SEMIANNUALLY': (D('100') / 6, D('200')), 'YEARLY': (D('100') / 12, D('100')),
        }
        for frequency, (monthly, yearly) in expected.items():
            self.assertEqual(calculate_recurring_equivalents(frequency, D('100')), (monthly, yearly), frequency)
        self.assertEqual(calculate_recurring_equivalents('MONTHLY', None), (D('0.00'), D('0.00')))

    def test_equivalent_properties_use_the_base_amount(self):
        seed_fx()
        rt = self.rt(amount='10', currency='$', frequency='QUARTERLY')
        self.assertEqual(rt.yearly_equivalent, D('800.00') * 4)
        self.assertEqual(rt.monthly_equivalent, D('800.00') / 3)

    def test_forecast_month_amounts(self):
        mk = lambda freq, start, end=None, amount='100': RecurringTransaction(
            frequency=freq, base_amount=D(amount), start_date=start, end_date=end)
        month = lambda rt, y, m: get_recurring_month_occurrence_amount(rt, y, m)
        self.assertEqual(month(mk('MONTHLY', date(2026, 3, 5)), 2026, 4), D('100'))
        self.assertEqual(month(mk('MONTHLY', date(2026, 3, 5)), 2026, 2), D('0'))          # before it starts
        self.assertEqual(month(mk('MONTHLY', date(2026, 1, 5), date(2026, 3, 1)), 2026, 4), D('0'))  # after it ended
        self.assertEqual(month(mk('QUARTERLY', date(2026, 1, 5)), 2026, 4), D('100'))
        self.assertEqual(month(mk('QUARTERLY', date(2026, 1, 5)), 2026, 5), D('0'))
        self.assertEqual(month(mk('SEMIANNUALLY', date(2026, 1, 5)), 2026, 7), D('100'))
        self.assertEqual(month(mk('SEMIANNUALLY', date(2026, 1, 5)), 2026, 4), D('0'))
        self.assertEqual(month(mk('YEARLY', date(2025, 6, 5)), 2026, 6), D('100'))
        self.assertEqual(month(mk('YEARLY', date(2025, 6, 5)), 2026, 7), D('0'))
        self.assertEqual(month(mk('WEEKLY', date(2026, 10, 1)), 2026, 10), D('500'))       # 1, 8, 15, 22, 29
        self.assertEqual(month(mk('WEEKLY', date(2026, 10, 1), date(2026, 10, 15)), 2026, 10), D('300'))
        self.assertEqual(month(mk('BIWEEKLY', date(2026, 10, 1)), 2026, 10), D('300'))     # 1, 15, 29
        self.assertEqual(month(mk('DAILY', date(2026, 1, 1)), 2026, 10), D('3000'))        # flat 30-day month
        self.assertEqual(month(mk('MONTHLY', date(2026, 1, 1), amount='0'), 2026, 10), D('0'))


# ---------------------------------------------------------------------------
# Form
# ---------------------------------------------------------------------------
class TestScheduleForm(SubBase):
    def data(self, **overrides):
        data = {'transaction_type': 'EXPENSE', 'amount': '499', 'currency': '₹', 'description': 'Netflix',
                'category': 'Food', 'frequency': 'MONTHLY', 'start_date': today().isoformat(),
                'payment_method': 'UPI', 'account': str(self.cash.id), 'is_active': 'on'}
        data.update(overrides)
        return data

    def form(self, user=None, instance=None, **overrides):
        return RecurringTransactionForm(self.data(**overrides), user=user or self.user, instance=instance)

    def assertError(self, field, **overrides):
        form = self.form(**overrides)
        self.assertFalse(form.is_valid())
        self.assertIn(field, form.errors, dict(form.errors))

    def test_valid_expense(self):
        self.assertTrue(self.form().is_valid())

    def test_required_fields(self):
        form = RecurringTransactionForm({}, user=self.user)
        self.assertFalse(form.is_valid())
        for field in ('transaction_type', 'amount', 'description', 'frequency', 'start_date'):
            self.assertIn(field, form.errors)

    def test_amount_must_be_positive(self):
        for bad in ('0', '-5', 'abc'):
            self.assertError('amount', amount=bad)

    def test_expense_needs_a_category(self):
        self.assertError('category', category='')

    def test_income_needs_a_source_not_a_category(self):
        self.assertError('source', transaction_type='INCOME', category='', source='')
        self.assertTrue(self.form(transaction_type='INCOME', category='', source='Salary').is_valid())

    def test_transfer_rules(self):
        base = dict(transaction_type='TRANSFER', category='', from_account=str(self.cash.id), to_account=str(self.bank.id))
        self.assertTrue(self.form(**base).is_valid())
        self.assertError('from_account', **{**base, 'from_account': ''})
        self.assertError('to_account', **{**base, 'to_account': ''})
        self.assertError('to_account', **{**base, 'to_account': str(self.cash.id)})

    def test_loan_repayment_rules(self):
        loan = Loan.objects.create(user=self.user, name='L', loan_type='PERSONAL', initial_principal=1000,
                                   duration_months=12, start_date=today(), is_active=True)
        base = dict(transaction_type='LOAN', category='', loan=str(loan.id))
        self.assertTrue(self.form(**base).is_valid())
        self.assertError('loan', **{**base, 'loan': ''})
        self.assertError('account', **{**base, 'account': ''})

    def test_insurance_premium_needs_a_payment_account(self):
        self.assertError('account', transaction_type='INSURANCE_PREMIUM', category='', account='')

    def test_capital_event_needs_a_subtype(self):
        self.assertError('capital_subtype', transaction_type='CAPITAL', category='')
        self.assertTrue(self.form(transaction_type='CAPITAL', category='', capital_subtype='large_purchase').is_valid())

    def test_dates(self):
        self.assertError('end_date', end_date=(today() - timedelta(days=1)).isoformat())
        self.assertTrue(self.form(end_date=today().isoformat()).is_valid())
        self.assertError('start_date', start_date='1999-12-31')
        self.assertError('start_date', start_date=(today() + relativedelta(years=51)).isoformat())
        self.assertTrue(self.form(start_date=(today() + relativedelta(years=10)).isoformat()).is_valid())

    def test_frequency_and_payment_method_choices(self):
        self.assertError('frequency', frequency='HOURLY')
        self.assertError('payment_method', payment_method='Bitcoin')
        blank = self.form(payment_method='')
        self.assertTrue(blank.is_valid(), dict(blank.errors))
        self.assertEqual(blank.cleaned_data['payment_method'], 'Cash')
        for frequency in FREQUENCIES:
            self.assertTrue(self.form(frequency=frequency).is_valid(), frequency)

    def test_duplicate_rule_matches_the_database_rule(self):
        self.rt(amount='499', description='Netflix', start=today(), category='Food')
        form = self.form()
        self.assertFalse(form.is_valid())
        self.assertTrue(form.non_field_errors())
        # Same details on another account is ALSO a duplicate: the database would reject it
        self.assertFalse(self.form(account=str(self.bank.id)).is_valid())
        # Any differing field of the rule is fine
        self.assertTrue(self.form(amount='500').is_valid())
        self.assertTrue(self.form(description='Hotstar').is_valid())
        self.assertTrue(self.form(frequency='YEARLY').is_valid())
        self.assertTrue(self.form(start_date=(today() + timedelta(days=1)).isoformat()).is_valid())
        self.assertTrue(self.form(is_active='').is_valid())     # inactive duplicates are allowed

    def test_editing_a_schedule_is_not_a_duplicate_of_itself(self):
        rt = self.rt(amount='499', description='Netflix', start=today())
        self.assertTrue(self.form(instance=rt).is_valid())

    def test_other_users_data_is_never_offered(self):
        other = self.make_user('other-form')
        theirs = Account.objects.create(user=other, name='Theirs', account_type='CASH_WALLET', balance=0, currency='₹')
        their_loan = Loan.objects.create(user=other, name='X', loan_type='PERSONAL', initial_principal=1, duration_months=1,
                                         start_date=today(), is_active=True)
        self.assertError('account', account=str(theirs.id))
        self.assertError('loan', transaction_type='LOAN', category='', loan=str(their_loan.id))

    def test_only_insurance_policies_are_offered_for_premiums(self):
        policy = PhysicalAsset.objects.create(user=self.user, name='Term', asset_class='INSURANCE', currency='₹')
        PhysicalAsset.objects.create(user=self.user, name='Car', asset_class='VEHICLE', currency='₹')
        names = set(RecurringTransactionForm(user=self.user).fields['physical_asset'].queryset.values_list('name', flat=True))
        self.assertEqual(names, {policy.name})

    def test_loan_and_insurance_schedules_cannot_change_type(self):
        loan = Loan.objects.create(user=self.user, name='L', loan_type='PERSONAL', initial_principal=1000,
                                   duration_months=12, start_date=today(), is_active=True)
        rt = self.rt('LOAN', loan=loan, description='EMI')
        form = RecurringTransactionForm(instance=rt, user=self.user)
        self.assertTrue(form.fields['transaction_type'].disabled)

    def test_free_tier_only_offers_unlocked_accounts(self):
        free = self.make_user('free-sub', tier='FREE')
        limit = get_limit('FREE', 'accounts')
        accounts = [Account.objects.create(user=free, name=f'A{i}', account_type='CASH_WALLET', balance=0, currency='₹')
                    for i in range(limit + 1)]
        offered = set(RecurringTransactionForm(user=free).fields['account'].queryset.values_list('id', flat=True))
        self.assertEqual(offered, {a.id for a in accounts[:limit]})

    # -- save() semantics -------------------------------------------------
    def saved(self, **overrides):
        form = self.form(**overrides)
        self.assertTrue(form.is_valid(), dict(form.errors))
        instance = form.save(commit=False)
        instance.user = self.user
        instance.save()
        return instance

    def test_month_end_flags_are_honoured_when_skipping_the_past(self):
        from expenses.services_recurring import RecurringService
        with patch('expenses.services_recurring.timezone.localdate', return_value=date(2026, 8, 31)):
            self.assertEqual(RecurringService.last_due_before_today(date(2026, 1, 30), 'MONTHLY', False, True), date(2026, 8, 31))
            self.assertEqual(RecurringService.last_due_before_today(date(2026, 1, 15), 'MONTHLY', True, False), date(2026, 8, 31))
        with patch('expenses.services_recurring.timezone.localdate', return_value=date(2026, 8, 30)):
            # Aug 30 is a Sunday; the last working day (Mon 31st) has not happened yet
            self.assertEqual(RecurringService.last_due_before_today(date(2026, 1, 30), 'MONTHLY', False, True), date(2026, 7, 31))
        with patch('expenses.services_recurring.timezone.localdate', return_value=date(2026, 1, 10)):
            self.assertIsNone(RecurringService.last_due_before_today(date(2026, 1, 5), 'MONTHLY', True, False))

    def test_new_future_schedule_has_nothing_processed(self):
        rt = self.saved(start_date=(today() + timedelta(days=9)).isoformat())
        self.assertIsNone(rt.last_processed_date)
        self.assertEqual(rt.next_due_date, today() + timedelta(days=9))

    def test_new_past_schedule_without_history_starts_from_the_next_due_date(self):
        start = today() - relativedelta(months=3, days=4)
        rt = self.saved(start_date=start.isoformat())
        self.assertIsNotNone(rt.last_processed_date)
        self.assertGreater(rt.next_due_date, today())

    def test_new_past_schedule_with_history_keeps_every_occurrence_due(self):
        start = today() - relativedelta(months=3)
        rt = self.saved(start_date=start.isoformat(), create_historical_entries='on')
        self.assertIsNone(rt.last_processed_date)
        self.assertEqual(rt.next_due_date, start)

    def test_history_checkbox_posts_the_past_when_committing(self):
        start = today() - relativedelta(months=2)
        form = self.form(start_date=start.isoformat(), create_historical_entries='on')
        self.assertTrue(form.is_valid())
        instance = form.save(commit=False)
        instance.user = self.user
        form.save()  # commit=True path (processes immediately)
        self.assertGreaterEqual(Expense.objects.filter(user=self.user, description='Netflix (Recurring)').count(), 2)

    def test_editing_the_schedule_never_reposts_history(self):
        """Regression: changing the frequency of an old schedule back-posted months of expenses."""
        rt = self.saved(start_date=(today() - timedelta(days=100)).isoformat())
        before = Expense.objects.count()
        form = self.form(instance=rt, frequency='WEEKLY', start_date=(today() - timedelta(days=100)).isoformat())
        self.assertTrue(form.is_valid(), dict(form.errors))
        edited = form.save()
        self.run_engine()
        self.assertEqual(Expense.objects.count(), before)
        self.assertGreater(edited.next_due_date, today())

    def test_editing_without_schedule_changes_leaves_progress_alone(self):
        rt = self.saved(start_date=(today() - timedelta(days=100)).isoformat())
        progress = rt.last_processed_date
        edited = self.form(instance=rt, amount='555', start_date=(today() - timedelta(days=100)).isoformat()).save()
        self.assertEqual(edited.last_processed_date, progress)

    def test_moving_a_schedule_into_the_future_resets_it(self):
        rt = self.saved(start_date=(today() - timedelta(days=100)).isoformat())
        edited = self.form(instance=rt, start_date=(today() + timedelta(days=7)).isoformat()).save()
        self.assertIsNone(edited.last_processed_date)
        self.assertEqual(edited.next_due_date, today() + timedelta(days=7))


# ---------------------------------------------------------------------------
# Posting engine
# ---------------------------------------------------------------------------
class TestPostingEngine(SubBase):
    def test_expense_is_posted_with_every_field(self):
        self.rt(amount='499', start=today(), payment_method='UPI', category='Bills', description='Airtel')
        self.run_engine()
        e = Expense.objects.get()
        self.assertEqual((e.description, e.amount, e.category, e.payment_method, e.account, e.date, e.currency),
                         ('Airtel (Recurring)', D('499.00'), 'Bills', 'UPI', self.cash, today(), '₹'))
        self.assertEqual(self.bal(self.cash), D('99501.00'))

    def test_expense_without_a_category_is_uncategorized_and_insurance_gets_its_own(self):
        self.rt(category=None, description='Misc')
        self.rt('INSURANCE_PREMIUM', description='Term premium', amount='1200', category=None)
        self.run_engine()
        self.assertEqual(Expense.objects.get(description='Misc (Recurring)').category, 'Uncategorized')
        self.assertEqual(Expense.objects.get(description='Term premium (Recurring)').category, 'Insurance')

    def test_income_is_posted_and_credits_the_account(self):
        self.rt('INCOME', amount='50000', source='Salary', description='Pay')
        self.run_engine()
        i = Income.objects.get()
        self.assertEqual((i.amount, i.source, i.source_type, i.account, i.date), (D('50000.00'), 'Salary', 'Salary', self.cash, today()))
        self.assertEqual(self.bal(self.cash), D('150000.00'))

    def test_income_keeps_the_matching_source_type(self):
        """Regression: every recurring income used to be posted as 'Salary'."""
        for index, source_type in enumerate(('Rental Income', 'Cashback & Rewards', 'Business', 'Investment Returns')):
            self.rt('INCOME', amount=str(100 + index), source=source_type, description=f'd{index}')
        self.run_engine()
        got = {i.source: i.source_type for i in Income.objects.all()}
        self.assertEqual(got, {'Rental Income': 'Rental Income', 'Cashback & Rewards': 'Cashback & Rewards',
                               'Business': 'Business', 'Investment Returns': 'Investment Returns'})

    def test_income_with_a_free_text_source_is_typed_other(self):
        self.rt('INCOME', source='Uncle Ravi', description='gift')
        self.run_engine()
        i = Income.objects.get()
        self.assertEqual((i.source, i.source_type), ('Uncle Ravi', 'Other'))

    def test_transfer_moves_money_between_accounts(self):
        self.rt('TRANSFER', amount='2000', account=None, from_account=self.cash, to_account=self.bank, description='SIP')
        self.run_engine()
        t = Transfer.objects.get()
        self.assertEqual((t.amount, t.from_account, t.to_account, t.description), (D('2000.00'), self.cash, self.bank, 'SIP (Recurring)'))
        self.assertEqual(self.bal(self.cash), D('98000.00'))
        self.assertEqual(self.bal(self.bank), D('102000.00'))

    def test_transfer_missing_an_account_is_deactivated_not_posted(self):
        rt = self.rt('TRANSFER', account=None, from_account=self.cash, to_account=None)
        self.run_engine()
        rt.refresh_from_db()
        self.assertFalse(rt.is_active)
        self.assertEqual(Transfer.objects.count(), 0)

    def test_capital_event_is_posted_with_its_flags(self):
        self.rt('CAPITAL', amount='9000', capital_subtype='medical_lump_sum', exclude_from_averages=False,
                exclude_from_budget=False, include_in_net_worth=False, description='Surgery', category=None)
        self.run_engine()
        c = CapitalEvent.objects.get()
        self.assertEqual((c.amount, c.subtype, c.note, c.account, c.exclude_from_averages, c.exclude_from_budget,
                          c.include_in_net_worth), (D('9000.00'), 'medical_lump_sum', 'Surgery (Recurring)', self.cash,
                                                    False, False, False))

    def test_foreign_currency_schedule_is_converted(self):
        seed_fx()
        self.rt(amount='10', currency='$', description='SaaS')
        self.run_engine()
        e = Expense.objects.get()
        self.assertEqual((e.currency, e.base_amount, e.exchange_rate), ('$', D('800.00'), D('80')))
        self.assertEqual(self.bal(self.cash), D('99200.00'))

    def test_failed_rate_lookup_skips_the_schedule_without_deactivating_it(self):
        rt = self.rt(amount='10', currency='$', description='SaaS')
        with patch('expenses.views.mixins.get_exchange_rate', side_effect=RuntimeError('fx down')):
            self.run_engine()
        rt.refresh_from_db()
        self.assertTrue(rt.is_active)
        self.assertEqual(Expense.objects.count(), 0)

    def test_catch_up_posts_every_missed_occurrence_once(self):
        start = today() - relativedelta(months=3)
        rt = self.rt(start=start)
        expected = 0
        due = start
        while due <= today():
            expected += 1
            due = RecurringTransaction.get_next_date(due, 'MONTHLY', start)
        self.run_engine()
        self.assertEqual(Expense.objects.count(), expected)
        self.assertEqual(sorted(Expense.objects.values_list('date', flat=True))[0], start)
        rt.refresh_from_db()
        self.assertGreater(rt.next_due_date, today())

    def test_running_twice_never_posts_twice(self):
        self.rt(start=today() - relativedelta(months=2))
        self.run_engine()
        count = Expense.objects.count()
        self.run_engine()
        self.assertEqual(Expense.objects.count(), count)

    def test_last_working_day_schedule_posts_once_a_month(self):
        start = date(today().year - 1, 1, 30)
        rt = self.rt(start=start, is_last_working_day=True, description='Rent')
        self.run_engine()
        months = [(e.date.year, e.date.month) for e in Expense.objects.all()]
        self.assertEqual(len(months), len(set(months)))
        self.assertGreaterEqual(len(months), 12)

    def test_month_end_schedule_does_not_post_both_the_start_date_and_month_end(self):
        start = date(today().year - 1, 1, 30)           # a Thursday; Jan 2025's last working day is Fri 31st
        rt = self.rt(start=start, is_last_working_day=True, description='Rent')
        self.assertEqual(rt.first_due_date(), date(start.year, 1, 31))
        self.assertEqual(rt.next_due_date, date(start.year, 1, 31))
        self.run_engine()
        self.assertEqual(Expense.objects.order_by('date').first().date, date(start.year, 1, 31))

    def test_a_manual_entry_for_the_same_day_is_not_duplicated(self):
        Expense.objects.create(user=self.user, date=today(), amount=D('100'), description='Netflix (Recurring)',
                               category='Food', currency='₹')
        rt = self.rt(start=today())
        self.run_engine()
        self.assertEqual(Expense.objects.count(), 1)
        rt.refresh_from_db()
        self.assertEqual(rt.last_processed_date, today())

    def test_future_start_is_not_posted(self):
        self.rt(start=today() + timedelta(days=3))
        self.run_engine()
        self.assertEqual(Expense.objects.count(), 0)

    def test_end_date_stops_posting_and_deactivates(self):
        start = today() - relativedelta(months=3)
        rt = self.rt(start=start, end_date=start + relativedelta(months=1, days=2))
        self.run_engine()
        self.assertEqual(Expense.objects.count(), 2)
        rt.refresh_from_db()
        self.assertFalse(rt.is_active)
        self.assertIsNone(rt.next_due_date)

    def test_a_schedule_already_past_its_end_date_is_deactivated(self):
        rt = self.rt(start=today() - relativedelta(months=3), last_processed_date=today() - relativedelta(months=1),
                     end_date=today() - relativedelta(months=2))
        self.run_engine()
        rt.refresh_from_db()
        self.assertFalse(rt.is_active)
        self.assertEqual(Expense.objects.count(), 0)

    def test_inactive_schedules_are_never_posted(self):
        self.rt(is_active=False)
        self.run_engine()
        self.assertEqual(Expense.objects.count(), 0)

    def test_an_inactive_account_deactivates_the_schedule(self):
        rt = self.rt()
        Account.objects.filter(pk=self.cash.pk).update(is_active=False)
        self.run_engine()
        rt.refresh_from_db()
        self.assertFalse(rt.is_active)
        self.assertEqual(Expense.objects.count(), 0)

    def test_catch_up_is_capped_per_run_and_resumes(self):
        self.rt(frequency='DAILY', start=today() - timedelta(days=9))
        self.run_engine(max_catchup=4)
        self.assertEqual(Expense.objects.count(), 4)
        self.run_engine(max_catchup=4)
        self.assertEqual(Expense.objects.count(), 8)
        self.run_engine(max_catchup=4)
        self.assertEqual(Expense.objects.count(), 10)       # 10 days: today inclusive

    def test_only_the_users_own_schedules_are_processed(self):
        other = self.make_user('other-engine')
        acc = Account.objects.create(user=other, name='C', account_type='CASH_WALLET', balance=100, currency='₹')
        RecurringTransaction.objects.create(user=other, transaction_type='EXPENSE', amount=D('5'), currency='₹',
                                            description='Theirs', frequency='MONTHLY', start_date=today(),
                                            account=acc, category='Food')
        self.run_engine()
        self.assertEqual(Expense.objects.count(), 0)

    def test_anonymous_users_are_ignored(self):
        from django.contrib.auth.models import AnonymousUser
        self.assertIsNone(process_user_recurring_transactions(AnonymousUser(), force=True))

    def test_plan_quota_only_processes_the_oldest_active_schedules(self):
        free = self.make_user('free-engine', tier='FREE')
        limit = get_limit('FREE', 'recurring_transactions')
        acc = Account.objects.create(user=free, name='C', account_type='CASH_WALLET', balance=1000, currency='₹')
        Category.objects.get_or_create(user=free, name='Food')
        for i in range(limit + 1):
            RecurringTransaction.objects.create(user=free, transaction_type='EXPENSE', amount=D('5'), currency='₹',
                                                description=f'S{i}', frequency='MONTHLY', start_date=today(),
                                                account=acc, category='Food')
        process_user_recurring_transactions(free, force=True)
        self.assertEqual(sorted(Expense.objects.filter(user=free).values_list('description', flat=True)),
                         [f'S{i} (Recurring)' for i in range(limit)])


class TestLoanSchedules(SubBase):
    def make_loan(self, principal='100000', rate='12'):
        loan = Loan.objects.create(user=self.user, name='Car', loan_type='CAR', initial_principal=D(principal),
                                   duration_months=24, start_date=today() - relativedelta(months=6), is_active=True)
        LoanInterestRate.objects.create(loan=loan, interest_rate=D(rate), effective_date=loan.start_date)
        return loan

    def test_emi_is_split_into_interest_and_principal(self):
        loan = self.make_loan()
        self.rt('LOAN', amount='5000', loan=loan, description='EMI')
        self.run_engine()
        r = LoanRepayment.objects.get()
        # 12% a year on 100,000 for a 30-day period = 986.30
        self.assertEqual((r.interest_portion, r.principal_portion, r.amount, r.from_account, r.date),
                         (D('986.30'), D('4013.70'), D('5000.00'), self.cash, today()))
        self.assertEqual(self.bal(self.cash), D('95000.00'))

    def test_interest_is_charged_on_the_reducing_balance(self):
        loan = self.make_loan()
        self.rt('LOAN', amount='5000', loan=loan, start=today() - relativedelta(months=2), description='EMI')
        self.run_engine()
        repayments = list(LoanRepayment.objects.filter(loan=loan).order_by('date'))
        self.assertGreaterEqual(len(repayments), 3)
        self.assertEqual(repayments[0].interest_portion, D('986.30'))
        self.assertLess(repayments[1].interest_portion, repayments[0].interest_portion)
        self.assertLess(repayments[2].interest_portion, repayments[1].interest_portion)
        paid = sum(r.principal_portion for r in repayments)
        self.assertEqual(paid, D('5000.00') * len(repayments) - sum(r.interest_portion for r in repayments))

    def test_final_emi_is_trimmed_and_the_schedule_ends(self):
        loan = self.make_loan(principal='3000', rate='0')
        rt = self.rt('LOAN', amount='5000', loan=loan, start=today() - relativedelta(months=1), description='EMI')
        self.run_engine()
        repayments = list(LoanRepayment.objects.filter(loan=loan))
        self.assertEqual(len(repayments), 1)
        self.assertEqual((repayments[0].principal_portion, repayments[0].amount), (D('3000.00'), D('3000.00')))
        rt.refresh_from_db()
        self.assertFalse(rt.is_active)

    def test_emi_that_cannot_cover_the_interest_is_deactivated(self):
        loan = self.make_loan()
        rt = self.rt('LOAN', amount='500', loan=loan, description='EMI')    # interest alone is 986.30
        self.run_engine()
        rt.refresh_from_db()
        self.assertFalse(rt.is_active)
        self.assertEqual(LoanRepayment.objects.count(), 0)

    def test_opening_paid_principal_and_down_payment_reduce_the_balance(self):
        loan = self.make_loan()
        Loan.objects.filter(pk=loan.pk).update(opening_paid_principal=D('50000'))
        self.rt('LOAN', amount='5000', loan=loan, description='EMI')
        self.run_engine()
        self.assertEqual(LoanRepayment.objects.get().interest_portion, D('493.15'))     # on 50,000

    def test_loan_schedule_without_an_account_is_deactivated(self):
        loan = self.make_loan()
        rt = self.rt('LOAN', amount='5000', loan=loan, account=None, description='EMI')
        self.run_engine()
        rt.refresh_from_db()
        self.assertFalse(rt.is_active)

    def test_rerunning_does_not_double_post_the_same_day(self):
        loan = self.make_loan()
        self.rt('LOAN', amount='5000', loan=loan, description='EMI')
        self.run_engine()
        self.run_engine()
        self.assertEqual(LoanRepayment.objects.count(), 1)


# ---------------------------------------------------------------------------
# Plan quota
# ---------------------------------------------------------------------------
class TestPlanQuota(SubBase):
    tier = 'FREE'

    def setUp(self):
        super().setUp()
        self.limit = get_limit('FREE', 'recurring_transactions')

    def test_quota_is_two_on_free(self):
        self.assertEqual(self.limit, 2)

    def test_can_add_until_the_limit_of_active_schedules(self):
        self.rt(description='a')
        self.assertTrue(self.user.profile.can_add_recurring())
        self.rt(description='b')
        self.assertFalse(self.user.profile.can_add_recurring())

    def test_cancelled_schedules_do_not_use_up_the_quota(self):
        self.rt(description='a', is_active=False)
        self.rt(description='b')
        self.assertTrue(self.user.profile.can_add_recurring())

    def test_only_active_schedules_lock_newer_ones(self):
        """Regression: a cancelled schedule locked a newer active one."""
        cancelled = self.rt(description='old', is_active=False)
        first = self.rt(description='a')
        second = self.rt(description='b')
        third = self.rt(description='c')
        profile = self.user.profile
        self.assertFalse(profile.is_recurring_locked(cancelled))
        self.assertFalse(profile.is_recurring_locked(first))
        self.assertFalse(profile.is_recurring_locked(second))
        self.assertTrue(profile.is_recurring_locked(third))

    def test_paid_plans_are_never_locked(self):
        profile = self.user.profile
        profile.tier = 'PRO'
        profile.save()
        for i in range(5):
            self.rt(description=f's{i}')
        self.assertFalse(any(profile.is_recurring_locked(r) for r in RecurringTransaction.objects.all()))
        self.assertTrue(profile.can_add_recurring())


# ---------------------------------------------------------------------------
# List view
# ---------------------------------------------------------------------------
class TestSubscriptionList(SubBase):
    url = reverse('recurring-list')

    def ctx(self, **params):
        response = self.client.get(self.url, params)
        self.assertEqual(response.status_code, 200)
        return response.context

    def test_login_required(self):
        self.client.logout()
        self.assertEqual(self.client.get(self.url).status_code, 302)

    def test_splits_active_and_cancelled_and_isolates_users(self):
        a = self.rt(description='a')
        c = self.rt(description='c', is_active=False)
        other = self.make_user('other-list')
        RecurringTransaction.objects.create(user=other, transaction_type='EXPENSE', amount=D('9'), currency='₹',
                                            description='secret', frequency='MONTHLY', start_date=today(), category='Food')
        ctx = self.ctx()
        self.assertEqual([r.id for r in ctx['active_subs']], [a.id])
        self.assertEqual([r.id for r in ctx['cancelled_subs']], [c.id])
        self.assertNotContains(self.client.get(self.url), 'secret')

    def test_cost_totals_exclude_income_and_transfers_and_use_equivalents(self):
        self.rt(amount='100', frequency='MONTHLY', description='m')
        self.rt(amount='1200', frequency='YEARLY', description='y')
        self.rt('INCOME', amount='50000', description='pay')
        self.rt('TRANSFER', amount='5000', account=None, from_account=self.cash, to_account=self.bank, description='sip')
        self.rt(amount='999', frequency='MONTHLY', description='gone', is_active=False)
        ctx = self.ctx()
        self.assertEqual(ctx['total_monthly_cost'], D('200'))          # 100 + 1200/12
        self.assertEqual(ctx['total_yearly_cost'], D('2400'))          # 1200 + 1200
        self.assertEqual(ctx['total_daily_cost'], D('2400') / 365)

    def test_loan_and_insurance_costs_are_included_in_the_totals(self):
        loan = Loan.objects.create(user=self.user, name='L', loan_type='PERSONAL', initial_principal=100000,
                                   duration_months=12, start_date=today(), is_active=True)
        self.rt('LOAN', amount='3000', loan=loan, description='emi')
        self.rt('INSURANCE_PREMIUM', amount='12000', frequency='YEARLY', description='prem')
        self.assertEqual(self.ctx()['total_monthly_cost'], D('4000'))

    def test_renewing_soon_is_every_outgoing_charge_due_within_thirty_days_soonest_first(self):
        loan = Loan.objects.create(user=self.user, name='L', loan_type='PERSONAL', initial_principal=1000,
                                   duration_months=12, start_date=today(), is_active=True)
        soon = self.rt(description='soon', start=today() + timedelta(days=5))
        emi = self.rt('LOAN', loan=loan, description='emi', start=today() + timedelta(days=2))
        prem = self.rt('INSURANCE_PREMIUM', description='prem', start=today() + timedelta(days=30))
        sip = self.rt('TRANSFER', account=None, from_account=self.cash, to_account=self.bank, description='sip',
                      start=today() + timedelta(days=9))
        self.rt('INCOME', description='pay', start=today() + timedelta(days=1))                 # income: never
        self.rt(description='far', start=today() + timedelta(days=31))                           # too far
        ctx = self.ctx()
        self.assertEqual([r.id for r in ctx['renewing_soon']], [emi.id, soon.id, sip.id, prem.id])
        self.assertEqual(ctx['renewals_count'], 4)

    def test_sorting(self):
        later = self.rt(description='b-later', amount='900', start=today() + timedelta(days=20))
        sooner = self.rt(description='a-sooner', amount='100', start=today() + timedelta(days=3))
        ids = lambda sort: [r.id for r in self.ctx(sort=sort)['active_subs']]
        self.assertEqual(ids('next_date_asc'), [sooner.id, later.id])
        self.assertEqual(ids('amount_desc'), [later.id, sooner.id])
        self.assertEqual(ids('amount_asc'), [sooner.id, later.id])
        self.assertEqual(ids('name_asc'), [sooner.id, later.id])
        self.assertEqual(len(ids('name_desc')), 2)   # accepted by the sort control: must not crash

    def test_filters(self):
        food = self.rt(description='f', category='Food')
        bills = self.rt(description='b', category='Bills', frequency='YEARLY')
        income = self.rt('INCOME', description='i')
        ids = lambda **p: {r.id for r in self.ctx(**p)['active_subs']}
        self.assertEqual(ids(category='Food'), {food.id})
        self.assertEqual(ids(frequency='YEARLY'), {bills.id})
        self.assertEqual(ids(transaction_type='INCOME'), {income.id})
        self.assertEqual(ids(transaction_type=['EXPENSE', 'INCOME']), {food.id, bills.id, income.id})
        self.assertEqual(ids(account=str(self.bank.id)), set())

    def test_status_filter(self):
        a = self.rt(description='a')
        c = self.rt(description='c', is_active=False)
        self.assertEqual({r.id for r in self.ctx(status='cancelled')['cancelled_subs']}, {c.id})
        self.assertEqual({r.id for r in self.ctx(status='active')['active_subs']}, {a.id})

    def test_type_filter_offers_every_type(self):
        offered = {o['value'] for f in self.ctx()['filter_config'].filters if f.key == 'transaction_type' for o in f.options}
        self.assertEqual(offered, {'EXPENSE', 'TRANSFER', 'INCOME', 'LOAN', 'CAPITAL', 'INSURANCE_PREMIUM'})

    def test_locked_flags_and_nudge_on_the_free_plan(self):
        free = self.make_user('free-list', tier='FREE')
        self.client.force_login(free)
        for i in range(3):
            RecurringTransaction.objects.create(user=free, transaction_type='EXPENSE', amount=D('5'), currency='₹',
                                                description=f's{i}', frequency='MONTHLY', start_date=today(), category='Food')
        ctx = self.ctx()
        self.assertEqual(sorted(s.is_locked for s in ctx['active_subs']), [False, False, True])
        self.assertTrue(ctx['is_limit_reached'])
        self.assertEqual((ctx['nudge_current'], ctx['nudge_limit'], ctx['nudge_upgrade_tier'], ctx['nudge_at_limit']),
                         (3, 2, 'PLUS', True))

    def test_paid_plan_has_no_nudge_or_locks(self):
        self.rt()
        ctx = self.ctx()
        self.assertNotIn('nudge_limit', ctx)
        self.assertFalse(ctx['is_limit_reached'])
        self.assertFalse(any(s.is_locked for s in ctx['active_subs']))

    def test_htmx_partial(self):
        full = [t.name for t in self.client.get(self.url).templates]
        partial = [t.name for t in self.client.get(self.url, HTTP_HX_REQUEST='true').templates]
        self.assertIn('expenses/recurring_transaction_list.html', full)
        self.assertIn('expenses/partials/_recurring_transaction_list.html', partial)

    def test_visiting_the_list_posts_due_schedules(self):
        self.rt(start=today())
        self.client.get(self.url)
        self.assertEqual(Expense.objects.count(), 1)


# ---------------------------------------------------------------------------
# Create / edit / delete
# ---------------------------------------------------------------------------
class TestSubscriptionCrud(SubBase):
    def payload(self, **overrides):
        data = {'transaction_type': 'EXPENSE', 'amount': '499', 'currency': '₹', 'description': 'Netflix',
                'category': 'Food', 'frequency': 'MONTHLY', 'start_date': today().isoformat(), 'payment_method': 'UPI',
                'account': str(self.cash.id), 'is_active': 'on'}
        data.update(overrides)
        return data

    def create(self, **overrides):
        return self.client.post(reverse('recurring-create'), self.payload(**overrides))

    # create
    def test_get_prefills_from_the_query_string(self):
        response = self.client.get(reverse('recurring-create'), {'description': 'Hotstar', 'amount': '299'})
        self.assertEqual(response.context['form'].initial, {'description': 'Hotstar', 'amount': '299'})

    def test_create_saves_and_posts_the_first_occurrence_immediately(self):
        response = self.create()
        self.assertRedirects(response, reverse('recurring-list'))
        rt = RecurringTransaction.objects.get()
        self.assertEqual((rt.user, rt.amount, rt.frequency, rt.account, rt.payment_method, rt.category),
                         (self.user, D('499.00'), 'MONTHLY', self.cash, 'UPI', 'Food'))
        self.assertEqual(Expense.objects.count(), 1)
        self.assertIn('Recurring transaction created successfully!', self.messages(response))

    def test_create_every_type(self):
        loan = Loan.objects.create(user=self.user, name='L', loan_type='PERSONAL', initial_principal=100000,
                                   duration_months=12, start_date=today(), is_active=True)
        cases = [
            dict(transaction_type='INCOME', category='', source='Salary', description='Pay'),
            dict(transaction_type='TRANSFER', category='', from_account=str(self.cash.id), to_account=str(self.bank.id), description='SIP'),
            dict(transaction_type='LOAN', category='', loan=str(loan.id), description='EMI'),
            dict(transaction_type='CAPITAL', category='', capital_subtype='large_purchase', description='Sofa'),
            dict(transaction_type='INSURANCE_PREMIUM', category='', description='Premium'),
        ]
        for case in cases:
            with self.subTest(type=case['transaction_type']):
                self.assertEqual(self.create(**case).status_code, 302)
        self.assertEqual(set(RecurringTransaction.objects.values_list('transaction_type', flat=True)),
                         {'INCOME', 'TRANSFER', 'LOAN', 'CAPITAL', 'INSURANCE_PREMIUM'})

    def test_invalid_input_creates_nothing(self):
        for override in ({'amount': '0'}, {'amount': '-5'}, {'category': ''}, {'frequency': 'HOURLY'}, {'description': ''}):
            with self.subTest(override=override):
                self.assertEqual(self.create(**override).status_code, 200)
        self.assertEqual(RecurringTransaction.objects.count(), 0)

    def test_exact_duplicate_is_refused_with_a_message(self):
        self.create()
        response = self.create()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(RecurringTransaction.objects.count(), 1)

    def test_a_near_duplicate_on_another_account_is_a_form_error_not_a_500(self):
        """Regression: the database rule ignores the account, so this used to raise IntegrityError."""
        self.create()
        response = self.create(account=str(self.bank.id))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(RecurringTransaction.objects.count(), 1)

    def test_history_checkbox_posts_the_past(self):
        start = (today() - relativedelta(months=3)).isoformat()
        self.create(start_date=start, create_historical_entries='on')
        self.assertGreaterEqual(Expense.objects.count(), 3)

    def test_no_history_checkbox_posts_only_what_is_due_from_now(self):
        start = (today() - relativedelta(months=3, days=3)).isoformat()
        self.create(start_date=start)
        self.assertEqual(Expense.objects.count(), 0)
        rt = RecurringTransaction.objects.get()
        self.assertGreater(rt.next_due_date, today())

    def test_limit_reached_sends_free_users_to_pricing(self):
        free = self.make_user('free-create', tier='FREE')
        self.client.force_login(free)
        for i in range(2):
            RecurringTransaction.objects.create(user=free, transaction_type='EXPENSE', amount=D('5'), currency='₹',
                                                description=f's{i}', frequency='MONTHLY', start_date=today(), category='Food')
        response = self.client.get(reverse('recurring-create'))
        self.assertRedirects(response, reverse('pricing'), fetch_redirect_response=False)
        self.assertEqual(self.client.post(reverse('recurring-create'), self.payload()).status_code, 302)
        self.assertEqual(RecurringTransaction.objects.filter(user=free).count(), 2)

    def test_free_user_with_a_cancelled_schedule_can_still_add(self):
        free = self.make_user('free-create2', tier='FREE')
        self.client.force_login(free)
        for i, active in enumerate((True, False)):
            RecurringTransaction.objects.create(user=free, transaction_type='EXPENSE', amount=D('5'), currency='₹',
                                                description=f's{i}', frequency='MONTHLY', start_date=today(),
                                                category='Food', is_active=active)
        self.assertEqual(self.client.get(reverse('recurring-create')).status_code, 200)

    def test_next_param_only_for_same_site(self):
        self.assertEqual(self.create(next='/transactions/').url, '/transactions/')
        self.assertEqual(self.create(next='https://evil.example/', description='x2').url, reverse('recurring-list'))

    # edit
    def edit(self, rt, **overrides):
        data = self.payload(description=rt.description, amount=str(rt.amount), start_date=rt.start_date.isoformat())
        data.update(overrides)
        return self.client.post(reverse('recurring-edit', kwargs={'pk': rt.pk}), data)

    def test_edit_page_renders(self):
        rt = self.rt()
        self.assertEqual(self.client.get(reverse('recurring-edit', kwargs={'pk': rt.pk})).status_code, 200)

    def test_edit_updates_fields(self):
        rt = self.rt(start=today() + timedelta(days=5))
        response = self.edit(rt, amount='650', category='Bills', payment_method='Cash')
        self.assertRedirects(response, reverse('recurring-list'))
        rt.refresh_from_db()
        self.assertEqual((rt.amount, rt.category, rt.payment_method), (D('650.00'), 'Bills', 'Cash'))
        self.assertIn('Recurring transaction updated successfully!', self.messages(response))

    def test_editing_the_frequency_does_not_back_post_history(self):
        """Regression: this used to create months of phantom expenses."""
        rt = self.rt(start=today() - timedelta(days=100))
        RecurringTransaction.objects.filter(pk=rt.pk).update(last_processed_date=today() - timedelta(days=5))
        before = Expense.objects.count()
        self.edit(rt, frequency='WEEKLY')
        self.assertEqual(Expense.objects.count(), before)

    def test_pausing_by_unticking_active_stops_posting(self):
        rt = self.rt(start=today() + timedelta(days=1))
        data = self.payload(description=rt.description, amount=str(rt.amount), start_date=(today() - timedelta(days=60)).isoformat())
        data.pop('is_active')
        self.client.post(reverse('recurring-edit', kwargs={'pk': rt.pk}), data)
        rt.refresh_from_db()
        self.assertFalse(rt.is_active)
        self.assertEqual(Expense.objects.count(), 0)

    def test_resuming_a_paused_schedule_does_not_back_post_the_pause(self):
        rt = self.rt(start=today() - timedelta(days=200), last_processed_date=today() - timedelta(days=100),
                     description='Gym', is_active=False)
        self.client.post(reverse('recurring-edit', kwargs={'pk': rt.pk}),
                         self.payload(description='Gym', amount=str(rt.amount), start_date=rt.start_date.isoformat()))
        rt.refresh_from_db()
        self.assertTrue(rt.is_active)
        self.assertEqual(Expense.objects.count(), 0)
        self.assertGreater(rt.next_due_date, today())

    def test_resuming_with_history_ticked_catches_up(self):
        rt = self.rt(start=today() - timedelta(days=200), last_processed_date=today() - timedelta(days=100),
                     description='Gym', is_active=False)
        self.client.post(reverse('recurring-edit', kwargs={'pk': rt.pk}),
                         self.payload(description='Gym', amount=str(rt.amount), start_date=rt.start_date.isoformat(),
                                      create_historical_entries='on'))
        self.assertGreaterEqual(Expense.objects.count(), 6)

    def test_other_users_schedule_is_404(self):
        other = self.make_user('other-edit')
        theirs = RecurringTransaction.objects.create(user=other, transaction_type='EXPENSE', amount=D('5'), currency='₹',
                                                     description='x', frequency='MONTHLY', start_date=today(), category='Food')
        self.assertEqual(self.client.get(reverse('recurring-edit', kwargs={'pk': theirs.pk})).status_code, 404)
        self.assertEqual(self.edit(theirs, amount='1').status_code, 404)
        self.assertEqual(self.client.post(reverse('recurring-delete', kwargs={'pk': theirs.pk})).status_code, 404)
        self.assertTrue(RecurringTransaction.objects.filter(pk=theirs.pk).exists())

    def test_locked_schedule_cannot_be_edited(self):
        free = self.make_user('free-edit', tier='FREE')
        self.client.force_login(free)
        made = [RecurringTransaction.objects.create(user=free, transaction_type='EXPENSE', amount=D('5'), currency='₹',
                                                    description=f's{i}', frequency='MONTHLY', start_date=today(), category='Food')
                for i in range(3)]
        self.assertEqual(self.client.get(reverse('recurring-edit', kwargs={'pk': made[0].pk})).status_code, 200)
        response = self.client.get(reverse('recurring-edit', kwargs={'pk': made[2].pk}))
        self.assertRedirects(response, reverse('recurring-list'), fetch_redirect_response=False)

    def test_edit_into_a_duplicate_is_refused(self):
        self.rt(amount='499', description='Netflix', start=today())
        other = self.rt(amount='100', description='Hotstar', start=today())
        response = self.edit(other, description='Netflix', amount='499')
        self.assertEqual(response.status_code, 200)
        other.refresh_from_db()
        self.assertEqual(other.description, 'Hotstar')

    def test_edit_next_param(self):
        rt = self.rt(start=today() + timedelta(days=3))
        self.assertEqual(self.edit(rt, next='/transactions/').url, '/transactions/')
        self.assertEqual(self.edit(rt, next='https://evil.example/').url, reverse('recurring-list'))

    # delete
    def test_delete_confirmation_page_then_delete(self):
        rt = self.rt(amount='1200', frequency='MONTHLY')
        url = reverse('recurring-delete', kwargs={'pk': rt.pk})
        self.assertEqual(self.client.get(url).status_code, 200)
        self.assertTrue(RecurringTransaction.objects.filter(pk=rt.pk).exists())
        response = self.client.post(url)
        self.assertRedirects(response, reverse('recurring-list'))
        self.assertFalse(RecurringTransaction.objects.filter(pk=rt.pk).exists())
        self.assertIn('You just saved ₹14,400/year 🎉', self.messages(response))

    def test_deleting_keeps_the_entries_it_already_posted(self):
        rt = self.rt(start=today())
        self.run_engine()
        self.client.post(reverse('recurring-delete', kwargs={'pk': rt.pk}))
        self.assertEqual(Expense.objects.count(), 1)

    def test_deleting_income_or_a_transfer_does_not_claim_a_saving(self):
        income = self.rt('INCOME', amount='50000', description='pay')
        sip = self.rt('TRANSFER', account=None, from_account=self.cash, to_account=self.bank, description='sip')
        for rt in (income, sip):
            response = self.client.post(reverse('recurring-delete', kwargs={'pk': rt.pk}))
            msgs = self.messages(response)
            self.assertFalse(any('saved' in m for m in msgs), msgs)
            self.assertIn('Recurring transaction deleted.', msgs)

    def test_login_required_everywhere(self):
        rt = self.rt()
        self.client.logout()
        for name, kwargs in (('recurring-list', {}), ('recurring-create', {}), ('recurring-edit', {'pk': rt.pk}),
                             ('recurring-delete', {'pk': rt.pk})):
            self.assertEqual(self.client.get(reverse(name, kwargs=kwargs)).status_code, 302, name)
        self.assertTrue(RecurringTransaction.objects.filter(pk=rt.pk).exists())


class TestSubscriptionDocs(SubBase):
    def test_frequencies_and_types_match_the_guide(self):
        self.assertEqual(FREQUENCIES, ['DAILY', 'WEEKLY', 'BIWEEKLY', 'MONTHLY', 'QUARTERLY', 'SEMIANNUALLY', 'YEARLY'])
        types = [t for t, _label in RecurringTransactionForm(user=self.user).fields['transaction_type'].choices]
        self.assertEqual(types, ['EXPENSE', 'INCOME', 'TRANSFER', 'LOAN', 'CAPITAL', 'INSURANCE_PREMIUM'])

    def test_renewing_window_is_thirty_days(self):
        self.rt(description='edge', start=today() + timedelta(days=30))
        self.rt(description='past-edge', start=today() + timedelta(days=31))
        response = self.client.get(reverse('recurring-list'))
        self.assertEqual([r.description for r in response.context['renewing_soon']], ['edge'])

    def test_plan_quotas_match_the_guide(self):
        self.assertEqual((get_limit('FREE', 'recurring_transactions'), get_limit('PLUS', 'recurring_transactions'),
                          get_limit('PRO', 'recurring_transactions')), (2, 5, -1))
