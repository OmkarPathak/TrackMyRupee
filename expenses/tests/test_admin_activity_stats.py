"""Admin: how many people added an expense, and how many signed in, in the last 7, 15 and 30 days."""
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from expenses.admin_stats import activity_stats
from expenses.models import Account, Expense


def rows(stats):
    return {r['days']: r for r in stats['rows']}


class StatsTests(TestCase):
    def setUp(self):
        self.now = timezone.now()

    def person(self, name, signed_in_days_ago=None):
        user = User.objects.create_user(name, password='x')
        if signed_in_days_ago is not None:
            User.objects.filter(pk=user.pk).update(last_login=self.now - timedelta(days=signed_in_days_ago, minutes=-1))
        return user

    def expense(self, user, days_ago, deleted=False):
        account, _ = Account.objects.get_or_create(user=user, name='Cash', defaults={
            'account_type': 'CASH_WALLET', 'balance': Decimal('0'), 'currency': '₹'})
        expense = Expense.objects.create(user=user, category='Food', amount=Decimal('10'), base_amount=Decimal('10'),
                                         currency='₹', date=self.now.date(), account=account)
        Expense.objects.filter(pk=expense.pk).update(created_at=self.now - timedelta(days=days_ago, minutes=-1),
                                                     is_deleted=deleted)
        return expense

    def test_no_users_no_division_by_zero(self):
        stats = activity_stats(self.now)
        self.assertEqual(stats['total'], 0)
        self.assertEqual([(r['expense_users'], r['login_users'], r['expense_pct']) for r in stats['rows']],
                         [(0, 0, 0)] * 3)

    def test_people_who_added_an_expense_in_each_window(self):
        a, b, c, d = (self.person(n) for n in 'abcd')
        self.expense(a, 2)          # inside all three windows
        self.expense(b, 10)         # inside 15 and 30
        self.expense(c, 20)         # inside 30 only
        self.expense(d, 45)         # none
        r = rows(activity_stats(self.now))
        self.assertEqual([r[7]['expense_users'], r[15]['expense_users'], r[30]['expense_users']], [1, 2, 3])

    def test_a_person_with_several_expenses_counts_once(self):
        a = self.person('a')
        for days in (1, 2, 3):
            self.expense(a, days)
        self.assertEqual(rows(activity_stats(self.now))[7]['expense_users'], 1)

    def test_a_deleted_expense_does_not_count(self):
        a = self.person('a')
        self.expense(a, 1, deleted=True)
        self.assertEqual(rows(activity_stats(self.now))[30]['expense_users'], 0)

    def test_people_who_signed_in_in_each_window(self):
        self.person('a', 3)
        self.person('b', 12)
        self.person('c', 29)
        self.person('d', 60)
        self.person('never')                    # never signed in: last_login is empty
        r = rows(activity_stats(self.now))
        self.assertEqual([r[7]['login_users'], r[15]['login_users'], r[30]['login_users']], [1, 2, 3])
        self.assertEqual(activity_stats(self.now)['total'], 5)

    def test_percentages_are_of_all_users(self):
        for name in 'abcd':
            self.person(name, 1)
        self.person('idle')
        self.assertEqual(rows(activity_stats(self.now))[7]['login_pct'], 80)

    def test_the_demo_account_is_left_out(self):
        demo = self.person('demo', 1)
        self.expense(demo, 1)
        stats = activity_stats(self.now)
        self.assertEqual(stats['total'], 0)
        self.assertEqual(rows(stats)[7]['login_users'], 0)
        self.assertEqual(rows(stats)[7]['expense_users'], 0)


class AdminPageTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser('boss', 'boss@example.com', 'pass12345!')
        self.person = User.objects.create_user('person', password='pass12345!')

    def test_the_page_shows_the_numbers_to_staff(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse('admin:auth_user_activity'))
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        for text in ('Last 7 days', 'Last 15 days', 'Last 30 days', 'Added at least one expense', 'Signed in', 'out of 2 users'):
            self.assertIn(text, html)

    def test_the_users_list_links_to_it(self):
        self.client.force_login(self.admin)
        html = self.client.get(reverse('admin:auth_user_changelist')).content.decode()
        self.assertIn(reverse('admin:auth_user_activity'), html)
        self.assertIn('Activity stats', html)

    def test_it_is_not_for_everyone(self):
        self.assertEqual(self.client.get(reverse('admin:auth_user_activity')).status_code, 302)   # to the admin login
        self.client.force_login(self.person)
        self.assertEqual(self.client.get(reverse('admin:auth_user_activity')).status_code, 302)

    def test_a_user_can_still_be_opened(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse('admin:auth_user_change', args=[self.person.pk])).status_code, 200)
