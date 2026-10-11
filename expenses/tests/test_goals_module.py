"""Savings goals: contribution maths, completion, estimates, forms, views, plan locks and totals."""

import datetime
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.contrib.messages import get_messages
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from expenses.forms import GoalContributionForm, SavingsGoalForm
from expenses.models import Account, FXRate, GoalContribution, SavingsGoal, UserProfile

D = Decimal


def today():
    return timezone.localdate()


class GoalBase(TestCase):
    tier = 'PRO'

    def setUp(self):
        self.user = self.make_user('goal-user', self.tier)
        self.client.force_login(self.user)
        Account.objects.filter(user=self.user).delete()
        self.cash = Account.objects.create(user=self.user, name='Cash', account_type='CASH_WALLET',
                                           balance=D('100000.00'), currency='₹')
        self.bank = Account.objects.create(user=self.user, name='Bank', account_type='SAVINGS_ACCOUNT',
                                           balance=D('100000.00'), currency='₹')
        cache.clear()

    def make_user(self, name, tier='PRO'):
        user = User.objects.create_user(username=name, password='pass')
        profile, _ = UserProfile.objects.get_or_create(user=user, defaults={'currency': '₹'})
        profile.tier = tier
        profile.currency = '₹'
        profile.has_seen_tutorial = True
        profile.save()
        user.refresh_from_db()
        return user

    def goal(self, target='1000', **kw):
        values = dict(user=self.user, name='Trip', target_amount=D(str(target)), currency='₹')
        values.update(kw)
        return SavingsGoal.objects.create(**values)

    def contribute(self, goal, amount, account='default', when=None, **kw):
        return GoalContribution.objects.create(
            goal=goal, amount=D(str(amount)), account=self.cash if account == 'default' else account,
            date=when or today(), **kw)

    def seed_fx(self):
        for (src, dst), rate in {('USD', 'INR'): '80', ('INR', 'USD'): '0.0125',
                                 ('$', '₹'): '80', ('₹', '$'): '0.0125'}.items():
            FXRate.objects.update_or_create(from_currency=src, to_currency=dst, as_of_date=today(),
                                            defaults={'rate': D(rate), 'source': 'test'})
        cache.clear()

    def bal(self, account):
        account.refresh_from_db()
        return account.balance


class TestContributionMaths(GoalBase):
    def test_contribution_moves_money_from_the_account_to_the_goal(self):
        goal = self.goal()
        self.contribute(goal, 300)
        goal.refresh_from_db()
        self.assertEqual(goal.current_amount, D('300.00'))
        self.assertEqual(self.bal(self.cash), D('99700.00'))

    def test_edit_moves_only_the_difference_and_follows_an_account_change(self):
        goal = self.goal()
        c = self.contribute(goal, 300)
        c.amount = D('500')
        c.save()
        goal.refresh_from_db()
        self.assertEqual((goal.current_amount, self.bal(self.cash)), (D('500.00'), D('99500.00')))
        c.account = self.bank
        c.save()
        self.assertEqual((self.bal(self.cash), self.bal(self.bank)), (D('100000.00'), D('99500.00')))

    def test_delete_gives_the_money_back(self):
        goal = self.goal()
        c = self.contribute(goal, 300)
        c.delete()
        goal.refresh_from_db()
        self.assertEqual((goal.current_amount, self.bal(self.cash)), (D('0.00'), D('100000.00')))

    def test_deleting_a_goal_returns_every_contribution_to_its_account(self):
        goal = self.goal()
        self.contribute(goal, 300)
        self.contribute(goal, 200, account=self.bank)
        goal.delete()
        self.assertEqual((self.bal(self.cash), self.bal(self.bank)), (D('100000.00'), D('100000.00')))
        self.assertFalse(GoalContribution.objects.exists())

    def test_foreign_currency_goal_charges_the_account_in_its_own_currency(self):
        self.seed_fx()
        goal = self.goal(target='500', currency='$')
        self.contribute(goal, 10)                         # $10 out of a rupee account
        self.assertEqual(self.bal(self.cash), D('100000.00') - D('800.00'))
        goal.refresh_from_db()
        self.assertEqual(goal.current_amount, D('10.00'))

    def test_contribution_without_an_account_changes_no_balance(self):
        goal = self.goal()
        self.contribute(goal, 100, account=None)
        self.assertEqual(self.bal(self.cash), D('100000.00'))
        goal.refresh_from_db()
        self.assertEqual(goal.current_amount, D('100.00'))


