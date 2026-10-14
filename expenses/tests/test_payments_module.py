"""Audit of checkout: what a payment buys, what it cannot be made to buy, and that it is applied once."""
import json
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from expenses.models import PaymentHistory, SubscriptionPlan

RZP = dict(RAZORPAY_KEY_ID='k', RAZORPAY_KEY_SECRET='s')


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend', **RZP)
class CheckoutBase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='buyer', email='buyer@example.com', password='pw')
        self.client.force_login(self.user)

    @property
    def profile(self):
        self.user.profile.refresh_from_db()
        return self.user.profile

    def order(self, plan='PLUS', duration='YEARLY', recurring=False):
        with patch('expenses.views_payment.razorpay.Client') as client:
            client.return_value.order.create.return_value = {'id': f'order_{PaymentHistory.objects.count() + 1}'}
            client.return_value.subscription.create.return_value = {'id': f'sub_{PaymentHistory.objects.count() + 1}'}
            return self.client.post(reverse('create-order'), json.dumps(
                {'plan_type': plan, 'duration': duration, 'is_recurring': recurring}), content_type='application/json')

    def verify(self, order_id, payment_id=None, subscription=False):
        key = 'razorpay_subscription_id' if subscription else 'razorpay_order_id'
        with patch('expenses.views_payment.razorpay.Client'):
            return self.client.post(reverse('verify-payment'), json.dumps({
                key: order_id, 'razorpay_payment_id': payment_id or f'pay_{order_id}', 'razorpay_signature': 'x'}),
                content_type='application/json')

    def buy(self, plan='PLUS', duration='YEARLY'):
        resp = self.order(plan, duration)
        self.assertEqual(resp.status_code, 200, resp.content)
        return self.verify(resp.json()['id'])


class WhatAPaymentBuysTests(CheckoutBase):
    def test_a_yearly_payment_gives_a_year_from_now(self):
        self.buy('PLUS', 'YEARLY')
        end = self.profile.subscription_end_date
        self.assertEqual(self.profile.tier, 'PLUS')
        self.assertAlmostEqual((end - timezone.now()).days, 365, delta=1)

    def test_a_monthly_payment_gives_thirty_days(self):
        self.buy('PRO', 'MONTHLY')
        self.assertAlmostEqual((self.profile.subscription_end_date - timezone.now()).days, 30, delta=1)

    def test_paying_during_the_free_trial_gives_the_full_paid_period_not_the_trial_days_left(self):
        self.client.post(reverse('start-trial'))
        self.assertAlmostEqual((self.profile.subscription_end_date - timezone.now()).days, 7, delta=1)
        self.buy('PLUS', 'YEARLY')
        self.assertAlmostEqual((self.profile.subscription_end_date - timezone.now()).days, 365, delta=1)

    def test_paying_again_while_a_paid_plan_is_active_adds_to_it_instead_of_being_lost(self):
        self.buy('PLUS', 'YEARLY')
        first_end = self.profile.subscription_end_date
        self.buy('PLUS', 'YEARLY')
        self.assertAlmostEqual((self.profile.subscription_end_date - first_end).days, 365, delta=1)

    def test_upgrading_tier_mid_term_switches_tier_and_keeps_the_time_already_paid_for(self):
        self.buy('PLUS', 'YEARLY')
        first_end = self.profile.subscription_end_date
        self.buy('PRO', 'MONTHLY')
        self.assertEqual(self.profile.tier, 'PRO')
        self.assertGreaterEqual(self.profile.subscription_end_date, first_end)

    def test_an_expired_plan_restarts_from_now(self):
        self.user.profile.tier = 'PLUS'
        self.user.profile.subscription_end_date = timezone.now() - timedelta(days=40)
        self.user.profile.save()
        PaymentHistory.objects.create(user=self.user, order_id='old', payment_id='p', amount=49, tier='PLUS',
                                      duration='MONTHLY', status='SUCCESS')
        self.buy('PLUS', 'MONTHLY')
        self.assertAlmostEqual((self.profile.subscription_end_date - timezone.now()).days, 30, delta=1)


