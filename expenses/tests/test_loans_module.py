"""Behavioural tests for Loans: the maths (EMI, interest, schedule, savings), repayments and their
balance effects, loan lifecycle, forms, every view, the posting engine's rate handling, and plan gates."""

import uuid
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

from dateutil.relativedelta import relativedelta
from django.contrib.auth.models import User
from django.contrib.messages import get_messages
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from expenses.forms import LoanForm, LoanInterestRateForm, LoanRepaymentForm
from expenses.models import (
    Account,
    CapitalEvent,
    FinancialAuditLog,
    FXRate,
    Loan,
    LoanInterestRate,
    LoanRepayment,
    RecurringTransaction,
    UserProfile,
)
from expenses.services import LoanService
from expenses.views.mixins import process_user_recurring_transactions
from finance_tracker.plans import get_limit

D = Decimal


def today():
    return timezone.localdate()


def seed_fx():
    for (src, dst), rate in {('USD', 'INR'): '80', ('INR', 'USD'): '0.0125'}.items():
        FXRate.objects.update_or_create(from_currency=src, to_currency=dst, as_of_date=today(),
                                        defaults={'rate': D(rate), 'source': 'test'})
    cache.clear()


class LoanBase(TestCase):
    tier = 'PRO'

    def setUp(self):
        self.user = self.make_user('loan-user', self.tier)
        self.client.force_login(self.user)
        self.cash = Account.objects.create(user=self.user, name='Cash', account_type='CASH_WALLET',
                                           balance=D('100000.00'), currency='₹')
        cache.clear()

    def make_user(self, name, tier='PRO', currency='₹'):
        user = User.objects.create_user(username=name, password='pass')
        profile, _ = UserProfile.objects.get_or_create(user=user, defaults={'currency': currency})
        profile.tier = tier
        profile.currency = currency
        profile.save()
        user.refresh_from_db()
        return user

    def loan(self, principal='100000', rate='12', months=12, started=None, user=None, **kw):
        values = dict(user=user or self.user, name=kw.pop('name', 'Home'), loan_type='HOME',
                      initial_principal=D(str(principal)), duration_months=months,
                      start_date=started or today() - relativedelta(months=2), is_active=True)
        values.update(kw)
        loan = Loan.objects.create(**values)
        if rate is not None:
            LoanInterestRate.objects.create(loan=loan, interest_rate=D(str(rate)), effective_date=loan.start_date)
        return loan

    def pay(self, loan, principal, interest='0', when=None, account='default'):
        return LoanRepayment.objects.create(
            loan=loan, from_account=self.cash if account == 'default' else account, date=when or today(),
            amount=D(str(principal)) + D(str(interest)), principal_portion=D(str(principal)),
            interest_portion=D(str(interest)))

    def prepay(self, loan, amount, subtype='loan_prepayment', **kw):
        return CapitalEvent.objects.create(user=loan.user, date=today(), amount=D(str(amount)), subtype=subtype,
                                           currency='₹', linked_loan=loan, **kw)

    def bal(self, account):
        account.refresh_from_db()
        return account.balance

    def messages(self, response):
        return [str(m) for m in get_messages(response.wsgi_request)]


# ---------------------------------------------------------------------------
# Maths
# ---------------------------------------------------------------------------
class TestLoanMath(LoanBase):
    def test_emi_known_values(self):
        self.assertEqual(LoanService.calculate_emi(50000, 12, 12), 4442.44)
        self.assertEqual(LoanService.calculate_emi(120000, 14, 12), 10774.45)   # the guide's example
        self.assertEqual(LoanService.calculate_emi(1000000, 10.5, 60), 21493.9)

    def test_emi_times_months_pays_back_principal_plus_interest(self):
        emi = D(str(LoanService.calculate_emi(100000, 12, 12)))
        self.assertEqual(emi, D('8884.88'))
        self.assertGreater(emi * 12, D('100000'))
        self.assertLess(emi * 12 - D('100000'), D('7000'))

    def test_zero_rate_emi_is_rounded_to_paise(self):
        self.assertEqual(LoanService.calculate_emi(100000, 0, 3), 33333.33)
        self.assertEqual(LoanService.calculate_emi(12000, 0, 12), 1000.0)

    def test_emi_degenerate_inputs(self):
        for args in ((0, 10, 12), (-5, 10, 12), (1000, 10, 0), (1000, 10, -1), (None, 10, 12)):
            self.assertEqual(LoanService.calculate_emi(*args), 0.0, args)

    def test_repayment_by_type(self):
        self.assertEqual(LoanService.calculate_repayment(120000, 12, 12, 'EMI'), 10661.85)
        self.assertEqual(LoanService.calculate_repayment(120000, 12, 12, 'INTEREST_ONLY'), 1200.0)
        self.assertEqual(LoanService.calculate_repayment(120000, 12, 12, 'BULLET'), 1200.0)
        self.assertEqual(LoanService.calculate_repayment(120000, 12, 12, 'WHATEVER'), 10661.85)
        self.assertEqual(LoanService.calculate_repayment(0, 12, 12, 'INTEREST_ONLY'), 0.0)

    def test_period_interest_month_based_matches_the_emi_rate(self):
        self.assertEqual(LoanService.period_interest(100000, 12, 'MONTHLY'), D('1000.00'))
        self.assertEqual(LoanService.period_interest(100000, 12, 'QUARTERLY'), D('3000.00'))
        self.assertEqual(LoanService.period_interest(100000, 12, 'SEMIANNUALLY'), D('6000.00'))
        self.assertEqual(LoanService.period_interest(100000, 12, 'YEARLY'), D('12000.00'))
        self.assertEqual(LoanService.period_interest(0, 12), D('0.00'))
        self.assertEqual(LoanService.period_interest(100000, 0), D('0.00'))

    def test_period_interest_day_based(self):
        self.assertEqual(LoanService.period_interest(365000, 10, 'DAILY'), D('100.00'))
        self.assertEqual(LoanService.period_interest(365000, 10, 'WEEKLY'), D('700.00'))
        self.assertEqual(LoanService.period_interest(365000, 10, 'BIWEEKLY'), D('1400.00'))

    def test_period_interest_rounds_half_up(self):
        self.assertEqual(LoanService.period_interest(D('1000.05'), 12, 'MONTHLY'), D('10.00'))   # 10.0005
        self.assertEqual(LoanService.period_interest(D('100.50'), 12, 'MONTHLY'), D('1.01'))     # 1.005

    def test_rate_on_uses_the_rate_in_force_on_the_date(self):
        rates = [(date(2026, 1, 1), D('10')), (date(2026, 6, 1), D('12')), (date(2027, 1, 1), D('9'))]
        for on, expected in ((date(2026, 1, 1), '10'), (date(2026, 5, 31), '10'), (date(2026, 6, 1), '12'),
                             (date(2026, 12, 31), '12'), (date(2027, 1, 1), '9'), (date(2030, 1, 1), '9')):
            self.assertEqual(LoanService.rate_on(rates, on), D(expected), on)

    def test_rate_on_ignores_future_rates_and_falls_back_to_the_earliest(self):
        self.assertEqual(LoanService.rate_on([(date(2030, 1, 1), D('7'))], date(2026, 1, 1)), D('7'))
        self.assertEqual(LoanService.rate_on([(date(2030, 1, 1), D('7')), (date(2031, 1, 1), D('8'))], date(2026, 1, 1)), D('7'))
        self.assertEqual(LoanService.rate_on([], date(2026, 1, 1)), D('0.00'))

    def test_rate_on_accepts_model_rows_in_any_order(self):
        loan = self.loan(rate=None)
        LoanInterestRate.objects.create(loan=loan, interest_rate=D('9'), effective_date=date(2026, 6, 1))
        LoanInterestRate.objects.create(loan=loan, interest_rate=D('11'), effective_date=date(2026, 1, 1))
        self.assertEqual(LoanService.rate_on(loan.interest_rates.all(), date(2026, 3, 1)), D('11'))
        self.assertEqual(LoanService.rate_on(loan.interest_rates.all(), date(2026, 7, 1)), D('9'))

    def test_current_rate_does_not_use_a_scheduled_future_rate(self):
        loan = self.loan(rate='12')
        LoanInterestRate.objects.create(loan=loan, interest_rate=D('24'), effective_date=today() + timedelta(days=30))
        self.assertEqual(LoanService.current_rate(loan), D('12'))
        self.assertEqual(LoanService.current_rate(loan, today() + timedelta(days=31)), D('24'))

    def test_base_rate(self):
        seed_fx()
        self.assertEqual(LoanService.base_rate(self.user, '₹'), D('1'))
        self.assertEqual(LoanService.base_rate(self.user, '$'), D('80'))
        with patch('expenses.services.get_exchange_rate', side_effect=RuntimeError('down')):
            self.assertEqual(LoanService.base_rate(self.user, '€'), D('1'))