class TestCompletion(GoalBase):
    def test_reaching_the_target_marks_the_goal_completed_in_the_database(self):
        goal = self.goal(target='1000')
        self.contribute(goal, 600)
        self.assertFalse(SavingsGoal.objects.get(pk=goal.pk).is_completed)
        self.contribute(goal, 400)
        self.assertTrue(SavingsGoal.objects.get(pk=goal.pk).is_completed)

    def test_removing_money_reopens_a_completed_goal(self):
        goal = self.goal(target='1000')
        c = self.contribute(goal, 1000)
        self.assertTrue(SavingsGoal.objects.get(pk=goal.pk).is_completed)
        c.delete()
        self.assertFalse(SavingsGoal.objects.get(pk=goal.pk).is_completed)

    def test_editing_a_contribution_across_the_target_flips_the_flag(self):
        goal = self.goal(target='1000')
        c = self.contribute(goal, 400)
        c.amount = D('1200')
        c.save()
        self.assertTrue(SavingsGoal.objects.get(pk=goal.pk).is_completed)
        c.amount = D('300')
        c.save()
        self.assertFalse(SavingsGoal.objects.get(pk=goal.pk).is_completed)

    def test_raising_or_lowering_the_target_re_evaluates_completion(self):
        goal = self.goal(target='1000')
        self.contribute(goal, 1000)
        goal.refresh_from_db()
        goal.target_amount = D('5000')
        goal.save()
        self.assertFalse(SavingsGoal.objects.get(pk=goal.pk).is_completed)
        goal.target_amount = D('800')
        goal.save()
        self.assertTrue(SavingsGoal.objects.get(pk=goal.pk).is_completed)

    def test_progress_is_capped_at_100_and_safe_for_a_zero_target(self):
        goal = self.goal(target='1000')
        self.contribute(goal, 250)
        goal.refresh_from_db()
        self.assertEqual(goal.progress_percentage, 25.0)
        self.contribute(goal, 2000)
        goal.refresh_from_db()
        self.assertEqual(goal.progress_percentage, 100)
        zero = SavingsGoal(user=self.user, name='z', target_amount=D('0'), current_amount=D('10'))
        self.assertEqual(zero.progress_percentage, 0)

    def test_the_detail_page_offers_contributions_only_while_the_goal_is_open(self):
        goal = self.goal(target='1000')
        self.contribute(goal, 1000)
        page = self.client.get(reverse('goal-detail', args=[goal.uuid]))
        self.assertTrue(page.context['goal'].is_completed)


