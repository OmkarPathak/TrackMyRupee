"""The add / edit save path: small jobs run after the response, and the form does not load the same lists twice."""
import json
import threading
from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.cache import cache
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from expenses import background
from expenses.forms import ExpenseForm, form_category_names
from expenses.models import Account, Category, Expense, ExpenseKeywordHint  # noqa: F401


class RunAfterResponseTests(TestCase):
    def test_runs_right_away_when_background_work_is_off(self):
        seen = []
        background.run_after_response(seen.append, 1)
        self.assertEqual(seen, [1])          # tests run inline

    @override_settings(BACKGROUND_TASKS_ENABLED=True)
    def test_runs_on_another_thread_when_on(self):
        done, where = threading.Event(), []

        def job():
            where.append(threading.current_thread().name)
            done.set()

        background.run_after_response(job, name='probe-job')
        self.assertTrue(done.wait(5))
        self.assertEqual(where, ['probe-job'])

    @override_settings(BACKGROUND_TASKS_ENABLED=True)
    def test_runs_inline_when_every_slot_is_busy(self):
        taken = [background._slots.acquire(blocking=False) for _ in range(4)]
        try:
            seen = []
            background.run_after_response(seen.append, 'x')
            self.assertEqual(seen, ['x'])    # never dropped
        finally:
            for ok in taken:
                if ok:
                    background._slots.release()

    def test_a_failing_job_does_not_raise(self):
        def boom():
            raise RuntimeError('nope')
        background.run_after_response(boom)


class SavePathTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user('sp', 'sp@example.com', 'pw')
        profile = self.user.profile
        profile.has_seen_tutorial = True
        profile.tier = 'PRO'
        profile.save()
        self.user.refresh_from_db()
        self.cash = Account.objects.create(user=self.user, name='Cash', account_type='CASH_WALLET', balance=Decimal('5000'))
        self.bank = Account.objects.create(user=self.user, name='Bank', account_type='SAVINGS_ACCOUNT', balance=Decimal('9000'))
        names = list(Category.objects.filter(user=self.user).order_by('id').values_list('name', flat=True))
        self.food, self.travel = names[0], names[1]      # the categories every new user starts with
        self.client.force_login(self.user)

    def save(self, **over):
        body = {'key': 'k-1', 'amount': '450', 'currency': '₹', 'date': date.today().isoformat(), 'description': 'Swiggy dinner',
                'category': self.food, 'account_id': self.bank.id, 'payment_method': 'UPI'}
        body.update(over)
        return self.client.post(reverse('expense-composer-save'), data=json.dumps(body), content_type='application/json')

    def test_add_still_learns_the_keyword(self):
        self.assertEqual(self.save().status_code, 200)
        hint = ExpenseKeywordHint.objects.get(user=self.user, keyword='swiggy')
        self.assertEqual((hint.category, hint.account_id, hint.payment_method), (self.food, self.bank.id, 'UPI'))
        self.save(key='k-2', category=self.travel, payment_method='Cash', account_id=self.cash.id)
        hint.refresh_from_db()
        self.assertEqual((hint.category, hint.account_id, hint.use_count), (self.travel, self.cash.id, 2))

    def test_form_loads_categories_and_accounts_once(self):
        user = User.objects.get(pk=self.user.pk)
        user.profile
        with CaptureQueriesContext(connection) as ctx:
            ExpenseForm(user=user)
            ExpenseForm(user=user)
            form_category_names(user)
        sql = ' '.join(q['sql'] for q in ctx.captured_queries)
        self.assertEqual(sql.count('FROM "expenses_category"'), 1)
        self.assertEqual(sql.count('FROM "expenses_account"'), 1)

    def test_default_account_is_still_cash(self):
        user = User.objects.get(pk=self.user.pk)
        user.profile
        self.assertEqual(ExpenseForm(user=user).fields['account'].initial, self.cash.id)

    def test_add_query_ceiling(self):
        self.client.get(reverse('expense-composer-data'))
        with CaptureQueriesContext(connection) as ctx:
            self.assertEqual(self.save().status_code, 200)
        self.assertLessEqual(len(ctx.captured_queries), 42)     # measured 38 (the keyword is learned inline under test)

    def test_edit_does_not_reload_the_user(self):
        expense = Expense.objects.create(user=self.user, date=date.today(), amount=Decimal('100'), description='x',
                                         category=self.food, account=self.bank, payment_method='UPI')
        form = {'date': date.today().isoformat(), 'amount': '120', 'currency': '₹', 'account': self.bank.id,
                'description': 'x edited', 'category': self.food, 'payment_method': 'UPI'}
        with CaptureQueriesContext(connection) as ctx:
            response = self.client.post(reverse('expense-edit', args=[expense.uuid]), form)
        self.assertEqual(response.status_code, 302)
        # the profile is loaded once, for the request itself: saving the expense does not fetch it again
        self.assertEqual(sum('FROM "expenses_userprofile"' in q['sql'] for q in ctx.captured_queries), 1)
        expense.refresh_from_db()
        self.assertEqual(expense.amount, Decimal('120.00'))