class TestLoanSummary(LoanBase):
    def test_remaining_principal_combines_everything(self):
        loan = self.loan('100000', opening_paid_principal=D('10000'))
        self.pay(loan, 3000, 1000)
        self.prepay(loan, 20000, 'loan_down_payment')
        self.prepay(loan, 5000, 'loan_prepayment')
        s = LoanService.get_loan_summary(Loan.objects.get(pk=loan.pk))
        self.assertEqual((s['principal_paid'], s['capital_prepaid'], s['opening_paid_principal'], s['interest_paid'],
                          s['total_paid'], s['remaining_principal']), (3000.0, 25000.0, 10000.0, 1000.0, 4000.0, 62000.0))
        self.assertEqual(Loan.objects.get(pk=loan.pk).remaining_principal, D('62000.00'))

    def test_only_loan_related_subtypes_reduce_principal(self):
        loan = self.loan('100000')
        self.prepay(loan, 9000, 'large_purchase')
        self.prepay(loan, 9000, 'other')
        self.assertEqual(Loan.objects.get(pk=loan.pk).remaining_principal, D('100000.00'))

    def test_remaining_never_goes_negative(self):
        loan = self.loan('1000')
        self.prepay(loan, 5000)
        self.assertEqual(Loan.objects.get(pk=loan.pk).remaining_principal, D('0.00'))

    def test_annotated_totals_match_the_property(self):
        from expenses.models import annotate_loan_principal_totals
        loan = self.loan('100000', opening_paid_principal=D('500'))
        self.pay(loan, 4000, 100)
        self.prepay(loan, 1000)
        annotated = annotate_loan_principal_totals(Loan.objects.filter(pk=loan.pk)).get()
        self.assertEqual(annotated.remaining_principal, Loan.objects.get(pk=loan.pk).remaining_principal)

    def test_total_liabilities_are_converted_to_the_base_currency(self):
        seed_fx()
        self.loan('100000', name='INR loan')
        self.loan('1000', name='USD loan', currency='$')
        self.loan('5000', name='closed', is_active=False)
        self.assertEqual(LoanService.get_total_liabilities(self.user), 180000.0)    # 100000 + 1000 * 80

    def test_total_liabilities_ignore_other_users(self):
        other = self.make_user('other-liab')
        self.loan('999999', user=other)
        self.loan('1000')
        self.assertEqual(LoanService.get_total_liabilities(self.user), 1000.0)

    def test_summary_is_cached_on_the_object(self):
        loan = self.loan()
        first = LoanService.get_loan_summary(loan)
        self.assertIs(LoanService.get_loan_summary(loan), first)


class TestAmortizationSchedule(LoanBase):
    def schedule(self, loan):
        return LoanService.generate_amortization_schedule(Loan.objects.get(pk=loan.pk))

    def test_covers_the_remaining_term_and_clears_the_balance(self):
        loan = self.loan('100000', '12', 12, started=today() - relativedelta(months=2))
        rows = self.schedule(loan)
        self.assertEqual(len(rows), 10)                                  # 12 months, 2 gone
        self.assertEqual(rows[-1]['balance'], 0.0)
        self.assertAlmostEqual(sum(r['principal'] for r in rows), 100000.0, delta=0.1)
        self.assertEqual(rows[0]['date'], today())

    def test_first_row_interest_and_split(self):
        rows = self.schedule(self.loan('100000', '12', 12, started=today()))
        emi = LoanService.calculate_emi(100000, 12, 12)
        self.assertEqual((rows[0]['emi'], rows[0]['interest'], rows[0]['principal']), (emi, 1000.0, round(emi - 1000, 2)))
        self.assertEqual(len(rows), 12)

    def test_emi_is_constant_except_the_last_payment(self):
        rows = self.schedule(self.loan('100000', '12', 12, started=today()))
        self.assertEqual({r['emi'] for r in rows[:-1]}, {rows[0]['emi']})
        self.assertAlmostEqual(rows[-1]['emi'], rows[0]['emi'], delta=0.1)

    def test_interest_falls_and_principal_rises(self):
        rows = self.schedule(self.loan('100000', '12', 12, started=today()))
        interests = [r['interest'] for r in rows]
        self.assertEqual(interests, sorted(interests, reverse=True))
        principals = [r['principal'] for r in rows]
        self.assertEqual(principals, sorted(principals))

    def test_a_loan_that_has_not_started_yet_has_its_full_term(self):
        """Regression: a loan starting in 3 months showed 15 rows for a 12-month term."""
        loan = self.loan('100000', '12', 12, started=today() + relativedelta(months=3))
        rows = self.schedule(loan)
        self.assertEqual(len(rows), 12)
        self.assertEqual(rows[0]['date'], loan.start_date)

    def test_part_paid_loan_schedules_only_the_remaining_balance(self):
        loan = self.loan('100000', '12', 12, started=today() - relativedelta(months=2))
        self.pay(loan, 30000, 0)
        self.prepay(loan, 20000)
        rows = self.schedule(loan)
        self.assertAlmostEqual(sum(r['principal'] for r in rows), 50000.0, delta=0.1)

    def test_zero_rate(self):
        rows = self.schedule(self.loan('12000', '0', 12, started=today()))
        self.assertEqual({r['interest'] for r in rows}, {0.0})
        self.assertEqual({r['principal'] for r in rows}, {1000.0})

    def test_no_rate_row_means_zero_interest(self):
        rows = self.schedule(self.loan('12000', None, 12, started=today()))
        self.assertEqual({r['interest'] for r in rows}, {0.0})

    def test_a_loan_past_its_term_but_still_owing_shows_one_final_payment(self):
        loan = self.loan('100000', '12', 6, started=today() - relativedelta(months=10))
        rows = self.schedule(loan)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['balance'], 0.0)
        self.assertAlmostEqual(rows[0]['principal'], 100000.0, delta=0.1)

    def test_paid_off_loan_has_no_schedule(self):
        loan = self.loan('1000', '12', 12)
        self.pay(loan, 1000)
        self.assertEqual(self.schedule(loan), [])

    def test_uses_the_rate_in_force_today_not_a_future_one(self):
        loan = self.loan('100000', '12', 12, started=today())
        LoanInterestRate.objects.create(loan=loan, interest_rate=D('24'), effective_date=today() + relativedelta(months=6))
        self.assertEqual(self.schedule(loan)[0]['interest'], 1000.0)

    def test_picks_up_a_rate_change_that_has_taken_effect(self):
        loan = self.loan('100000', '12', 12, started=today() - relativedelta(months=3))
        LoanInterestRate.objects.create(loan=loan, interest_rate=D('24'), effective_date=today() - relativedelta(months=1))
        self.assertEqual(self.schedule(loan)[0]['interest'], 2000.0)

    def test_interest_only_loan_pays_interest_then_the_principal_at_the_end(self):
        """Regression: bullet and interest-only loans were shown as amortising."""
        for repayment_type in ('INTEREST_ONLY', 'BULLET'):
            loan = self.loan('120000', '12', 6, started=today(), repayment_type=repayment_type, name=repayment_type)
            rows = self.schedule(loan)
            self.assertEqual(len(rows), 6, repayment_type)
            self.assertEqual({r['principal'] for r in rows[:-1]}, {0.0}, repayment_type)
            self.assertEqual({r['balance'] for r in rows[:-1]}, {120000.0}, repayment_type)
            self.assertEqual({r['interest'] for r in rows}, {1200.0}, repayment_type)
            self.assertEqual((rows[-1]['principal'], rows[-1]['emi'], rows[-1]['balance']), (120000.0, 121200.0, 0.0), repayment_type)
            self.assertEqual(rows[0]['emi'], 1200.0, repayment_type)

    def test_month_labels_roll_over_the_year(self):
        loan = self.loan('1200', '0', 12, started=date(today().year, 11, 15) if today() > date(today().year, 11, 15) else today())
        rows = self.schedule(loan)
        self.assertEqual(len({r['month'] for r in rows}), len(rows))