class TestEstimates(GoalBase):
    def test_estimated_completion_follows_the_average_daily_pace(self):
        goal = self.goal(target='1000')
        self.contribute(goal, 100, when=today() - datetime.timedelta(days=10))
        goal.refresh_from_db()
        # 100 over 10 days = 10 a day; 900 left -> 90 days
        self.assertEqual(goal.estimated_completion_date, today() + datetime.timedelta(days=90))

    def test_model_and_page_give_the_same_estimate(self):
        goal = self.goal(target='1000')
        self.contribute(goal, 100, when=today() - datetime.timedelta(days=10))
        self.contribute(goal, 50, when=today() - datetime.timedelta(days=2))
        goal.refresh_from_db()
        ctx = self.client.get(reverse('goal-detail', args=[goal.uuid])).context
        self.assertEqual(ctx['estimated_completion_date'], goal.estimated_completion_date)

    def test_no_estimate_without_contributions_or_once_completed(self):
        goal = self.goal(target='1000')
        self.assertIsNone(goal.estimated_completion_date)
        self.contribute(goal, 1000)
        goal.refresh_from_db()
        self.assertEqual(goal.estimated_completion_date, today())

    def test_needed_per_month_and_on_track_flag(self):
        goal = self.goal(target='1000', target_date=today() + datetime.timedelta(days=61))
        self.contribute(goal, 400, when=today() - datetime.timedelta(days=30))
        ctx = self.client.get(reverse('goal-detail', args=[goal.uuid])).context
        # 600 left over ~2 months
        self.assertAlmostEqual(float(ctx['needed_per_month']), 600 / (61 / 30.437), places=1)
        # pace: 400 in 30 days -> finishes in ~45 days, before the deadline
        self.assertTrue(ctx['is_on_track'])
        self.assertTrue(ctx['completion_status']['is_early'])

    def test_behind_pace_reports_the_gap(self):
        goal = self.goal(target='1000', target_date=today() + datetime.timedelta(days=30))
        self.contribute(goal, 10, when=today() - datetime.timedelta(days=100))
        ctx = self.client.get(reverse('goal-detail', args=[goal.uuid])).context
        self.assertFalse(ctx['is_on_track'])
        self.assertGreater(ctx['needed_gap'], 0)
        self.assertFalse(ctx['completion_status']['is_early'])

    def test_past_target_date_needs_everything_now(self):
        goal = self.goal(target='1000', target_date=today() - datetime.timedelta(days=5))
        self.contribute(goal, 100)
        ctx = self.client.get(reverse('goal-detail', args=[goal.uuid])).context
        self.assertEqual((ctx['needed_per_month'], ctx['needed_gap']), (D('900.00'), D('900.00')))
        self.assertFalse(ctx['is_on_track'])

    def test_trend_is_cumulative_and_includes_contributions_before_the_window(self):
        goal = self.goal(target='10000')
        self.contribute(goal, 100, when=today() - datetime.timedelta(days=60))
        self.contribute(goal, 50, when=today() - datetime.timedelta(days=3))
        trend = self.client.get(reverse('goal-detail', args=[goal.uuid])).context['trend_data']
        self.assertEqual(trend['type'], 'monthly')        # more than 45 days of history
        actual = [v for v in trend['values'] if v is not None]
        self.assertEqual((len(actual), actual[-1]), (12, 150.0))
        self.assertEqual(len(trend['labels']), len(trend['values']))

    def test_daily_trend_for_a_young_goal(self):
        goal = self.goal(target='10000')
        self.contribute(goal, 100, when=today() - datetime.timedelta(days=5))
        self.contribute(goal, 50, when=today())
        trend = self.client.get(reverse('goal-detail', args=[goal.uuid])).context['trend_data']
        self.assertEqual(trend['type'], 'daily')
        actual = [v for v in trend['values'] if v is not None]
        self.assertEqual((actual[0], actual[-1], len(actual)), (0.0, 150.0, 30))


