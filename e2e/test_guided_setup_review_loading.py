"""The review of a Guided Setup holds the previous answer until the server's answer for the form as it is now arrives.
While it is on its way the review is shown faded and Confirm is held, so nobody confirms what they have not seen."""
from django.urls import reverse
from django.utils import timezone

from e2e.base import E2ETestCase, make_account, make_user

LOADING = '#tmr-review-fd.tmr-review-loading'
CONFIRM = '[data-confirm-btn] >> visible=true'


class Base(E2ETestCase):
    def setUp(self):
        super().setUp()
        self.user = make_user('review-loading')
        self.funding = make_account(self.user, 'HDFC Savings', 'SAVINGS_ACCOUNT', 3000000)
        self.today = timezone.localdate()
        self.held = []

    def hold_previews(self, page):
        """Keep every preview request on its way until `release` lets it through."""
        page.route('**/flows/fd/preview/', lambda route: self.held.append(route))

    def release(self, page, rounds=6):
        """Let held requests through, one at a time (the one queued behind it is sent when it has finished)."""
        for _ in range(rounds):
            if not self.held:
                page.wait_for_timeout(400)
                if not self.held:
                    return
            self.held.pop(0).continue_()
            page.wait_for_timeout(400)

    def fill_to_the_last_step(self, page):
        form = page.locator('#tmr-flow-form-fd')
        nxt = lambda: (form.locator('button:has-text("Next") >> visible=true').first.click(), page.wait_for_timeout(300))
        page.fill('input[name="name"]', 'Big FD')
        page.fill('input[name="principal"]', '100000')
        page.fill('input[name="annual_rate"]', '7')
        self.set_select(page, 'select[name="from_account"]', str(self.funding.pk))
        nxt()
        page.fill('input[name="deposit_start_date"]', self.today.isoformat())
        page.fill('input[name="maturity_date"]', self.today.replace(year=self.today.year + 2).isoformat())
        nxt()
        nxt()          # payout (left as it is)
        nxt()          # reporting
        return form


class ReviewLoadingTests(Base):
    def test_the_review_is_faded_and_confirm_is_held_until_the_answer_arrives(self):
        page = self.new_page(self.user)
        self.open(page, reverse('flow-detail', kwargs={'key': 'fd'}))
        self.hold_previews(page)
        self.fill_to_the_last_step(page)
        page.wait_for_selector('#tmr-review-fd', state='visible')
        page.wait_for_selector(LOADING)
        self.assertTrue(page.locator(CONFIRM).is_disabled())
        self.assertEqual(page.get_attribute('#tmr-review-fd', 'aria-busy'), 'true')
        self.release(page)
        page.wait_for_selector(LOADING, state='detached')
        self.assertFalse(page.locator(CONFIRM).is_disabled())
        self.assertEqual(page.get_attribute('#tmr-review-fd', 'aria-busy'), 'false')

    def test_an_answer_that_was_already_on_its_way_does_not_end_the_wait(self):
        """The earlier requests carry the form as it was; only the one sent after the review opened may end it."""
        page = self.new_page(self.user)
        self.open(page, reverse('flow-detail', kwargs={'key': 'fd'}))
        self.hold_previews(page)
        self.fill_to_the_last_step(page)
        page.wait_for_selector(LOADING)
        self.release(page, rounds=1)                     # the first held request finishes ...
        page.wait_for_timeout(500)
        self.assertTrue(page.locator(LOADING).count() == 1 or not self.held, 'the wait ended on an older answer')
        self.release(page)
        page.wait_for_selector(LOADING, state='detached')

    def test_a_failed_request_does_not_leave_confirm_held(self):
        page = self.new_page(self.user)
        self.open(page, reverse('flow-detail', kwargs={'key': 'fd'}))
        page.route('**/flows/fd/preview/', lambda route: route.abort())
        self.fill_to_the_last_step(page)
        page.wait_for_selector('#tmr-review-fd', state='visible')
        page.wait_for_selector(LOADING, state='detached', timeout=14000)
        self.assertFalse(page.locator(CONFIRM).is_disabled())

    def test_a_normal_review_ends_up_not_faded(self):
        page = self.new_page(self.user)
        self.open(page, reverse('flow-detail', kwargs={'key': 'fd'}))
        self.fill_to_the_last_step(page)
        page.wait_for_selector('#tmr-review-fd', state='visible')
        page.wait_for_selector(LOADING, state='detached')
        self.review_text(page, 'fd', 'Fixed deposit account')
        self.assertFalse(page.locator(CONFIRM).is_disabled())