class TestExtraEmiSavings(LoanBase):
    def test_one_extra_emi_a_year_saves_interest_and_time(self):
        loan = self.loan('1000000', '10', 120, started=today())
        result = LoanService.calculate_extra_emi_savings(loan)
        self.assertEqual(result['emi'], LoanService.calculate_emi(1000000, 10, 120))
        self.assertEqual(result['normal_months'], 120)
        self.assertLess(result['extra_months'], 120)
        self.assertEqual(result['months_saved'], result['normal_months'] - result['extra_months'])
        self.assertGreater(result['interest_saved'], 0)
        self.assertEqual(result['years_saved'], round(result['months_saved'] / 12, 1))
        self.assertAlmostEqual(result['normal_interest'], float(D(str(result['emi'])) * 120 - D('1000000')), delta=5)

    def test_nothing_to_report_when_paid_off_past_term_or_not_an_emi_loan(self):
        paid = self.loan('1000', '12', 12)
        self.pay(paid, 1000)
        self.assertIsNone(LoanService.calculate_extra_emi_savings(paid))
        self.assertIsNone(LoanService.calculate_extra_emi_savings(self.loan('1000', '12', 3, started=today() - relativedelta(months=10), name='old')))
        self.assertIsNone(LoanService.calculate_extra_emi_savings(self.loan('1000', '12', 12, started=today(), repayment_type='BULLET', name='b')))

    def test_a_loan_that_has_not_started_uses_its_full_term(self):
        loan = self.loan('100000', '12', 12, started=today() + relativedelta(months=3))
        self.assertEqual(LoanService.calculate_extra_emi_savings(loan)['normal_months'], 12)


# ---------------------------------------------------------------------------
# Repayment model and loan lifecycle
# ---------------------------------------------------------------------------
class TestRepaymentModel(LoanBase):
    def build(self, loan, **kw):
        values = dict(loan=loan, from_account=self.cash, date=today(), amount=D('5000'),
                      principal_portion=D('4000'), interest_portion=D('1000'))
        values.update(kw)
        return LoanRepayment(**values)

    def test_a_valid_repayment_debits_the_account(self):
        loan = self.loan()
        self.build(loan).save()
        self.assertEqual(self.bal(self.cash), D('95000.00'))

    def test_rules(self):
        loan = self.loan()
        cases = [dict(amount=D('0'), principal_portion=D('0'), interest_portion=D('0')),
                 dict(amount=D('-5'), principal_portion=D('-5'), interest_portion=D('0')),
                 dict(principal_portion=D('-1'), interest_portion=D('5001')),
                 dict(interest_portion=D('-1'), principal_portion=D('5001')),
                 dict(principal_portion=D('3000')),                                 # 3000 + 1000 != 5000
                 dict(amount=D('150000'), principal_portion=D('149000'))]            # more than the principal
        for overrides in cases:
            with self.subTest(overrides=overrides), self.assertRaises(ValidationError):
                self.build(loan, **overrides).save()
        self.assertEqual(LoanRepayment.objects.count(), 0)
        self.assertEqual(self.bal(self.cash), D('100000.00'))

    def test_principal_cannot_exceed_what_is_left_after_prepayments_and_opening_paid(self):
        loan = self.loan('100000', opening_paid_principal=D('90000'))
        self.prepay(loan, 5000)
        with self.assertRaises(ValidationError):
            self.build(loan, amount=D('6000'), principal_portion=D('6000'), interest_portion=D('0')).save()   # only 5,000 left
        self.build(loan, amount=D('5000'), principal_portion=D('5000'), interest_portion=D('0')).save()

    def test_the_account_must_belong_to_the_loan_owner(self):
        other = self.make_user('other-repay')
        theirs = Account.objects.create(user=other, name='T', account_type='CASH_WALLET', balance=10, currency='₹')
        with self.assertRaises(ValidationError):
            self.build(self.loan(), from_account=theirs).save()

    def test_no_account_means_no_balance_change(self):
        self.build(self.loan(), from_account=None).save()
        self.assertEqual(self.bal(self.cash), D('100000.00'))

    def test_update_applies_the_difference_and_account_moves(self):
        bank = Account.objects.create(user=self.user, name='Bank', account_type='SAVINGS_ACCOUNT', balance=D('50000'), currency='₹')
        loan = self.loan()
        r = self.build(loan)
        r.save()
        r.amount, r.principal_portion = D('7000'), D('6000')
        r.save()
        self.assertEqual(self.bal(self.cash), D('93000.00'))
        r.from_account = bank
        r.save()
        self.assertEqual((self.bal(self.cash), self.bal(bank)), (D('100000.00'), D('43000.00')))

    def test_delete_restores_the_balance(self):
        r = self.build(self.loan())
        r.save()
        r.delete()
        self.assertEqual(self.bal(self.cash), D('100000.00'))

    def test_foreign_currency_loan(self):
        seed_fx()
        loan = self.loan('1000', currency='$', name='usd')
        r = LoanRepayment(loan=loan, from_account=self.cash, date=today(), amount=D('50'),
                          principal_portion=D('40'), interest_portion=D('10'))
        r.save()
        self.assertEqual((r.exchange_rate, r.base_amount), (D('80'), D('4000.00')))
        self.assertEqual(self.bal(self.cash), D('96000.00'))          # $50 -> ₹4,000
        r.delete()
        self.assertEqual(self.bal(self.cash), D('100000.00'))

    def test_audit_log_on_create_and_update(self):
        r = self.build(self.loan())
        r.save()
        r.amount, r.principal_portion = D('6000'), D('5000')
        r.save()
        logs = FinancialAuditLog.objects.filter(model_name='LoanRepayment', object_id=r.id).order_by('timestamp', 'id')
        self.assertEqual([x.action for x in logs], ['CREATE', 'UPDATE'])

    def test_deleting_a_loan_removes_its_repayments_and_gives_the_money_back(self):
        loan = self.loan()
        self.pay(loan, 4000, 1000)
        self.pay(loan, 4000, 1000, when=today() - timedelta(days=30))
        self.prepay(loan, 100)
        loan.delete()
        self.assertEqual(LoanRepayment.objects.count(), 0)
        self.assertEqual(self.bal(self.cash), D('100000.00'))
        self.assertIsNone(CapitalEvent.objects.get().linked_loan)      # the event stays, unlinked

    def test_deleting_a_loan_removes_its_recurring_schedule(self):
        loan = self.loan()
        RecurringTransaction.objects.create(user=self.user, transaction_type='LOAN', amount=D('5000'), currency='₹',
                                            account=self.cash, loan=loan, frequency='MONTHLY', start_date=today(), description='emi')
        loan.delete()
        self.assertEqual(RecurringTransaction.objects.count(), 0)


