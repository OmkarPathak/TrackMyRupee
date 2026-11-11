"""Finding what a plan adds: the Settings list of what the next plan includes, and the plan tag on a Guided Setup
card the person's plan cannot finish."""

from django.contrib.auth.models import User
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from expenses.models import UserProfile
from finance_tracker.plans import PLAN_DETAILS, plan_unlocks


def user_on(tier, name=None):
    user = User.objects.create_user(name or f'plan-{tier.lower()}', password='pass')
    profile, _ = UserProfile.objects.get_or_create(user=user, defaults={'currency': '₹'})
    profile.tier = tier
    profile.has_seen_tutorial = True
    profile.save()
    user.refresh_from_db()      # drop the profile cached on the user: saving the user would write it back
    return user


class PlanUnlocksTests(SimpleTestCase):
    def test_free_is_told_about_loans_first_and_the_numbers_come_from_the_plan_table(self):
        lines = plan_unlocks('FREE')
        self.assertIn('Track loans', lines[0])
        accounts = PLAN_DETAILS['PLUS']['limits']['accounts']
        self.assertIn(f'Up to {accounts} accounts, instead of 2', lines)

    def test_plus_is_told_what_pro_adds(self):
        lines = plan_unlocks('PLUS')
        self.assertIn('Unlimited loans', lines)
        self.assertIn('Unlimited accounts, instead of 10', lines)
        self.assertFalse(any('Reminders' in line for line in lines))      # Plus already has them

    def test_the_top_plan_has_nothing_to_add(self):
        self.assertEqual(plan_unlocks('PRO'), [])


class SettingsPlanListTests(TestCase):
    def page(self, tier):
        self.client.force_login(user_on(tier))
        return self.client.get(reverse('settings-home'))

    def test_a_free_person_sees_what_plus_includes(self):
        response = self.page('FREE')
        self.assertContains(response, 'What Plus includes')
        self.assertContains(response, 'Track loans')

    def test_a_plus_person_sees_what_pro_includes(self):
        response = self.page('PLUS')
        self.assertContains(response, 'What Pro includes')
        self.assertContains(response, 'Unlimited loans')

    def test_a_pro_person_sees_no_list(self):
        self.assertNotContains(self.page('PRO'), 'includes</span>')


class GuidedSetupPlanTagTests(TestCase):
    def flows(self, tier):
        self.client.force_login(user_on(tier))
        response = self.client.get(reverse('tmr-flows'))
        self.assertEqual(response.status_code, 200)
        return response, {f['key']: f for f in response.context['flows']} if isinstance(response.context['flows'], list) \
            else {f['key']: f for f in __import__('json').loads(response.context['flows'])}

    def test_the_loan_card_says_plus_plan_on_the_free_plan(self):
        response, flows = self.flows('FREE')
        self.assertEqual(flows['loan']['needs_plan'], 'Plus plan')
        self.assertContains(response, 'bi-lock-fill')

    def test_only_the_cards_that_create_a_loan_carry_the_tag(self):
        _response, flows = self.flows('FREE')
        self.assertEqual({k for k, f in flows.items() if f['needs_plan']}, {'loan', 'car'})

    def test_a_plus_person_sees_no_tag(self):
        _response, flows = self.flows('PLUS')
        self.assertEqual(flows['loan']['needs_plan'], '')