class WhatCannotBeBoughtTests(CheckoutBase):
    def test_lifetime_is_not_for_sale_through_checkout(self):
        resp = self.order('PRO', 'LIFETIME')
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(PaymentHistory.objects.count(), 0)

    def test_the_free_plan_cannot_be_ordered(self):
        self.assertEqual(self.order('FREE', 'YEARLY').status_code, 400)
        self.assertEqual(self.order('GOLD', 'YEARLY').status_code, 400)
        self.assertEqual(PaymentHistory.objects.count(), 0)

    def test_the_order_amount_comes_from_the_server_price_list_not_the_browser(self):
        from finance_tracker.plans import PLAN_DETAILS
        with patch('expenses.views_payment.razorpay.Client') as client:
            client.return_value.order.create.return_value = {'id': 'order_x'}
            self.client.post(reverse('create-order'), json.dumps(
                {'plan_type': 'PLUS', 'duration': 'MONTHLY', 'is_recurring': False, 'amount': 1}),
                content_type='application/json')
            sent = client.return_value.order.create.call_args.kwargs['data']
        self.assertEqual(sent['amount'], PLAN_DETAILS['PLUS']['price_monthly'] * 100)
        self.assertEqual(PaymentHistory.objects.get().tier, 'PLUS')

    def test_the_tier_activated_is_the_one_ordered_not_one_named_at_verification(self):
        resp = self.order('PLUS', 'MONTHLY')
        with patch('expenses.views_payment.razorpay.Client'):
            self.client.post(reverse('verify-payment'), json.dumps({
                'razorpay_order_id': resp.json()['id'], 'razorpay_payment_id': 'p', 'razorpay_signature': 'x',
                'plan_type': 'PRO'}), content_type='application/json')
        self.assertEqual(self.profile.tier, 'PLUS')

    def test_a_failed_signature_activates_nothing(self):
        import razorpay
        resp = self.order('PRO', 'YEARLY')
        with patch('expenses.views_payment.razorpay.Client') as client:
            client.return_value.utility.verify_payment_signature.side_effect = razorpay.errors.SignatureVerificationError('bad')
            r = self.client.post(reverse('verify-payment'), json.dumps({
                'razorpay_order_id': resp.json()['id'], 'razorpay_payment_id': 'p', 'razorpay_signature': 'x'}),
                content_type='application/json')
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.profile.tier, 'FREE')
        self.assertEqual(PaymentHistory.objects.get().status, 'FAILED')

    def test_verifying_twice_applies_the_payment_once(self):
        resp = self.order('PLUS', 'MONTHLY')
        self.verify(resp.json()['id'])
        end = self.profile.subscription_end_date
        self.verify(resp.json()['id'])
        self.assertEqual(self.profile.subscription_end_date, end)

    def test_anonymous_users_cannot_order_or_verify(self):
        self.client.logout()
        self.assertEqual(self.client.post(reverse('create-order'), '{}', content_type='application/json').status_code, 302)
        self.assertEqual(self.client.post(reverse('verify-payment'), '{}', content_type='application/json').status_code, 302)