class TestLoanLifecycle(LoanBase):
    def active(self, loan):
        loan.refresh_from_db()
        return loan.is_active

    def test_paying_off_the_principal_closes_the_loan_and_deleting_the_payment_reopens_it(self):
        loan = self.loan('5000')
        r = self.pay(loan, 5000)
        self.assertFalse(self.active(loan))
        r.delete()
        self.assertTrue(self.active(loan))

    def test_a_prepayment_can_close_and_reopen_it(self):
        loan = self.loan('5000')
        e = self.prepay(loan, 5000)
        self.assertFalse(self.active(loan))
        e.delete()
        self.assertTrue(self.active(loan))

    def test_part_payment_keeps_it_open(self):
        loan = self.loan('5000')
        self.pay(loan, 4999)
        self.assertTrue(self.active(loan))

    def test_opening_paid_principal_counts_towards_closing(self):
        loan = self.loan('5000', opening_paid_principal=D('4000'))
        self.pay(loan, 1000)
        self.assertFalse(self.active(loan))

    def test_closed_loans_do_not_count_as_liabilities(self):
        loan = self.loan('5000')
        self.pay(loan, 5000)
        self.assertEqual(LoanService.get_total_liabilities(self.user), 0.0)


class TestPlanQuota(LoanBase):
    tier = 'PLUS'

    def test_quotas_match_the_guide(self):
        self.assertEqual((get_limit('FREE', 'loans'), get_limit('PLUS', 'loans'), get_limit('PRO', 'loans')), (0, 1, -1))

    def test_plus_has_room_for_one_active_loan(self):
        profile = self.user.profile
        self.assertTrue(profile.can_add_loan())
        self.loan()
        self.assertFalse(profile.can_add_loan())

    def test_closing_a_loan_frees_its_slot(self):
        """Regression: closed loans kept using up the quota forever."""
        loan = self.loan('1000')
        self.pay(loan, 1000)
        self.assertFalse(Loan.objects.get(pk=loan.pk).is_active)
        self.assertTrue(self.user.profile.can_add_loan())

    def test_pro_is_unlimited(self):
        profile = self.user.profile
        profile.tier = 'PRO'
        profile.save()
        for i in range(4):
            self.loan(name=f'L{i}')
        self.assertTrue(profile.can_add_loan())


# ---------------------------------------------------------------------------
# Forms
# ---------------------------------------------------------------------------
class TestLoanForms(LoanBase):
    def data(self, **overrides):
        data = {'name': 'Car loan', 'loan_type': 'CAR', 'initial_principal': '500000', 'duration_months': '60',
                'start_date': today().isoformat(), 'currency': '₹', 'interest_rate': '9.5'}
        data.update(overrides)
        return data

    def form(self, **overrides):
        return LoanForm(self.data(**overrides), user=self.user)

    def test_valid_and_required(self):
        self.assertTrue(self.form().is_valid())
        form = LoanForm({}, user=self.user)
        self.assertFalse(form.is_valid())
        for field in ('name', 'initial_principal', 'duration_months', 'start_date', 'interest_rate'):
            self.assertIn(field, form.errors)

    def test_principal_must_be_positive(self):
        for bad in ('0', '-1', 'abc', '1.234'):
            self.assertIn('initial_principal', self.form(initial_principal=bad).errors, bad)

    def test_duration_bounds(self):
        for bad in ('0', '-6', '601', 'x'):
            self.assertIn('duration_months', self.form(duration_months=bad).errors, bad)
        for ok in ('1', '600'):
            self.assertTrue(self.form(duration_months=ok).is_valid(), ok)

    def test_rate_bounds(self):
        for bad in ('-0.01', '100.01', '250', 'x'):
            self.assertIn('interest_rate', self.form(interest_rate=bad).errors, bad)
        for ok in ('0', '100', '8.75'):
            self.assertTrue(self.form(interest_rate=ok).is_valid(), ok)

    def test_loan_type_and_currency_must_be_known(self):
        self.assertIn('loan_type', self.form(loan_type='GOLD').errors)
        self.assertIn('currency', self.form(currency='XXX').errors)
        for code, _label in Loan.LOAN_TYPES:
            self.assertTrue(self.form(loan_type=code).is_valid(), code)

    def test_editing_shows_the_latest_rate(self):
        loan = self.loan(rate='9')
        LoanInterestRate.objects.create(loan=loan, interest_rate=D('11'), effective_date=today())
        self.assertEqual(LoanForm(instance=loan, user=self.user).fields['interest_rate'].initial, D('11'))

    def test_rate_update_form(self):
        valid = LoanInterestRateForm({'interest_rate': '9.5', 'effective_date': today().isoformat()})
        self.assertTrue(valid.is_valid())
        for bad in ('-1', '101', 'x', ''):
            self.assertFalse(LoanInterestRateForm({'interest_rate': bad, 'effective_date': today().isoformat()}).is_valid(), bad)
        self.assertFalse(LoanInterestRateForm({'interest_rate': '9', 'effective_date': ''}).is_valid())


class TestRepaymentForm(LoanBase):
    def form(self, loan, **data):
        payload = {'from_account': str(self.cash.id), 'date': today().isoformat()}
        payload.update(data)
        return LoanRepaymentForm(payload, user=self.user, loan=loan)

    def valid(self, loan, **data):
        form = self.form(loan, **data)
        self.assertTrue(form.is_valid(), dict(form.errors))
        return form.cleaned_data

    def test_initial_suggestion_is_the_emi_split(self):
        loan = self.loan('100000', '12', 12, started=today())
        form = LoanRepaymentForm(user=self.user, loan=loan)
        self.assertEqual(form.fields['amount'].initial, LoanService.calculate_emi(100000, 12, 12))
        self.assertEqual(form.fields['interest_portion'].initial, 1000.0)
        self.assertEqual(form.fields['principal_portion'].initial, round(LoanService.calculate_emi(100000, 12, 12) - 1000, 2))

    def test_amount_only_is_split_automatically(self):
        loan = self.loan('100000', '12')
        cleaned = self.valid(loan, amount='5000')
        self.assertEqual((cleaned['amount'], cleaned['interest_portion'], cleaned['principal_portion']),
                         (D('5000.00'), D('1000.00'), D('4000.00')))

    def test_an_amount_below_the_interest_is_all_interest(self):
        cleaned = self.valid(self.loan('100000', '12'), amount='400')
        self.assertEqual((cleaned['interest_portion'], cleaned['principal_portion']), (D('400.00'), D('0.00')))

    def test_explicit_split_must_add_up(self):
        loan = self.loan()
        self.valid(loan, amount='5000', principal_portion='4000', interest_portion='1000')
        form = self.form(loan, amount='5000', principal_portion='3000', interest_portion='1000')
        self.assertFalse(form.is_valid())
        self.assertIn('amount', form.errors)

    def test_one_portion_fills_in_the_other(self):
        loan = self.loan()
        cleaned = self.valid(loan, amount='5000', principal_portion='4500')
        self.assertEqual(cleaned['interest_portion'], D('500.00'))
        cleaned = self.valid(loan, amount='5000', interest_portion='700')
        self.assertEqual(cleaned['principal_portion'], D('4300.00'))

    def test_negative_values_are_rejected(self):
        loan = self.loan()
        for data in ({'amount': '0'}, {'amount': '-5'}, {'amount': '5000', 'principal_portion': '-1', 'interest_portion': '5001'},
                     {'amount': '5000', 'principal_portion': '5001', 'interest_portion': '-1'}):
            self.assertFalse(self.form(loan, **data).is_valid(), data)

    def test_paying_off_in_full_caps_the_principal_to_what_is_left(self):
        loan = self.loan('1000', '12')
        cleaned = self.valid(loan, amount='1500', principal_portion='1400', interest_portion='100')
        self.assertEqual((cleaned['principal_portion'], cleaned['interest_portion'], cleaned['amount']),
                         (D('1000.00'), D('100.00'), D('1100.00')))

    def test_the_cap_includes_opening_paid_principal(self):
        loan = self.loan('100000', '0', opening_paid_principal=D('90000'))
        cleaned = self.valid(loan, amount='15000', principal_portion='15000', interest_portion='0')
        self.assertEqual(cleaned['principal_portion'], D('10000.00'))

    def test_future_dates_are_rejected(self):
        loan = self.loan()
        self.assertFalse(self.form(loan, amount='5000', date=(today() + timedelta(days=20)).isoformat()).is_valid())
        self.assertTrue(self.form(loan, amount='5000', date=(today() + timedelta(days=1)).isoformat()).is_valid())

    def test_several_date_formats_are_understood(self):
        loan = self.loan()
        for text in ('2026-03-05', '05/03/2026', '05-03-2026'):
            self.assertTrue(self.form(loan, amount='5000', date=text).is_valid(), text)

    def test_recurring_needs_a_frequency(self):
        loan = self.loan()
        self.assertFalse(self.form(loan, amount='5000', add_to_recurring='on', recurring_frequency='').is_valid())
        self.assertTrue(self.form(loan, amount='5000', add_to_recurring='on', recurring_frequency='MONTHLY').is_valid())

    def test_account_must_be_own_and_active(self):
        other = self.make_user('other-repay-form')
        theirs = Account.objects.create(user=other, name='T', account_type='CASH_WALLET', balance=10, currency='₹')
        self.assertFalse(self.form(self.loan(), amount='5000', from_account=str(theirs.id)).is_valid())