class TestForms(GoalBase):
    def data(self, **kw):
        data = {'name': 'Trip', 'target_amount': '1000', 'currency': '₹', 'icon': '✈️', 'color': 'primary'}
        data.update(kw)
        return data

    def test_goal_form_rules(self):
        self.assertTrue(SavingsGoalForm(self.data(), user=self.user).is_valid())
        for bad in ({'target_amount': '0'}, {'target_amount': '-5'}, {'target_amount': 'abc'},
                    {'target_amount': '1' * 14}, {'name': ''}, {'name': '   '}):
            form = SavingsGoalForm(self.data(**bad), user=self.user)
            self.assertFalse(form.is_valid(), bad)

    def test_a_new_goal_cannot_have_a_past_target_date(self):
        form = SavingsGoalForm(self.data(target_date=(today() - datetime.timedelta(days=1)).isoformat()), user=self.user)
        self.assertIn('target_date', form.errors)
        ok = SavingsGoalForm(self.data(target_date=today().isoformat()), user=self.user)
        self.assertTrue(ok.is_valid(), ok.errors)

    def test_an_existing_goal_may_keep_a_date_that_has_since_passed(self):
        goal = self.goal(target_date=today() - datetime.timedelta(days=10))
        form = SavingsGoalForm(self.data(target_date=goal.target_date.isoformat()), instance=goal, user=self.user)
        self.assertTrue(form.is_valid(), form.errors)

    def test_currency_cannot_change_once_money_is_in_the_goal(self):
        self.seed_fx()
        goal = self.goal(currency='₹')
        self.contribute(goal, 100)
        form = SavingsGoalForm(self.data(currency='$'), instance=goal, user=self.user)
        self.assertIn('currency', form.errors)
        empty = self.goal(name='Empty', currency='₹')
        self.assertTrue(SavingsGoalForm(self.data(currency='$'), instance=empty, user=self.user).is_valid())

    def test_contribution_form_rules(self):
        base = {'account': self.cash.pk, 'amount': '100', 'date': today()}
        self.assertTrue(GoalContributionForm(base, user=self.user).is_valid())
        self.assertIn('amount', GoalContributionForm({**base, 'amount': '0'}, user=self.user).errors)
        self.assertIn('amount', GoalContributionForm({**base, 'amount': '-1'}, user=self.user).errors)
        self.assertIn('account', GoalContributionForm({**base, 'account': ''}, user=self.user).errors)
        future = (today() + datetime.timedelta(days=3)).isoformat()
        self.assertIn('date', GoalContributionForm({**base, 'date': future}, user=self.user).errors)
        tomorrow = (today() + datetime.timedelta(days=1)).isoformat()
        self.assertTrue(GoalContributionForm({**base, 'date': tomorrow}, user=self.user).is_valid())

    def test_contribution_account_must_be_the_users_own(self):
        other = self.make_user('goal-other')
        theirs = Account.objects.create(user=other, name='Theirs', account_type='CASH_WALLET', balance=1, currency='₹')
        form = GoalContributionForm({'account': theirs.pk, 'amount': '5', 'date': today()}, user=self.user)
        self.assertIn('account', form.errors)


