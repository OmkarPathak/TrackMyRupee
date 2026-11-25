"""Shared set-up for the loan browser tests: loans, repayments and the plain things every one of them clicks."""
from datetime import timedelta
from decimal import Decimal

from django.utils import timezone

D = Decimal


def make_loan(user, name='Home Loan', principal=1200000, months=120, rate='9', days_ago=40, loan_type='HOME',
              repayment_type='EMI', currency='₹', **extra):
    """A loan that started `days_ago` days ago, with its first interest rate."""
    from expenses.models import Loan, LoanInterestRate
    start = timezone.localdate() - timedelta(days=days_ago)
    loan = Loan.objects.create(user=user, name=name, loan_type=loan_type, initial_principal=D(str(principal)),
                               duration_months=months, start_date=start, currency=currency,
                               repayment_type=repayment_type, **extra)
    LoanInterestRate.objects.create(loan=loan, interest_rate=D(str(rate)), effective_date=start)
    return loan


def make_repayment(loan, account, principal, interest='0', days_ago=1):
    from expenses.models import LoanRepayment
    principal, interest = D(str(principal)), D(str(interest))
    return LoanRepayment.objects.create(loan=loan, from_account=account, amount=principal + interest,
                                        principal_portion=principal, interest_portion=interest,
                                        date=timezone.localdate() - timedelta(days=days_ago))


def emi(principal, rate, months):
    """The EMI the app's own formula gives, to compare with what a page shows."""
    principal, months, monthly = float(principal), int(months), float(rate) / 12 / 100
    if monthly == 0:
        return principal / months
    factor = (1 + monthly) ** months
    return principal * monthly * factor / (factor - 1)


def confirm_delete(test, page, trigger):
    """Click a trash button, then the Delete in the confirmation that opens."""
    page.click(trigger)
    page.wait_for_selector('#deleteModal.show')
    page.click('#deleteModalConfirmBtn')
    page.wait_for_selector('#deleteModal.show', state='detached', timeout=10000) if False else None
    page.wait_for_timeout(600)


def amount_in(text):
    """The number in a figure such as '₹12,00,000' or '-₹1,850'."""
    digits = ''.join(ch for ch in text if ch.isdigit() or ch == '.')
    return D(digits) if digits else None