# ---------------------------------------------------------------------------
# Views
# ---------------------------------------------------------------------------
class TestLoanAccessAndList(LoanBase):
    url = reverse('loan-list')

    def test_login_required(self):
        self.client.logout()
        for name, kwargs in (('loan-list', {}), ('loan-create', {})):
            self.assertEqual(self.client.get(reverse(name, kwargs=kwargs)).status_code, 302)

    def test_free_plan_is_sent_to_pricing_from_every_loan_page(self):
        free = self.make_user('free-loans', tier='FREE')
        self.client.force_login(free)
        loan = self.loan(user=free)
        for name, kwargs in (('loan-list', {}), ('loan-create', {}), ('loan-detail', {'pk': loan.pk}),
                             ('loan-edit', {'pk': loan.pk}), ('loan-tab-schedule', {'pk': loan.pk}),
                             ('loan-tab-history', {'pk': loan.pk})):
            with self.subTest(page=name):
                self.assertRedirects(self.client.get(reverse(name, kwargs=kwargs)), reverse('pricing'), fetch_redirect_response=False)
        self.assertRedirects(self.client.post(reverse('loan-delete', kwargs={'pk': loan.pk})), reverse('pricing'), fetch_redirect_response=False)
        self.assertTrue(Loan.objects.filter(pk=loan.pk).exists())

    def ctx(self, **params):
        response = self.client.get(self.url, params)
        self.assertEqual(response.status_code, 200)
        return response.context

    def test_status_filter_and_counts(self):
        active = self.loan(name='active')
        closed = self.loan('100', name='closed')
        self.pay(closed, 100)
        names = lambda **p: [s['loan'].name for s in self.ctx(**p)['loan_summaries']]
        self.assertEqual(names(), ['active'])
        self.assertEqual(names(status='inactive'), ['closed'])
        self.assertEqual(set(names(status='all')), {'active', 'closed'})
        self.assertEqual(names(status='garbage'), ['active'])
        ctx = self.ctx()
        self.assertEqual((ctx['active_count'], ctx['inactive_count'], ctx['all_count']), (1, 1, 2))

    def test_only_own_loans_and_newest_first(self):
        old = self.loan(name='old', started=today() - relativedelta(months=5))
        new = self.loan(name='new', started=today() - relativedelta(months=1))
        other = self.make_user('other-loan-list')
        self.loan(name='secret', user=other)
        self.assertEqual([s['loan'].name for s in self.ctx()['loan_summaries']], ['new', 'old'])
        self.assertNotContains(self.client.get(self.url), 'secret')

    def test_card_numbers_and_progress(self):
        loan = self.loan('100000', opening_paid_principal=D('10000'))
        self.pay(loan, 20000, 3000)
        self.prepay(loan, 10000)
        s = self.ctx()['loan_summaries'][0]
        self.assertEqual((s['principal_paid'], s['capital_prepaid'], s['opening_paid'], s['interest_paid'],
                          s['total_paid'], s['remaining_principal']), (20000.0, 10000.0, 10000.0, 3000.0, 23000.0, 60000.0))
        self.assertEqual(s['progress'], 40.0)

    def test_portfolio_totals(self):
        a = self.loan('100000', name='a')
        self.pay(a, 20000, 2000)
        b = self.loan('50000', name='b', opening_paid_principal=D('10000'))
        self.prepay(b, 5000)
        ctx = self.ctx()
        totals = ctx['portfolio_totals']
        self.assertEqual(totals['tot_principal_paid'], 20000 + 5000 + 10000)
        self.assertEqual(totals['tot_interest_paid'], 2000)
        self.assertEqual(totals['tot_remaining_debt'], 80000 + 35000)
        self.assertEqual(ctx['total_debt'], 115000.0)
        self.assertEqual(totals['paid_pct'], round(35000 / 150000 * 100, 1))
        self.assertEqual(round(totals['paid_pct'] + totals['remaining_pct'], 1), 100.0)
        self.assertEqual(ctx['portfolio_breakdown_chart']['values'], [35000, 2000, 115000])
        chart = ctx['loan_comparison_chart']
        self.assertEqual(sorted(chart['principal_paid']), [15000, 20000])

    def test_portfolio_converts_foreign_loans_to_the_base_currency(self):
        seed_fx()
        self.loan('100000', name='inr')
        self.loan('1000', name='usd', currency='$')
        ctx = self.ctx()
        self.assertEqual(ctx['total_debt'], 180000.0)
        self.assertEqual(ctx['portfolio_totals']['tot_remaining_debt'], 180000.0)
        cards = {s['loan'].name: s['remaining_principal'] for s in ctx['loan_summaries']}
        self.assertEqual(cards['usd'], 1000.0)               # each card stays in its own currency

    def test_empty_portfolio(self):
        ctx = self.ctx()
        self.assertEqual((ctx['total_debt'], ctx['portfolio_totals']['paid_pct'], ctx['portfolio_totals']['remaining_pct']), (0.0, 0.0, 0.0))

    def test_htmx_partial(self):
        partial = [t.name for t in self.client.get(self.url, HTTP_HX_REQUEST='true').templates]
        self.assertIn('expenses/partials/_loan_list_partial.html', partial)
        self.assertNotIn('expenses/loan_list.html', partial)