class WebhookTests(CheckoutBase):
    def post(self, event, sub_id='sub_w', payment_id='pay_w', amount=4900, signature_ok=True):
        end = int((timezone.now() + timedelta(days=30)).timestamp())
        payload = {'event': event, 'payload': {
            'subscription': {'entity': {'id': sub_id, 'current_end': end}},
            'payment': {'entity': {'id': payment_id, 'amount': amount}}}}
        with patch('expenses.views_payment.razorpay.Client') as client, self.settings(RAZORPAY_WEBHOOK_SECRET='w'):
            if not signature_ok:
                import razorpay
                client.return_value.utility.verify_webhook_signature.side_effect = razorpay.errors.SignatureVerificationError('bad')
            return self.client.post(reverse('razorpay-webhook'), json.dumps(payload),
                                    content_type='application/json', HTTP_X_RAZORPAY_SIGNATURE='sig')

    def subscribed(self, tier='PLUS'):
        p = self.user.profile
        p.tier, p.razorpay_subscription_id = tier, 'sub_w'
        p.subscription_end_date = timezone.now() + timedelta(days=10)
        p.save()
        PaymentHistory.objects.create(user=self.user, order_id='sub_w', payment_id='pay_0', amount=49, tier=tier,
                                      duration='MONTHLY', status='SUCCESS')

    def test_a_forged_webhook_changes_nothing(self):
        self.subscribed()
        before = self.profile.subscription_end_date
        self.assertEqual(self.post('subscription.charged', signature_ok=False).status_code, 400)
        self.assertEqual(self.profile.subscription_end_date, before)
        self.assertEqual(PaymentHistory.objects.count(), 1)

    def test_a_webhook_with_no_secret_configured_is_refused(self):
        with patch('expenses.views_payment.razorpay.Client'), self.settings(RAZORPAY_WEBHOOK_SECRET=''):
            r = self.client.post(reverse('razorpay-webhook'), '{}', content_type='application/json')
        self.assertEqual(r.status_code, 400)

    def test_a_renewal_extends_the_plan_and_is_logged_once_even_if_delivered_twice(self):
        self.subscribed()
        self.post('subscription.charged', payment_id='pay_renew')
        self.post('subscription.charged', payment_id='pay_renew')
        self.assertAlmostEqual((self.profile.subscription_end_date - timezone.now()).days, 30, delta=1)
        self.assertEqual(PaymentHistory.objects.filter(payment_id='pay_renew').count(), 1)

    def test_a_renewal_resets_the_expiry_reminder(self):
        self.subscribed()
        UserProfile = type(self.user.profile)
        UserProfile.objects.filter(user=self.user).update(expiry_reminder_sent=True)
        self.post('subscription.charged', payment_id='pay_renew2')
        self.assertFalse(self.profile.expiry_reminder_sent)

    def test_repeated_payment_failure_ends_access_immediately(self):
        self.subscribed('PRO')
        self.post('subscription.halted')
        self.assertEqual(self.profile.active_tier, 'FREE')
        self.assertEqual(self.profile.tier, 'FREE')

    def test_all_billing_cycles_used_downgrades(self):
        self.subscribed('PRO')
        self.post('subscription.completed')
        self.assertEqual(self.profile.tier, 'FREE')

    def test_a_cancellation_keeps_access_until_the_paid_period_ends(self):
        self.subscribed('PLUS')
        self.post('subscription.cancelled')
        self.assertEqual(self.profile.active_tier, 'PLUS')

    def test_a_retry_in_progress_changes_nothing(self):
        self.subscribed('PLUS')
        before = self.profile.subscription_end_date
        self.assertEqual(self.post('subscription.pending').status_code, 200)
        self.assertEqual(self.profile.subscription_end_date, before)

    def test_a_charge_for_an_unknown_subscription_is_acknowledged_without_activating_anyone(self):
        self.assertEqual(self.post('subscription.charged', sub_id='sub_unknown').status_code, 200)
        self.assertEqual(PaymentHistory.objects.count(), 0)
        self.assertEqual(self.profile.tier, 'FREE')

    def test_an_unhandled_event_is_acknowledged(self):
        self.assertEqual(self.post('payment.authorized').status_code, 200)

    def test_get_is_not_allowed(self):
        self.assertEqual(self.client.get(reverse('razorpay-webhook')).status_code, 405)


