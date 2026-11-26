"""Recurring Deposit (RD): setting one up, how each installment earns, missed months, and the day-of-month edge cases.

e2e/test_rd_installments.py covers the Installments card; this file covers the figures and the form.
The expected amounts come from type_helpers.rd_value, worked out installment by installment independently of the app.
"""
from datetime import date
from decimal import Decimal

from dateutil.relativedelta import relativedelta
from django.urls import reverse
from django.utils import timezone

from e2e.base import E2ETestCase, make_account, make_user
from e2e.type_helpers import balance_on, labelled, monthly_dates, open_account, rd_value

D = Decimal
TODAY = timezone.localdate()


class Base(E2ETestCase):
    def assertMoney(self, shown, worked, msg=None):
        """Equal to the paisa-level rounding of adding up installments one by one (the app and a hand calculation differ by a few paise)."""
        self.assertLessEqual(abs(shown - worked), D('0.10'), msg or f'{shown} is not {worked}')

    def setUp(self):
        super().setUp()
        self.user = make_user('rd-edge')
        self.bank = make_account(self.user, 'HDFC Savings', 'SAVINGS_ACCOUNT', 500000)

    def rd(self, name='RD', amount=5000, day=5, rate=7, compounding='QUARTERLY', start=None, maturity=None, **extra):
        return make_account(self.user, name, 'RD', 0, deposit_rate=D(str(rate)), deposit_compounding=compounding,
                            deposit_start_date=start, deposit_maturity_date=maturity,
                            rd_installment_amount=D(amount), rd_installment_day=day, **extra)

    def pay(self, rd, on, amount=5000):
        from expenses.models import Transfer
        Transfer.objects.create(user=self.user, from_account=self.bank, to_account=rd, amount=D(amount),
                                converted_amount=D(amount), date=on)


class FormTests(Base):
    def fill(self, page, name='Edge RD', amount='5000', day='5', rate='7', start=None, maturity=None):
        self.open(page, reverse('account-create') + '?type=RD')
        page.fill('#id_name', name)
        page.click('#nextBtn')
        page.wait_for_selector('#id_rd_installment_amount', state='visible')
        for field, value in (('deposit_principal', '5000'), ('rd_installment_amount', amount), ('rd_installment_day', day), ('deposit_rate', rate),
                             ('deposit_start_date', start), ('deposit_maturity_date', maturity)):
            if value is not None:
                page.fill(f'#id_{field}', value if isinstance(value, str) else value.isoformat())
        page.locator('form button[type="submit"]:visible, form input[type="submit"]:visible').first.click()

    def test_a_recurring_deposit_is_created_with_its_schedule(self):
        from expenses.models import Account
        start = TODAY - relativedelta(months=3)
        page = self.new_page(self.user)
        self.fill(page, start=start, maturity=start + relativedelta(months=12))
        self.wait_until(lambda: Account.objects.filter(user=self.user, name='Edge RD').exists(), 'the RD was not saved', seconds=15)
        rd = Account.objects.get(user=self.user, name='Edge RD')
        self.assertEqual((rd.account_type, rd.rd_installment_amount, rd.rd_installment_day, rd.deposit_rate),
                         ('RD', D('5000.00'), 5, D('7.0000')))

    def test_a_recurring_deposit_is_saved_without_a_principal(self):
        """An RD has no single starting amount, so the form does not ask for one."""
        from expenses.models import Account
        start = TODAY - relativedelta(months=3)
        page = self.new_page(self.user)
        self.open(page, reverse('account-create') + '?type=RD')
        page.fill('#id_name', 'No principal')
        page.click('#nextBtn')
        page.wait_for_selector('#id_rd_installment_amount', state='visible')
        for field, value in (('rd_installment_amount', '5000'), ('rd_installment_day', '5'), ('deposit_rate', '7'),
                             ('deposit_start_date', start.isoformat()), ('deposit_maturity_date', (start + relativedelta(months=12)).isoformat())):
            page.fill(f'#id_{field}', value)
        page.locator('form button[type="submit"]:visible, form input[type="submit"]:visible').first.click()
        self.wait_until(lambda: Account.objects.filter(user=self.user, name='No principal').exists(), 'the RD was not saved', seconds=15)
        page = self.new_page(self.user)
        open_account(self, page, Account.objects.get(user=self.user, name='No principal'))

    def test_the_installment_day_must_be_between_1_and_28(self):
        from expenses.models import Account
        start = TODAY - relativedelta(months=3)
        for day, saved in (('0', False), ('29', False), ('31', False), ('1', True), ('28', True)):
            with self.subTest(day=day):
                name = f'Day {day}'
                page = self.new_page(self.user)
                self.fill(page, name=name, day=day, start=start, maturity=start + relativedelta(months=12))
                page.wait_for_timeout(1200)
                self.assertEqual(Account.objects.filter(user=self.user, name=name).exists(), saved)
                if not saved:
                    refused_by_browser = not page.evaluate("document.querySelector('#id_rd_installment_day').validity.valid")
                    self.assertTrue(refused_by_browser or 'between 1 and 28' in page.inner_text('body'))

    def test_an_installment_amount_is_required_and_cannot_be_zero_or_negative(self):
        from expenses.models import Account
        start = TODAY - relativedelta(months=3)
        for label, amount in (('blank', ''), ('zero', '0'), ('negative', '-500')):
            with self.subTest(label):
                page = self.new_page(self.user)
                self.fill(page, name=f'Amount {label}', amount=amount, start=start, maturity=start + relativedelta(months=12))
                page.wait_for_timeout(1200)
                self.assertFalse(Account.objects.filter(user=self.user, name=f'Amount {label}').exists())
                self.assertIn('nstallment amount', page.inner_text('body'))

    def test_the_maturity_date_must_be_after_the_start_date(self):
        from expenses.models import Account
        start = TODAY - relativedelta(months=3)
        page = self.new_page(self.user)
        self.fill(page, start=start, maturity=start - relativedelta(days=1))
        page.wait_for_timeout(1200)
        self.assertIn('Maturity date must be after the start date', page.inner_text('body'))
        self.assertFalse(Account.objects.filter(user=self.user, name='Edge RD').exists())