class TestLoanCreateEditDelete(LoanBase):
    def payload(self, **overrides):
        data = {'name': 'Car loan', 'loan_type': 'CAR', 'initial_principal': '500000', 'duration_months': '60',
                'start_date': today().isoformat(), 'currency': '₹', 'interest_rate': '9.5'}
        data.update(overrides)
        return data

    def test_create_makes_the_loan_and_its_first_rate(self):
        response = self.client.post(reverse('loan-create'), self.payload())
        self.assertRedirects(response, reverse('loan-list'))
        loan = Loan.objects.get()
        self.assertEqual((loan.user, loan.name, loan.loan_type, loan.initial_principal, loan.duration_months,
                          loan.currency, loan.repayment_type, loan.is_active),
                         (self.user, 'Car loan', 'CAR', D('500000.00'), 60, '₹', 'EMI', True))
        rate = loan.interest_rates.get()
        self.assertEqual((rate.interest_rate, rate.effective_date), (D('9.50'), loan.start_date))
        self.assertIn('Loan created successfully!', self.messages(response))

    def test_create_rejects_bad_input(self):
        for override in ({'initial_principal': '0'}, {'initial_principal': '-5'}, {'duration_months': '0'},
                         {'duration_months': '9999'}, {'interest_rate': '-1'}, {'interest_rate': '150'}, {'name': ''}):
            with self.subTest(override=override):
                self.assertEqual(self.client.post(reverse('loan-create'), self.payload(**override)).status_code, 200)
        self.assertEqual(Loan.objects.count(), 0)

    def test_create_is_blocked_at_the_plan_quota(self):
        plus = self.make_user('plus-loans', tier='PLUS')
        self.client.force_login(plus)
        self.loan(user=plus)
        response = self.client.post(reverse('loan-create'), self.payload())
        self.assertRedirects(response, reverse('pricing'), fetch_redirect_response=False)
        self.assertEqual(Loan.objects.filter(user=plus).count(), 1)

    def test_edit_updates_fields_and_the_only_rate(self):
        loan = self.loan('100000', '12', started=today() - relativedelta(months=3))
        response = self.client.post(reverse('loan-edit', kwargs={'pk': loan.pk}), self.payload(
            name='Renamed', initial_principal='150000', interest_rate='10', start_date=(today() - relativedelta(months=6)).isoformat()))
        self.assertRedirects(response, reverse('loan-list'))
        loan.refresh_from_db()
        self.assertEqual((loan.name, loan.initial_principal), ('Renamed', D('150000.00')))
        rate = loan.interest_rates.get()
        self.assertEqual((rate.interest_rate, rate.effective_date), (D('10.00'), loan.start_date))
        self.assertIn('Loan updated successfully!', self.messages(response))

    def test_edit_leaves_a_rate_history_alone(self):
        loan = self.loan('100000', '12')
        later = LoanInterestRate.objects.create(loan=loan, interest_rate=D('15'), effective_date=today())
        self.client.post(reverse('loan-edit', kwargs={'pk': loan.pk}), self.payload(interest_rate='9'))
        later.refresh_from_db()
        self.assertEqual(later.interest_rate, D('15.00'))
        self.assertEqual(loan.interest_rates.count(), 2)

    def test_edit_reopens_a_closed_loan_when_the_principal_goes_up(self):
        """Regression: editing never re-checked whether the loan was still open."""
        loan = self.loan('1000')
        self.pay(loan, 1000)
        self.assertFalse(Loan.objects.get(pk=loan.pk).is_active)
        self.client.post(reverse('loan-edit', kwargs={'pk': loan.pk}), self.payload(initial_principal='5000', start_date=loan.start_date.isoformat()))
        self.assertTrue(Loan.objects.get(pk=loan.pk).is_active)

    def test_edit_closes_a_loan_when_the_principal_drops_to_what_is_paid(self):
        loan = self.loan('5000')
        self.pay(loan, 1000)
        self.client.post(reverse('loan-edit', kwargs={'pk': loan.pk}), self.payload(initial_principal='1000', start_date=loan.start_date.isoformat()))
        self.assertFalse(Loan.objects.get(pk=loan.pk).is_active)

    def test_other_users_loans_are_404_everywhere(self):
        other = self.make_user('other-loan-edit')
        theirs = self.loan(user=other)
        for name in ('loan-edit', 'loan-detail', 'loan-tab-schedule', 'loan-tab-history'):
            self.assertEqual(self.client.get(reverse(name, kwargs={'pk': theirs.pk})).status_code, 404, name)
        self.assertEqual(self.client.post(reverse('loan-edit', kwargs={'pk': theirs.pk}), self.payload()).status_code, 404)
        self.assertEqual(self.client.post(reverse('loan-delete', kwargs={'pk': theirs.pk})).status_code, 404)
        self.assertEqual(self.client.post(reverse('loan-repayment-create', kwargs={'pk': theirs.pk}), {}).status_code, 404)
        self.assertEqual(self.client.post(reverse('loan-rate-update', kwargs={'pk': theirs.pk}), {}).status_code, 404)
        self.assertTrue(Loan.objects.filter(pk=theirs.pk).exists())

    def test_delete_removes_the_loan_gives_money_back_and_says_so(self):
        loan = self.loan()
        self.pay(loan, 4000, 1000)
        self.assertEqual(self.bal(self.cash), D('95000.00'))
        response = self.client.post(reverse('loan-delete', kwargs={'pk': loan.pk}))
        self.assertRedirects(response, reverse('loan-list'))
        self.assertFalse(Loan.objects.filter(pk=loan.pk).exists())
        self.assertEqual(self.bal(self.cash), D('100000.00'))
        self.assertIn('Loan deleted successfully.', self.messages(response))

    def test_opening_the_delete_url_does_not_crash_and_deletes_nothing(self):
        """Regression: GET /loans/<id>/delete/ raised TemplateDoesNotExist."""
        loan = self.loan()
        response = self.client.get(reverse('loan-delete', kwargs={'pk': loan.pk}))
        self.assertRedirects(response, reverse('loan-detail', kwargs={'pk': loan.uuid}), fetch_redirect_response=False)
        self.assertTrue(Loan.objects.filter(pk=loan.pk).exists())

    def test_delete_by_uuid(self):
        loan = self.loan()
        self.client.post(reverse('loan-delete', kwargs={'pk': loan.uuid}))
        self.assertFalse(Loan.objects.filter(pk=loan.pk).exists())


