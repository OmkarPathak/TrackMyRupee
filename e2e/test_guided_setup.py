"""Guided Setup: every flow can be opened, walked through to its review, confirmed, and leaves its records behind.

The flows are read from the registry, so a flow added later is covered without touching this file.
"""
from datetime import timedelta

from django.urls import reverse
from django.utils import timezone

from e2e.base import E2ETestCase, make_account, make_user

# Fills every empty field of the step on screen with something the form accepts, by what the field is called.
FILL_STEP = """
([today, later, flowKey]) => {
  const step = Array.from(document.querySelectorAll('.wizard-step-content'))
    .find(el => el.offsetParent !== null && el.dataset.stepKey !== 'review');
  if (!step) return;
  const has = (name, words) => words.some(w => name.includes(w));
  for (const el of step.querySelectorAll('input, select, textarea')) {
    const type = (el.getAttribute('type') || el.tagName).toLowerCase();
    const name = el.getAttribute('name') || '';
    if (!name || ['hidden', 'checkbox', 'radio', 'submit', 'button'].includes(type)) continue;
    if (el.value) continue;
    let value;
    if (el.tagName === 'SELECT') {
      const option = Array.from(el.options).find(o => o.value);
      if (!option) continue;
      value = option.value;
    } else if (type === 'date') {
      value = has(name, ['maturity', 'end', 'expiry', 'due', 'target']) ? later : today;
    } else if (has(name, ['rate'])) {
      value = '8';
    } else if (has(name, ['percent', 'ratio', 'share'])) {
      value = '10';
    } else if (has(name, ['tenure', 'months'])) {
      value = '24';
    } else if (has(name, ['day', 'date'])) {
      value = '15';                                    // (a day of the month: the date fields have type=date)
    } else if (type === 'number') {
      value = '50000';
    } else if (name === 'name' || has(name, ['name', 'title'])) {
      value = 'E2E ' + flowKey;
    } else if (type === 'email') {
      value = 'e2e-invite@example.com';
    } else {
      value = 'E2E test';
    }
    el.value = value;
    el.dispatchEvent(new Event('input', { bubbles: true }));
    el.dispatchEvent(new Event('change', { bubbles: true }));
  }
}
"""


class Base(E2ETestCase):
    def setUp(self):
        super().setUp()
        self.user = make_user('guided')
        self.bank = make_account(self.user, 'HDFC Savings', 'SAVINGS_ACCOUNT', 500000)
        self.today = timezone.localdate()

    def walk_to_review(self, page, key, *, fill=True):
        """Go through the wizard with Next until its review step shows."""
        form = page.locator(f'#tmr-flow-form-{key}')
        review = page.locator(f'#tmr-review-{key}')
        for _step in range(12):
            if review.is_visible():
                return
            if fill:
                page.evaluate(FILL_STEP, [self.today.isoformat(), (self.today + timedelta(days=365)).isoformat(), key])
            form.locator('button:has-text("Next") >> visible=true').first.click()
            page.wait_for_timeout(400)
        self.fail(f'the {key} setup never reached its review step')

    def confirm(self, page):
        page.locator('[data-confirm-btn]').first.click()


class EveryFlowTests(Base):
    def available_flows(self):
        from expenses.flows.registry import FlowRegistry
        return [flow for flow in FlowRegistry.all().values() if flow.is_available(self.user)]

    def test_the_landing_page_lists_every_available_setup_and_each_opens(self):
        page = self.new_page(self.user)
        self.open(page, reverse('flow-landing'))
        flows = self.available_flows()
        self.assertGreaterEqual(len(flows), 8)
        for flow in flows:
            self.open(page, reverse('flow-detail', kwargs={'key': flow.key}))
            self.assertTrue(page.locator(f'#tmr-flow-form-{flow.key}').count(), f'{flow.key} has no form')
            self.assertTrue(page.locator('.wizard-step-content').count(), f'{flow.key} shows no steps')
            self.assertTrue(page.locator('button:has-text("Next") >> visible=true').count(), f'{flow.key} has no Next')

    def test_every_setup_reaches_its_review_and_creates_its_records_when_confirmed(self):
        from expenses.models import FinancialFlow
        failures = []
        for flow in self.available_flows():
            page = self.new_page(self.user)
            try:
                self.open(page, reverse('flow-detail', kwargs={'key': flow.key}))
                self.walk_to_review(page, flow.key)
                self.review_text(page, flow.key, 'WHAT WE')
                self.confirm(page)
                self.wait_until(lambda key=flow.key: FinancialFlow.objects.filter(user=self.user, flow_key=key).exists(),
                                f'{flow.key} created nothing', seconds=15)
            except AssertionError as exc:
                failures.append(f'{flow.key}: {exc}')
            finally:
                page.context.close()
                self._contexts.remove(page.context)
        self.assertEqual(failures, [], 'these setups could not be completed:\n' + '\n'.join(failures))

    def test_the_review_can_go_back_and_change_an_answer(self):
        page = self.new_page(self.user)
        self.open(page, reverse('flow-detail', kwargs={'key': 'sip'}))
        self.walk_to_review(page, 'sip')
        page.locator('#tmr-flow-form-sip button:has-text("Back") >> visible=true').first.click()
        page.wait_for_timeout(400)
        self.assertFalse(page.locator('#tmr-review-sip').is_visible())

    def test_a_setup_opens_and_walks_on_a_phone(self):
        page = self.new_page(self.user, phone=True)
        self.open(page, reverse('flow-detail', kwargs={'key': 'sip'}))
        self.walk_to_review(page, 'sip')
        self.assertTrue(page.locator('#tmr-review-sip').is_visible())