class TestViews(GoalBase):
    def messages(self, response):
        return [str(m) for m in get_messages(response.wsgi_request)]

    def test_create_edit_and_list(self):
        response = self.client.post(reverse('goal-create'), {
            'name': 'Laptop', 'target_amount': '50000', 'currency': '₹', 'icon': '💻', 'color': 'success'})
        self.assertEqual(response.status_code, 302)
        goal = SavingsGoal.objects.get(name='Laptop')
        self.assertEqual(goal.user, self.user)
        self.client.post(reverse('goal-edit', args=[goal.uuid]), {
            'name': 'Laptop Pro', 'target_amount': '60000', 'currency': '₹', 'icon': '💻', 'color': 'success'})
        goal.refresh_from_db()
        self.assertEqual((goal.name, goal.target_amount), ('Laptop Pro', D('60000.00')))
        listing = self.client.get(reverse('goal-list'))
        self.assertEqual([g.name for g in listing.context['goals']], ['Laptop Pro'])

    def test_contribution_flow_through_the_detail_page(self):
        goal = self.goal()
        response = self.client.post(reverse('goal-detail', args=[goal.uuid]), {
            'account': self.cash.pk, 'amount': '250', 'date': today().isoformat()})
        self.assertEqual(response.status_code, 302)
        goal.refresh_from_db()
        self.assertEqual((goal.current_amount, self.bal(self.cash)), (D('250.00'), D('99750.00')))

    def test_invalid_contribution_shows_errors_and_changes_nothing(self):
        goal = self.goal()
        response = self.client.post(reverse('goal-detail', args=[goal.uuid]), {
            'account': '', 'amount': '250', 'date': today().isoformat()})
        self.assertEqual(response.status_code, 200)
        self.assertIn('account', response.context['form'].errors)
        self.assertFalse(GoalContribution.objects.exists())

    def test_a_failed_exchange_rate_is_a_clear_error_not_a_crash(self):
        goal = self.goal(currency='$')
        with patch('expenses.models.get_exchange_rate', side_effect=RuntimeError('fx down')):
            response = self.client.post(reverse('goal-detail', args=[goal.uuid]), {
                'account': self.cash.pk, 'amount': '5', 'date': today().isoformat()})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['form'].non_field_errors())
        self.assertFalse(GoalContribution.objects.exists())

    def test_other_users_goals_and_contributions_are_off_limits(self):
        other = self.make_user('goal-other2')
        theirs = SavingsGoal.objects.create(user=other, name='Theirs', target_amount=D('10'), currency='₹')
        mine_account = self.cash
        c = GoalContribution.objects.create(goal=theirs, amount=D('1'))
        for name, args in (('goal-detail', [theirs.uuid]), ('goal-edit', [theirs.uuid]), ('goal-delete', [theirs.uuid]),
                           ('goal-contribution-edit', [c.uuid]), ('goal-contribution-delete', [c.uuid])):
            self.assertEqual(self.client.get(reverse(name, args=args)).status_code, 404, name)
        self.assertEqual(self.client.post(reverse('goal-detail', args=[theirs.uuid]), {
            'account': mine_account.pk, 'amount': '5', 'date': today().isoformat()}).status_code, 404)

    def test_contribution_edit_and_delete_views(self):
        goal = self.goal()
        c = self.contribute(goal, 300)
        response = self.client.post(reverse('goal-contribution-edit', args=[c.uuid]), {
            'account': self.cash.pk, 'amount': '400', 'date': today().isoformat()})
        self.assertEqual(response.status_code, 302)
        goal.refresh_from_db()
        self.assertEqual(goal.current_amount, D('400.00'))
        response = self.client.post(reverse('goal-contribution-delete', args=[c.uuid]))
        self.assertEqual(response.status_code, 302)
        goal.refresh_from_db()
        self.assertEqual((goal.current_amount, self.bal(self.cash)), (D('0.00'), D('100000.00')))
        self.assertIn('Contribution deleted successfully!', self.messages(response))

    def test_goal_delete_confirms_and_reports(self):
        goal = self.goal()
        self.contribute(goal, 300)
        self.assertEqual(self.client.get(reverse('goal-delete', args=[goal.uuid])).status_code, 200)
        response = self.client.post(reverse('goal-delete', args=[goal.uuid]))
        self.assertFalse(SavingsGoal.objects.filter(pk=goal.pk).exists())
        self.assertEqual(self.bal(self.cash), D('100000.00'))
        self.assertIn('Savings goal deleted successfully.', self.messages(response))

    def test_contribution_delete_next_redirect_is_same_site_only(self):
        goal = self.goal()
        c = self.contribute(goal, 10)
        c2 = self.contribute(goal, 10)
        good = self.client.post(reverse('goal-contribution-delete', args=[c.uuid]), {'next': '/goals/'})
        self.assertEqual(good.url, '/goals/')
        bad = self.client.post(reverse('goal-contribution-delete', args=[c2.uuid]), {'next': 'https://evil.example/'})
        self.assertNotIn('evil', bad.url)

    def test_login_is_required(self):
        goal = self.goal()
        self.client.logout()
        for name, args in (('goal-list', []), ('goal-create', []), ('goal-detail', [goal.uuid])):
            self.assertEqual(self.client.get(reverse(name, args=args)).status_code, 302, name)