class TestLoanDetail(LoanBase):
    def detail(self, loan):
        response = self.client.get(reverse('loan-detail', kwargs={'pk': loan.pk}))
        self.assertEqual(response.status_code, 200)
        return response.context

    def test_summary_schedule_and_history(self):
        loan = self.loan('100000', '12', 12, started=today() - relativedelta(months=2))
        self.pay(loan, 8000, 1000, when=today() - timedelta(days=30))
        self.pay(loan, 8100, 900)
        ctx = self.detail(loan)
        self.assertEqual(ctx['summary']['principal_paid'], 16100.0)
        self.assertEqual([r.principal_portion for r in ctx['repayments']], [D('8100.00'), D('8000.00')])   # newest first
        self.assertEqual(len(ctx['schedule']), 10)
        self.assertIsNotNone(ctx['extra_emi_savings'])

    def test_breakdown_chart_adds_up_to_the_loan(self):
        loan = self.loan('100000', opening_paid_principal=D('5000'))
        self.pay(loan, 10000, 500)
        self.prepay(loan, 15000)
        labels_values = self.detail(loan)['breakdown_chart_data']['values']
        principal_paid, interest_paid, remaining = labels_values
        self.assertEqual((principal_paid, interest_paid, remaining), (30000.0, 500.0, 70000.0))
        self.assertEqual(principal_paid + remaining, 100000.0)

    def test_linked_capital_events_are_listed_with_their_total(self):
        loan = self.loan('100000')
        self.prepay(loan, 15000, 'loan_down_payment')
        self.prepay(loan, 1000, 'other')
        ctx = self.detail(loan)
        self.assertEqual(len(ctx['linked_capital_events']), 2)
        self.assertEqual(ctx['linked_capital_total'], 16000.0)

    def test_amortization_chart_for_an_open_loan_joins_history_and_future(self):
        loan = self.loan('100000', '12', 12, started=today() - relativedelta(months=2))
        self.pay(loan, 8000, 1000, when=today() - timedelta(days=30))
        chart = self.detail(loan)['amortization_chart_data']
        self.assertFalse(chart['is_historical'])
        self.assertEqual(chart['history_count'], 1)
        self.assertEqual(len(chart['labels']), 1 + 10)
        self.assertEqual(chart['balance'][-1], 0.0)
        self.assertEqual(chart['balance'][0], 92000.0)

    def test_a_paid_off_loan_shows_its_history_only(self):
        loan = self.loan('1000', '12', 12)
        self.pay(loan, 1000, 10)
        ctx = self.detail(loan)
        self.assertEqual(ctx['schedule'], [])
        self.assertTrue(ctx['amortization_chart_data']['is_historical'])
        self.assertEqual(ctx['amortization_chart_data']['balance'][-1], 0.0)
        self.assertIsNone(ctx['extra_emi_savings'])

    def test_empty_loan_has_a_schedule_but_no_history(self):
        ctx = self.detail(self.loan('100000', '12', 12, started=today()))
        self.assertEqual(len(ctx['schedule']), 12)
        self.assertEqual(ctx['amortization_chart_data']['history_count'], 0)

    def test_trend_text(self):
        loan = self.loan('100000', '12', 12, started=today() - relativedelta(months=4))
        for m in (3, 2, 1):
            self.pay(loan, 5000, 100, when=today() - relativedelta(months=m))
        text = self.detail(loan)['trend_summary_text']
        self.assertIn('95,000', text)
        self.assertIn('85,000', text)

    def test_tabs_render(self):
        loan = self.loan()
        self.pay(loan, 1000, 10)
        sched = self.client.get(reverse('loan-tab-schedule', kwargs={'pk': loan.pk}))
        hist = self.client.get(reverse('loan-tab-history', kwargs={'pk': loan.pk}))
        self.assertEqual((sched.status_code, hist.status_code), (200, 200))
        self.assertEqual(len(hist.context['repayments']), 1)
        self.assertTrue(sched.context['schedule'])


class TestRepaymentViews(LoanBase):
    def post(self, loan, **overrides):
        data = {'from_account': str(self.cash.id), 'amount': '5000', 'principal_portion': '4000',
                'interest_portion': '1000', 'date': today().isoformat()}
        data.update(overrides)
        return self.client.post(reverse('loan-repayment-create', kwargs={'pk': loan.pk}), data)

    def test_record_a_repayment(self):
        loan = self.loan()
        response = self.post(loan)
        self.assertRedirects(response, reverse('loan-detail', kwargs={'pk': loan.uuid}), fetch_redirect_response=False)
        r = loan.repayments.get()
        self.assertEqual((r.amount, r.principal_portion, r.interest_portion, r.from_account), (D('5000'), D('4000'), D('1000'), self.cash))
        self.assertEqual(self.bal(self.cash), D('95000.00'))
        self.assertIn('Repayment recorded successfully!', self.messages(response))

    def test_amount_only_is_split_with_the_loans_interest(self):
        loan = self.loan('100000', '12')
        self.post(loan, principal_portion='', interest_portion='')
        r = loan.repayments.get()
        self.assertEqual((r.interest_portion, r.principal_portion), (D('1000.00'), D('4000.00')))

    def test_invalid_repayments_are_refused_with_a_message(self):
        loan = self.loan()
        for override in ({'amount': '0'}, {'amount': '5000', 'principal_portion': '3000'},
                         {'date': (today() + timedelta(days=30)).isoformat()}, {'date': 'garbage'}):
            with self.subTest(override=override):
                response = self.post(loan, **override)
                self.assertEqual(response.status_code, 302)
                self.assertTrue(any('Error recording repayment' in m for m in self.messages(response)))
        self.assertEqual(loan.repayments.count(), 0)
        self.assertEqual(self.bal(self.cash), D('100000.00'))

    def test_cannot_pay_from_someone_elses_account(self):
        other = self.make_user('other-repay-view')
        theirs = Account.objects.create(user=other, name='T', account_type='CASH_WALLET', balance=10, currency='₹')
        self.post(self.loan(), from_account=str(theirs.id))
        self.assertEqual(LoanRepayment.objects.count(), 0)

    def test_paying_the_last_of_the_principal_closes_the_loan(self):
        loan = self.loan('1000', '0')
        self.post(loan, amount='1000', principal_portion='1000', interest_portion='0')
        self.assertFalse(Loan.objects.get(pk=loan.pk).is_active)

    def test_conversion_failure_is_reported_and_nothing_is_saved(self):
        loan = self.loan('1000', currency='$')
        with patch('expenses.models.get_exchange_rate', side_effect=RuntimeError('fx down')):
            response = self.post(loan, amount='50', principal_portion='40', interest_portion='10')
        self.assertTrue(any('currency conversion failed' in m for m in self.messages(response)))
        self.assertEqual(LoanRepayment.objects.count(), 0)

    def test_make_recurring_creates_one_schedule(self):
        loan = self.loan()
        response = self.post(loan, add_to_recurring='on', recurring_frequency='MONTHLY')
        rt = RecurringTransaction.objects.get()
        self.assertEqual((rt.transaction_type, rt.loan, rt.amount, rt.frequency, rt.account, rt.start_date, rt.description),
                         ('LOAN', loan, D('5000.00'), 'MONTHLY', self.cash, today(), 'Loan EMI: Home'))
        self.assertIn('Recurring loan repayment created.', self.messages(response))

    def test_make_recurring_twice_with_identical_details_makes_one(self):
        loan = self.loan()
        self.post(loan, add_to_recurring='on', recurring_frequency='MONTHLY')
        self.post(loan, add_to_recurring='on', recurring_frequency='MONTHLY', principal_portion='3999', interest_portion='1001')
        self.assertEqual(RecurringTransaction.objects.count(), 1)

    def test_make_recurring_collision_on_another_account_is_a_warning_not_a_500(self):
        """Regression: IntegrityError from the schedule uniqueness rule escaped as a 500."""
        loan = self.loan()
        bank = Account.objects.create(user=self.user, name='Bank', account_type='SAVINGS_ACCOUNT', balance=D('50000'), currency='₹')
        self.post(loan, add_to_recurring='on', recurring_frequency='MONTHLY')
        response = self.post(loan, add_to_recurring='on', recurring_frequency='MONTHLY', from_account=str(bank.id))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(loan.repayments.count(), 2)                      # the payment itself was recorded
        self.assertEqual(RecurringTransaction.objects.count(), 1)
        self.assertTrue(any('identical recurring repayment already exists' in m for m in self.messages(response)))

    def test_delete_a_repayment(self):
        loan = self.loan()
        r = self.pay(loan, 4000, 1000)
        url = reverse('loan-repayment-delete', kwargs={'pk': r.pk})
        response = self.client.post(url)
        self.assertRedirects(response, reverse('loan-detail', kwargs={'pk': loan.uuid}), fetch_redirect_response=False)
        self.assertFalse(LoanRepayment.objects.filter(pk=r.pk).exists())
        self.assertEqual(self.bal(self.cash), D('100000.00'))
        self.assertIn('Repayment deleted successfully.', self.messages(response))

    def test_opening_the_repayment_delete_url_does_not_crash(self):
        r = self.pay(self.loan(), 4000, 1000)
        response = self.client.get(reverse('loan-repayment-delete', kwargs={'pk': r.pk}))
        self.assertEqual(response.status_code, 302)
        self.assertTrue(LoanRepayment.objects.filter(pk=r.pk).exists())

    def test_repayment_delete_next_param_only_same_site(self):
        loan = self.loan()
        r1, r2 = self.pay(loan, 100), self.pay(loan, 100, when=today() - timedelta(days=3))
        self.assertEqual(self.client.post(reverse('loan-repayment-delete', kwargs={'pk': r1.pk}) + '?next=/loans/').url, '/loans/')
        self.assertEqual(self.client.post(reverse('loan-repayment-delete', kwargs={'pk': r2.pk}) + '?next=https://evil.example/').url,
                         reverse('loan-detail', kwargs={'pk': loan.uuid}))

    def test_other_users_repayment_cannot_be_deleted(self):
        other = self.make_user('other-repay-del')
        theirs_loan = self.loan(user=other)
        theirs = LoanRepayment.objects.create(loan=theirs_loan, date=today(), amount=D('10'), principal_portion=D('10'), interest_portion=D('0'))
        self.assertEqual(self.client.post(reverse('loan-repayment-delete', kwargs={'pk': theirs.pk})).status_code, 404)
        self.assertTrue(LoanRepayment.objects.filter(pk=theirs.pk).exists())