class ExpiryTests(CheckoutBase):
    def set_plan(self, tier, end, lifetime=False):
        p = self.user.profile
        p.tier, p.subscription_end_date, p.is_lifetime = tier, end, lifetime
        p.save()

    def test_a_plan_past_its_end_date_behaves_as_free_at_once(self):
        self.set_plan('PRO', timezone.now() - timedelta(minutes=1))
        p = self.profile
        self.assertEqual(p.active_tier, 'FREE')
        self.assertTrue(p.subscription_expired)
        self.assertFalse(p.can_export_csv)
        self.assertFalse(p.can_track_loans)

    def test_an_active_plan_has_its_features(self):
        self.set_plan('PLUS', timezone.now() + timedelta(days=1))
        p = self.profile
        self.assertEqual(p.active_tier, 'PLUS')
        self.assertTrue(p.can_export_csv)
        self.assertTrue(p.can_track_loans)
        self.assertFalse(p.subscription_expired)

    def test_lifetime_never_expires(self):
        self.set_plan('PRO', timezone.now() - timedelta(days=900), lifetime=True)
        self.assertEqual(self.profile.active_tier, 'PRO')

    def test_trial_is_offered_once_and_only_to_free_accounts(self):
        self.assertTrue(self.profile.can_start_trial)
        self.assertEqual(self.client.post(reverse('start-trial')).status_code, 200)
        self.assertEqual(self.profile.active_tier, 'PRO')
        self.assertEqual(self.client.post(reverse('start-trial')).status_code, 400)
        self.set_plan('FREE', None)
        self.assertEqual(self.client.post(reverse('start-trial')).status_code, 400)  # already used

    def test_a_paying_user_cannot_start_a_trial(self):
        self.set_plan('PLUS', timezone.now() + timedelta(days=30))
        self.assertEqual(self.client.post(reverse('start-trial')).status_code, 400)


class FeatureGatingTests(CheckoutBase):
    def tier(self, tier):
        p = self.user.profile
        p.tier, p.subscription_end_date = tier, (timezone.now() + timedelta(days=30) if tier != 'FREE' else None)
        p.save()

    def test_export_is_paid_only_on_both_entry_points(self):
        for name, method in (('export-expenses', 'get'), ('export-data', 'post')):
            r = getattr(self.client, method)(reverse(name))
            self.assertRedirects(r, reverse('pricing'), fetch_redirect_response=False)
        self.tier('PLUS')
        self.assertEqual(self.client.get(reverse('export-expenses')).status_code, 200)

    def test_year_in_review_is_paid_only(self):
        self.assertRedirects(self.client.get(reverse('year_in_review', args=[2025])), reverse('pricing'),
                             fetch_redirect_response=False)
        self.tier('PLUS')
        self.assertEqual(self.client.get(reverse('year_in_review', args=[2025])).status_code, 200)

    def test_net_worth_history_is_limited_by_plan(self):
        from finance_tracker.plans import get_limit
        for tier, months in (('FREE', 2), ('PLUS', 6)):
            self.tier(tier)
            self.assertEqual(self.profile.net_worth_history_limit, months)
        self.tier('PRO')
        self.assertEqual(self.profile.net_worth_history_limit, -1)
        self.assertEqual(get_limit('FREE', 'net_worth_history'), 2)

    def test_the_inline_category_creator_stops_at_the_plan_limit(self):
        from expenses.models import Category
        from finance_tracker.plans import get_limit
        limit = get_limit('FREE', 'budget_categories')
        existing = Category.objects.filter(user=self.user).count()
        for i in range(limit - existing):
            Category.objects.create(user=self.user, name=f'C{i}')
        r = self.client.post(reverse('category-create-ajax'), json.dumps({'name': 'One too many'}),
                             content_type='application/json')
        self.assertEqual(r.status_code, 403)
        self.assertFalse(Category.objects.filter(user=self.user, name='One too many').exists())
        self.tier('PLUS')
        r = self.client.post(reverse('category-create-ajax'), json.dumps({'name': 'One too many'}),
                             content_type='application/json')
        self.assertTrue(r.json()['success'])