class ValueTests(Base):
    def test_a_schedule_with_nothing_recorded_is_worked_out_installment_by_installment(self):
        start = (TODAY - relativedelta(months=6)).replace(day=5)
        maturity = start + relativedelta(months=12)
        rd = self.rd(start=start, maturity=maturity, rate=7, compounding='QUARTERLY')
        paid = [d for d in monthly_dates(start, 5, 12) if d <= TODAY]
        page = self.new_page(self.user)
        open_account(self, page, rd)
        self.assertMoney(balance_on(page), rd_value([(d, D(5000)) for d in paid], 7, TODAY, 'QUARTERLY'))

    def test_the_maturity_amount_for_a_year_long_rd_at_each_compounding(self):
        start = (TODAY - relativedelta(months=2)).replace(day=5)
        maturity = start + relativedelta(months=12)
        schedule = [(d, D(5000)) for d in monthly_dates(start, 5, 12)]
        page = self.new_page(self.user)
        for compounding in ('SIMPLE', 'QUARTERLY', 'MONTHLY', 'ANNUAL'):
            with self.subTest(compounding):
                rd = self.rd(f'RD {compounding}', start=start, maturity=maturity, compounding=compounding)
                open_account(self, page, rd)
                self.assertMoney(labelled(page, 'Expected at maturity:'), rd_value(schedule, 7, maturity, compounding))

    def test_twelve_installments_not_thirteen_for_a_twelve_month_rd(self):
        """An installment on the maturity date itself would be one too many (the day the bank pays out)."""
        start = date(2024, 1, 5)
        rd = self.rd(start=start, maturity=date(2025, 1, 5), rate=0)
        page = self.new_page(self.user)
        open_account(self, page, rd)
        self.assertIn('Installments: 12 of 12 paid', page.inner_text('body').replace('\n', ' '))
        self.assertEqual(balance_on(page), D('60000.00'))

    def test_a_missed_month_is_left_out_of_what_was_paid_and_what_it_earned(self):
        start = (TODAY - relativedelta(months=5)).replace(day=1)
        maturity = start + relativedelta(months=12)
        rd = self.rd(start=start, maturity=maturity, day=1, rate=8, compounding='SIMPLE')
        paid_on = [d for i, d in enumerate(monthly_dates(start, 1, 6)) if i != 2 and d <= TODAY]      # the third month was skipped
        for d in paid_on:
            self.pay(rd, d)
        page = self.new_page(self.user)
        open_account(self, page, rd)
        self.assertMoney(balance_on(page), rd_value([(d, D(5000)) for d in paid_on], 8, TODAY, 'SIMPLE'))
        self.assertEqual(labelled(page, 'Principal:'), D(5000) * len(paid_on))

    def test_a_larger_or_smaller_transfer_than_the_schedule_is_what_counts(self):
        start = (TODAY - relativedelta(months=3)).replace(day=1)
        rd = self.rd(start=start, maturity=start + relativedelta(months=12), day=1, rate=6, compounding='SIMPLE')
        amounts = [5000, 7500, 2500]
        dates = [d for d in monthly_dates(start, 1, 3) if d <= TODAY]
        for d, a in zip(dates, amounts, strict=False):
            self.pay(rd, d, a)
        page = self.new_page(self.user)
        open_account(self, page, rd)
        self.assertMoney(balance_on(page), rd_value(list(zip(dates, map(D, amounts), strict=False)), 6, TODAY, 'SIMPLE'))
        self.assertEqual(labelled(page, 'Principal:'), D(sum(amounts[:len(dates)])))

    def test_a_deposit_with_a_zero_rate_is_just_what_was_paid_in(self):
        start = (TODAY - relativedelta(months=4)).replace(day=5)
        rd = self.rd(start=start, maturity=start + relativedelta(months=12), rate=0)
        paid = [d for d in monthly_dates(start, 5, 12) if d <= TODAY]
        page = self.new_page(self.user)
        open_account(self, page, rd)
        self.assertEqual(balance_on(page), D(5000) * len(paid))
        self.assertEqual(labelled(page, 'Expected at maturity:'), D(60000))