class SipFundsTests(Base):
    def setUp(self):
        super().setUp()
        from expenses.models import AMFIScheme
        AMFIScheme.objects.create(scheme_code='120596', scheme_name='ICICI Prudential Large & Mid Cap Fund - Direct Plan - Growth')
        AMFIScheme.objects.create(scheme_code='120716', scheme_name='UTI Nifty 50 Index Fund - Direct Plan - Growth')

    def pick_fund(self, page, row, typed, amount):
        rows = page.locator('#flowSplitRows .split-row')
        rows.nth(row).locator('[data-f=name]').fill(typed)
        suggestion = rows.nth(row).locator('[data-f=suggest] a').first
        suggestion.wait_for()
        suggestion.click()
        rows.nth(row).locator('[data-f=amount]').fill(amount)

    def test_choosing_funds_in_the_setup_creates_the_split_and_the_first_instalment_buys_them(self):
        from expenses.models import Account, Holding, RecurringFundSplit
        page = self.new_page(self.user)
        self.open(page, reverse('flow-detail', kwargs={'key': 'sip'}))
        form = page.locator('#tmr-flow-form-sip')
        self.set_select(page, 'select[name="instrument_type"]', 'SIP')
        page.fill('input[name="name"]', 'Index SIP')
        page.fill('input[name="amount"]', '15000')
        self.set_select(page, 'select[name="from_account"]', str(self.bank.pk))
        page.fill('input[name="deposit_start_date"]', (self.today + timedelta(days=3)).isoformat())
        form.locator('button:has-text("Next") >> visible=true').first.click()
        page.wait_for_selector('#flowSplitAdd')                              # the Funds step

        page.click('#flowSplitAdd')
        self.pick_fund(page, 0, 'icici large and midcap direct', '10000')    # typed the way people type it
        page.click('#flowSplitAdd')
        self.pick_fund(page, 1, 'uti nifty 50', '5000')
        self.assertIn('15000.00 of 15000.00', page.inner_text('#flowSplitSummary'))
        form.locator('button:has-text("Next") >> visible=true').first.click()

        page.wait_for_selector('#tmr-review-sip', state='visible')
        review = self.review_text(page, 'sip', 'ICICI Prudential Large & Mid Cap Fund', 'UTI Nifty 50 Index Fund')
        self.assertIn('ICICI Prudential Large & Mid Cap Fund', review)
        self.assertIn('UTI Nifty 50 Index Fund', review)
        self.confirm(page)
        self.wait_until(lambda: RecurringFundSplit.objects.count() == 2, 'the fund split was not saved', seconds=15)
        account = Account.objects.get(user=self.user, account_type='MUTUAL_FUND')
        self.assertEqual({h.scheme_code for h in Holding.objects.filter(account=account)}, {'120596', '120716'})

    def test_too_much_for_the_instalment_is_flagged_before_continuing(self):
        page = self.new_page(self.user)
        self.open(page, reverse('flow-detail', kwargs={'key': 'sip'}))
        page.fill('input[name="name"]', 'Index SIP')
        page.fill('input[name="amount"]', '1000')
        self.set_select(page, 'select[name="from_account"]', str(self.bank.pk))
        page.locator('#tmr-flow-form-sip button:has-text("Next") >> visible=true').first.click()
        page.click('#flowSplitAdd')
        self.pick_fund(page, 0, 'uti nifty', '5000')
        self.assertIn('more than the instalment', page.inner_text('#flowSplitSummary'))

    def test_a_recurring_deposit_has_no_funds_step(self):
        page = self.new_page(self.user)
        self.open(page, reverse('flow-detail', kwargs={'key': 'sip'}))
        self.set_select(page, 'select[name="instrument_type"]', 'RD')
        page.wait_for_timeout(300)
        self.assertFalse(page.locator('.wizard-step-content[data-step-key="sip_funds"]').is_visible())
        page.fill('input[name="name"]', 'Monthly RD')
        page.fill('input[name="amount"]', '2000')
        self.set_select(page, 'select[name="from_account"]', str(self.bank.pk))
        page.locator('#tmr-flow-form-sip button:has-text("Next") >> visible=true').first.click()
        page.wait_for_timeout(400)
        self.assertFalse(page.locator('#flowSplitAdd').is_visible())