class PlanTableTests(TestCase):
    """The price list and limits are one table; the rest of the app and the emails read it."""

    def test_each_higher_plan_is_at_least_as_generous_as_the_one_below(self):
        from finance_tracker.plans import PLAN_DETAILS
        def rank(v):
            return float('inf') if v == -1 else v
        for key in ('accounts', 'loans', 'recurring_transactions', 'budget_categories', 'savings_goals',
                    'expenses_per_month', 'net_worth_history'):
            values = [rank(PLAN_DETAILS[t]['limits'][key]) for t in ('FREE', 'PLUS', 'PRO')]
            self.assertEqual(values, sorted(values), key)

    def test_a_year_costs_less_than_twelve_months_and_pro_more_than_plus(self):
        from finance_tracker.plans import PLAN_DETAILS
        for tier in ('PLUS', 'PRO'):
            self.assertLess(PLAN_DETAILS[tier]['price_yearly'], PLAN_DETAILS[tier]['price_monthly'] * 12)
        self.assertGreater(PLAN_DETAILS['PRO']['price_monthly'], PLAN_DETAILS['PLUS']['price_monthly'])
        self.assertGreater(PLAN_DETAILS['PRO']['price_yearly'], PLAN_DETAILS['PLUS']['price_yearly'])

    def test_unknown_tiers_get_the_free_limits(self):
        from finance_tracker.plans import get_limit
        self.assertEqual(get_limit('NOPE', 'accounts'), get_limit('FREE', 'accounts'))


class SecondSubscriptionTests(CheckoutBase):
    def setUp(self):
        super().setUp()
        for tier, duration, price in (('PLUS', 'MONTHLY', 49), ('PRO', 'MONTHLY', 99)):
            SubscriptionPlan.objects.create(tier=tier, duration=duration, name=f'{tier} {duration}', price=price,
                                            razorpay_plan_id=f'plan_{tier}_{duration}')

    def start_subscription(self, tier='PLUS'):
        resp = self.order(tier, 'MONTHLY', recurring=True)
        self.assertEqual(resp.json()['type'], 'SUBSCRIPTION', resp.content)
        return self.verify(resp.json()['id'], subscription=True)

    def test_an_active_subscription_blocks_starting_another_that_would_bill_twice(self):
        self.start_subscription('PLUS')
        r = self.order('PRO', 'MONTHLY', recurring=True)
        self.assertEqual(r.status_code, 400)
        self.assertIn('Cancel', r.json()['error'])
        self.assertEqual(PaymentHistory.objects.count(), 1)

    def test_a_one_time_payment_is_still_possible_while_subscribed(self):
        self.start_subscription('PLUS')
        self.assertEqual(self.order('PRO', 'MONTHLY', recurring=False).status_code, 200)

    def test_after_cancelling_a_new_subscription_can_be_started(self):
        self.start_subscription('PLUS')
        type(self.user.profile).objects.filter(user=self.user).update(cancel_at_cycle_end=True)
        self.assertEqual(self.order('PRO', 'MONTHLY', recurring=True).status_code, 200)


class PriceConsistencyTests(TestCase):
    """The prices written into pages must be the prices charged (PLAN_DETAILS)."""

    def test_price_text_on_the_pricing_and_landing_pages_matches_the_price_list(self):
        from pathlib import Path

        from django.conf import settings

        from finance_tracker.plans import PLAN_DETAILS
        expected = [f"₹{PLAN_DETAILS['PLUS']['price_monthly']}/mo", f"₹{PLAN_DETAILS['PLUS']['price_yearly']}/yr",
                    f"₹{PLAN_DETAILS['PRO']['price_monthly']}/mo", f"₹{PLAN_DETAILS['PRO']['price_yearly']}/yr"]
        for name in ('templates/expenses/pricing.html', 'templates/landing.html'):
            text = (Path(settings.BASE_DIR) / name).read_text()
            for price in expected:
                self.assertIn(price, text, f'{name} does not mention {price}')
            self.assertNotIn('9999999999', text)
