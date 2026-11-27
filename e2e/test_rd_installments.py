"""A Recurring Deposit's page: the installments paid, the ones still expected, and the maturity value."""
from decimal import Decimal

from dateutil.relativedelta import relativedelta
from django.urls import reverse
from django.utils import timezone

from e2e.base import E2ETestCase, make_account, make_user


class RdInstallmentsTests(E2ETestCase):
    def setUp(self):
        super().setUp()
        from expenses.models import Transfer
        self.user = make_user('rd-saver')
        self.bank = make_account(self.user, 'ICICI Savings', 'SAVINGS_ACCOUNT', 900000)
        today = timezone.localdate()
        start = (today.replace(day=1) - relativedelta(months=5)).replace(day=7)       # started five months ago
        self.rd = make_account(
            self.user, 'RD Baliraja', 'RD', 0, deposit_rate=Decimal('12'), deposit_compounding='SIMPLE',
            deposit_start_date=start, deposit_maturity_date=start + relativedelta(months=12),
            rd_installment_amount=Decimal('20000'), rd_installment_day=7)
        # the first two months were paid before the app tracked it; the rest are recorded transfers on the 1st
        first_recorded = start.replace(day=1) + relativedelta(months=2)
        month = first_recorded
        while month <= today:
            Transfer.objects.create(user=self.user, from_account=self.bank, to_account=self.rd, amount=Decimal('20000'),
                                    converted_amount=Decimal('20000'), date=month)
            month += relativedelta(months=1)

    def plan(self):
        from expenses.account_valuation import get_rd_plan
        return get_rd_plan(self.rd)

    def open_rd(self, phone=False):
        page = self.new_page(self.user, phone=phone)
        self.open(page, reverse('account-detail', kwargs={'pk': self.rd.uuid}))
        return page

    def test_the_card_shows_paid_expected_and_the_maturity_value_the_app_works_out(self):
        plan = self.plan()
        page = self.open_rd()
        card = page.locator('#rd-installments')
        card.wait_for()
        text = card.inner_text()
        self.assertIn(f'{plan["paid_count"]} of {plan["planned_count"]} paid', text)
        self.assertIn('Paid so far', text)
        self.assertIn('Still to come', text)
        self.assertGreater(plan['upcoming_count'], 0)                      # a year-long RD five months in has more to come
        self.assertEqual(card.locator('#rd-upcoming li').count(), plan['upcoming_count'])
        self.assertEqual(card.locator('.is-expected').count(), len([u for u in plan['upcoming'] if u['expected']]))
        self.assertIn(f'{int(plan["planned_total"]):,}'.replace(',', ''), text.replace(',', ''))    # the total put in
        self.assertIn('Expected on', text)

    def test_the_header_card_agrees_with_the_installments_card(self):
        plan = self.plan()
        page = self.open_rd()
        header = page.inner_text('body')
        self.assertIn(f'Installments: {plan["paid_count"]} of {plan["planned_count"]} paid', header.replace('\n', ' '))
        self.assertIn('Expected at maturity', header)

    def test_it_fits_on_a_phone(self):
        page = self.open_rd(phone=True)
        card = page.locator('#rd-installments')
        card.wait_for()
        box = card.bounding_box()
        self.assertLessEqual(box['x'] + box['width'], 390 + 1)               # inside the screen
        self.assertFalse(page.evaluate('() => document.documentElement.scrollWidth > window.innerWidth'),
                         'the page scrolls sideways')