class TestListTotals(GoalBase):
    def test_total_saved_is_in_the_users_currency(self):
        self.seed_fx()
        inr = self.goal(name='A')
        usd = self.goal(name='B', target='500', currency='$')
        self.contribute(inr, 1000)
        self.contribute(usd, 10)                              # $10 = 800 rupees
        ctx = self.client.get(reverse('goal-list')).context
        self.assertEqual(ctx['total_saved'], D('1800.00'))

    def test_total_saved_with_no_goals_is_zero(self):
        self.assertEqual(self.client.get(reverse('goal-list')).context['total_saved'], 0)


class TestPlanLocks(GoalBase):
    tier = 'FREE'

    def test_only_the_first_goal_is_usable_on_the_free_plan(self):
        first = self.goal(name='One')
        second = self.goal(name='Two')
        self.assertFalse(self.user.profile.is_goal_locked(first))
        self.assertTrue(self.user.profile.is_goal_locked(second))

    def test_cannot_add_edit_or_contribute_to_a_locked_goal(self):
        self.goal(name='One')
        locked = self.goal(name='Two')
        self.assertEqual(self.client.get(reverse('goal-edit', args=[locked.uuid])).status_code, 302)
        response = self.client.post(reverse('goal-detail', args=[locked.uuid]), {
            'account': self.cash.pk, 'amount': '5', 'date': today().isoformat()})
        self.assertEqual(response.status_code, 302)
        self.assertFalse(GoalContribution.objects.exists())

    def test_cannot_edit_or_delete_contributions_of_a_locked_goal(self):
        self.goal(name='One')
        locked = self.goal(name='Two')
        c = GoalContribution.objects.create(goal=locked, amount=D('10'), account=self.cash)
        edit = self.client.post(reverse('goal-contribution-edit', args=[c.uuid]), {
            'account': self.cash.pk, 'amount': '99', 'date': today().isoformat()})
        self.assertEqual(edit.status_code, 302)
        c.refresh_from_db()
        self.assertEqual(c.amount, D('10.00'))
        delete = self.client.post(reverse('goal-contribution-delete', args=[c.uuid]))
        self.assertEqual(delete.status_code, 302)
        self.assertTrue(GoalContribution.objects.filter(pk=c.pk).exists())
        self.assertEqual(self.bal(self.cash), D('100000.00') - D('10.00'))

    def test_a_locked_goal_can_still_be_deleted_to_free_a_slot(self):
        self.goal(name='One')
        locked = self.goal(name='Two')
        self.client.post(reverse('goal-delete', args=[locked.uuid]))
        self.assertFalse(SavingsGoal.objects.filter(pk=locked.pk).exists())

    def test_creating_past_the_limit_is_refused(self):
        self.goal(name='One')
        response = self.client.post(reverse('goal-create'), {
            'name': 'Two', 'target_amount': '5', 'currency': '₹', 'icon': 'x', 'color': 'primary'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(SavingsGoal.objects.count(), 1)


class TestCompletedFlagRepair(GoalBase):
    def test_migration_repairs_wrong_flags_and_leaves_right_ones_alone(self):
        import importlib
        from django.apps import apps
        migration = importlib.import_module('expenses.migrations.0101_fix_goal_completed_flag')
        reached = self.goal(name='Reached', target='100')
        open_goal = self.goal(name='Open', target='100')
        zero = self.goal(name='Zero', target='100')
        SavingsGoal.objects.filter(pk=reached.pk).update(current_amount=D('100'), is_completed=False)   # stale: open
        SavingsGoal.objects.filter(pk=open_goal.pk).update(current_amount=D('40'), is_completed=True)   # stale: done
        migration.fix_completed_flag(apps, None)
        flags = dict(SavingsGoal.objects.values_list('name', 'is_completed'))
        self.assertEqual(flags, {'Reached': True, 'Open': False, 'Zero': False})