class CalendarEdgeCases(Base):
    """Past, finished deposits, so the answers do not depend on today's date."""

    def test_an_installment_day_of_28_is_paid_every_month_including_february(self):
        start, maturity = date(2023, 6, 28), date(2024, 6, 28)
        rd = self.rd(start=start, maturity=maturity, day=28, rate=6, compounding='SIMPLE')
        page = self.new_page(self.user)
        open_account(self, page, rd)
        schedule = [(d, D(5000)) for d in monthly_dates(start, 28, 12)]
        self.assertMoney(balance_on(page), rd_value(schedule, 6, maturity, 'SIMPLE'))

    def test_a_deposit_that_runs_across_a_leap_february_matches_the_hand_worked_figure(self):
        start, maturity = date(2023, 12, 1), date(2024, 12, 1)
        rd = self.rd(start=start, maturity=maturity, day=1, rate=7.5, compounding='MONTHLY')
        page = self.new_page(self.user)
        open_account(self, page, rd)
        schedule = [(d, D(5000)) for d in monthly_dates(start, 1, 12)]
        self.assertMoney(balance_on(page), rd_value(schedule, 7.5, maturity, 'MONTHLY'))

    def test_a_matured_deposit_stops_growing_and_says_so(self):
        start, maturity = date(2022, 1, 5), date(2023, 1, 5)
        rd = self.rd(start=start, maturity=maturity, rate=7, compounding='QUARTERLY')
        schedule = [(d, D(5000)) for d in monthly_dates(start, 5, 12)]
        page = self.new_page(self.user)
        open_account(self, page, rd)
        body = page.inner_text('body').replace('\n', ' ')
        self.assertMoney(balance_on(page), rd_value(schedule, 7, maturity, 'QUARTERLY'))
        self.assertEqual(labelled(page, 'At maturity:'), balance_on(page))
        self.assertIn('Matured on', body)
        self.assertIn('No installments are still to come.', body)
        self.assertEqual(page.locator('#rd-upcoming li').count(), 0)

    def test_an_rd_that_starts_in_the_future_holds_nothing_yet(self):
        start = TODAY + relativedelta(months=2)
        rd = self.rd(start=start, maturity=start + relativedelta(months=12))
        page = self.new_page(self.user)
        open_account(self, page, rd)
        self.assertEqual(balance_on(page), D('0.00'))
        self.assertIn('Installments: 0 of 12 paid', page.inner_text('body').replace('\n', ' '))