class TestRateUpdates(LoanBase):
    def update(self, loan, **data):
        payload = {'interest_rate': '9.5', 'effective_date': today().isoformat()}
        payload.update(data)
        return self.client.post(reverse('loan-rate-update', kwargs={'pk': loan.pk}), payload)

    def test_add_a_rate(self):
        loan = self.loan(rate='12')
        response = self.update(loan)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(loan.interest_rates.count(), 2)
        self.assertEqual(loan.interest_rates.order_by('-effective_date').first().interest_rate, D('9.50'))
        self.assertIn('Interest rate updated successfully!', self.messages(response))

    def test_invalid_rates_are_refused(self):
        loan = self.loan(rate='12')
        for data in ({'interest_rate': '-1'}, {'interest_rate': '101'}, {'interest_rate': 'x'}, {'effective_date': ''}):
            with self.subTest(data=data):
                response = self.update(loan, **data)
                self.assertTrue(any('Error updating interest rate' in m for m in self.messages(response)))
        self.assertEqual(loan.interest_rates.count(), 1)

    def test_the_schedule_follows_the_new_rate_once_it_is_in_force(self):
        loan = self.loan('100000', '12', 12, started=today())
        self.update(loan, interest_rate='24', effective_date=today().isoformat())
        self.assertEqual(LoanService.generate_amortization_schedule(Loan.objects.get(pk=loan.pk))[0]['interest'], 2000.0)

    def test_a_scheduled_future_rate_changes_nothing_yet(self):
        loan = self.loan('100000', '12', 12, started=today())
        self.update(loan, interest_rate='24', effective_date=(today() + timedelta(days=60)).isoformat())
        self.assertEqual(LoanService.generate_amortization_schedule(Loan.objects.get(pk=loan.pk))[0]['interest'], 1000.0)


# ---------------------------------------------------------------------------
# Posting engine and loans
# ---------------------------------------------------------------------------
class TestEngineRates(LoanBase):
    def schedule_emi(self, loan, amount='5000', start=None):
        return RecurringTransaction.objects.create(
            user=self.user, transaction_type='LOAN', amount=D(amount), currency='₹', account=self.cash, loan=loan,
            frequency='MONTHLY', start_date=start or today(), description='EMI')

    def test_a_rate_that_starts_in_the_future_is_not_applied_today(self):
        """Regression: the newest rate was used even if it only starts months from now."""
        loan = self.loan('100000', '12', started=today())
        LoanInterestRate.objects.create(loan=loan, interest_rate=D('24'), effective_date=today() + relativedelta(months=6))
        self.schedule_emi(loan)
        process_user_recurring_transactions(self.user, force=True)
        self.assertEqual(loan.repayments.get().interest_portion, D('1000.00'))

    def test_catching_up_across_a_rate_change_uses_the_rate_of_each_month(self):
        start = today() - relativedelta(months=3)
        loan = self.loan('100000', '12', 24, started=start)
        LoanInterestRate.objects.create(loan=loan, interest_rate=D('24'), effective_date=start + relativedelta(months=2))
        self.schedule_emi(loan, start=start)
        process_user_recurring_transactions(self.user, force=True)
        rows = list(loan.repayments.order_by('date'))
        self.assertEqual(len(rows), 4)
        # months 0 and 1 at 12%, months 2 and 3 at 24% on the then-remaining balance
        balance = D('100000')
        for index, row in enumerate(rows):
            rate = D('12') if index < 2 else D('24')
            expected_interest = (balance * rate / 100 / 12).quantize(D('0.01'))
            self.assertEqual(row.interest_portion, expected_interest, index)
            balance -= row.principal_portion

    def test_posted_interest_matches_the_amortization_schedule(self):
        loan = self.loan('100000', '12', 12, started=today())
        emi = LoanService.calculate_emi(100000, 12, 12)
        first = LoanService.generate_amortization_schedule(Loan.objects.get(pk=loan.pk))[0]   # before anything is posted
        self.schedule_emi(loan, amount=str(emi))
        process_user_recurring_transactions(self.user, force=True)
        posted = loan.repayments.get()
        self.assertEqual((float(posted.interest_portion), float(posted.principal_portion)), (first['interest'], first['principal']))

    def test_full_run_pays_the_loan_off_in_the_scheduled_number_of_months(self):
        start = today() - relativedelta(months=12)
        loan = self.loan('12000', '0', 12, started=start)
        self.schedule_emi(loan, amount='1000', start=start)
        process_user_recurring_transactions(self.user, force=True)
        self.assertEqual(loan.repayments.count(), 12)
        loan.refresh_from_db()
        self.assertFalse(loan.is_active)
        self.assertEqual(self.bal(self.cash), D('88000.00'))


class TestLoanFlowZeroRate(LoanBase):
    def test_zero_percent_loan_that_does_not_divide_evenly_can_be_created(self):
        """Regression: EMI 33333.3333 failed the amount's 2-decimal rule and the flow crashed."""
        from expenses.flows.registry import FlowRegistry
        flow = FlowRegistry.get('loan')
        form = flow.form_class(data=dict(name='Zero', principal='100000', annual_rate='0', tenure_months='3',
                                         start_date=today().isoformat(), create_repayment_schedule='on',
                                         payment_account=self.cash.id, repayment_type='EMI', loan_type='PERSONAL',
                                         repayment_is_active='on'), user=self.user)
        self.assertTrue(form.is_valid(), dict(form.errors))
        result = flow.commit(self.user, form.cleaned_data, uuid.uuid4())
        emi = next(o for o in result.created if isinstance(o, RecurringTransaction))
        self.assertEqual(emi.amount, D('33333.33'))


class TestEmiCalculatorPage(TestCase):
    def test_is_public_and_shows_the_defaults(self):
        response = self.client.get(reverse('loan-emi-calculator'))
        self.assertEqual(response.status_code, 200)
        ctx = response.context
        self.assertEqual((ctx['default_principal'], ctx['default_rate'], ctx['default_months']), (1000000, 10.5, 60))
        self.assertEqual(ctx['default_emi'], LoanService.calculate_emi(1000000, 10.5, 60))
        self.assertAlmostEqual(ctx['default_total_interest'], ctx['default_emi'] * 60 - 1000000, places=2)


class TestLoanDocs(LoanBase):
    def test_loan_types_match_the_guide(self):
        self.assertEqual([label for _code, label in Loan.LOAN_TYPES],
                         ['Home Loan', 'Car Loan', 'Personal Loan', 'Education Loan', 'Business Loan', 'Other'])
        self.assertEqual([code for code, _l in Loan.REPAYMENT_TYPE_CHOICES], ['EMI', 'BULLET', 'INTEREST_ONLY'])

    def test_the_guides_emi_example(self):
        self.assertEqual(round(LoanService.calculate_emi(120000, 14, 12)), 10774)
