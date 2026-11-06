"""Demo-user data for the features added after the first demo seed (setup_demo_user).

The demo is the first thing a visitor sees of TrackMyRupee, so it should show what the app can do: family sharing and an
emergency sheet, insurance policies with premiums, refunds, mutual funds bought through SIPs and a sale with its profit,
versioned budgets, a foreign-currency account, a car with its loan, past net worth snapshots and so on.

Every section is its own function, run in its own savepoint: if one of them fails the others (and the core demo) still
load, and the failure is printed. Everything here is demo data for the fictional "Riya" and her family; nothing is real.
"""
from __future__ import annotations

import logging
from contextlib import contextmanager
from datetime import date, timedelta
from decimal import ROUND_DOWN, Decimal
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth.models import User
from django.db import transaction
from django.utils import timezone

import expenses.models as m
from expenses.models import (
    Account,
    CapitalEvent,
    Category,
    Expense,
    Income,
    Loan,
    LoanInterestRate,
    LoanRepayment,
    PhysicalAsset,
    RecurringTransaction,
    Transfer,
)

logger = logging.getLogger(__name__)
D = Decimal

SEED_VERSION = 2                    # bump when the demo gains data: the demo reseeds when the marker below is missing
MARKER_SLUG = f'demo-seed-v{SEED_VERSION}'
FAMILY_USERNAMES = ('demo_arjun', 'demo_meera')
REFUND = 'Refund / Reimbursement'


def marker_present(user) -> bool:
    """Whether the demo was seeded with the current version of this data."""
    return m.Notification.objects.filter(user=user, slug=MARKER_SLUG).exists()


@contextmanager
def _quiet():
    """Seeding must never send mail (the demo user's address is not real). Only the family modules' own mail function is
    silenced, not the mail settings: other visitors are served while the demo reseeds."""
    with patch('expenses.family.emergency.send_mail'), patch('expenses.family.notices.send_mail'), \
            patch('expenses.family.emails.send_mail'):
        yield


def _run(stdout, style, label, fn, *args):
    try:
        with transaction.atomic(), _quiet():
            fn(*args)
        stdout.write(style.SUCCESS(f'Demo extras: {label}'))
    except Exception as exc:        # noqa: BLE001 - a missing extra must not break the demo
        logger.exception('demo seed section failed: %s', label)
        stdout.write(style.ERROR(f'Demo extras FAILED ({label}): {type(exc).__name__}: {exc}'))


# ── reset ─────────────────────────────────────────────────────────────────────────────────────────────────────────────

def wipe_user_data(user) -> None:
    """Delete a demo user's data in dependency order (the user row itself stays: a stable id keeps the demo login simple)."""
    m.EmergencySheet.objects.filter(owner=user).delete()            # its items, who may ask, and the requests
    m.Household.objects.filter(owner=user).delete()                 # members, shares, invitations
    m.HouseholdMember.objects.filter(user=user).delete()
    m.GoalContribution.objects.filter(goal__user=user).delete()
    m.SavingsGoal.objects.filter(user=user).delete()
    m.FinancialFlow.objects.filter(user=user).delete()
    m.AccountReconciliation.objects.filter(user=user).delete()
    m.BudgetLimitVersion.objects.filter(user=user).delete()
    m.BudgetMonthOverride.objects.filter(user=user).delete()
    m.BudgetSuggestionDismissal.objects.filter(user=user).delete()
    m.ExpenseKeywordHint.objects.filter(user=user).delete()
    m.InsightFeedback.objects.filter(user=user).delete()
    m.InsightState.objects.filter(user=user).delete()
    m.Transfer.objects.filter(user=user).delete()
    m.Expense.objects.filter(user=user).delete()
    m.Income.objects.filter(user=user).delete()
    m.LoanRepayment.objects.filter(loan__user=user).delete()
    m.LoanInterestRate.objects.filter(loan__user=user).delete()
    m.CapitalEvent.objects.filter(user=user).delete()
    m.Loan.objects.filter(user=user).delete()
    m.RecurringTransaction.objects.filter(user=user).delete()
    m.Notification.objects.filter(user=user).delete()
    m.NetWorthSnapshot.objects.filter(user=user).delete()
    m.ConsentEvent.objects.filter(user=user).delete()
    m.FinancialAuditLog.objects.filter(user=user).delete()
    m.LedgerReconciliationReport.objects.filter(user=user).delete()
    # the ledger: lines first (PROTECT on the ledger account), then entries, then the accounts
    m.JournalLine.objects.filter(journal_entry__user=user).delete()
    m.JournalLine.objects.filter(ledger_account__user=user).delete()
    m.JournalEntry.objects.filter(user=user).delete()
    m.LedgerAccount.objects.filter(user=user).delete()
    m.AssetValuation.objects.filter(asset__user=user).delete()
    m.PhysicalAsset.objects.filter(user=user).delete()
    m.Valuation.objects.filter(holding__account__user=user).delete()
    m.Holding.objects.filter(account__user=user).delete()
    m.Account.objects.filter(user=user).delete()
    m.Category.objects.filter(user=user).delete()


def wipe_family_users() -> None:
    for username in FAMILY_USERNAMES:
        member = User.objects.filter(username=username).first()
        if member is not None:
            wipe_user_data(member)


# ── people ────────────────────────────────────────────────────────────────────────────────────────────────────────────

def _make_member(username: str, first_name: str) -> User:
    user = User.objects.filter(username=username).first()
    if user is None:
        user = User.objects.create_user(username=username, email=f'{username}@example.com', first_name=first_name)
    user.first_name = first_name
    user.set_unusable_password()
    user.save(update_fields=['first_name', 'password'])
    profile, _ = m.UserProfile.objects.get_or_create(user=user)
    profile.tier = 'PRO'
    profile.is_lifetime = True
    profile.currency = '₹'
    profile.has_seen_tutorial = True
    profile.consent_granted = True
    profile.consent_timestamp = timezone.now()
    profile.consent_version = '1.0'
    profile.save()
    return User.objects.get(pk=user.pk)          # a fresh copy, so the plan just saved is what is read


def _opening(user, account, amount, when, label='Opening Balance'):
    return Income.objects.create(user=user, source=label, source_type='Other', amount=D(str(amount)), date=when,
                                 description='Demo seed corpus', account=account, currency=account.currency)


# ── accounts the first seed did not have ──────────────────────────────────────────────────────────────────────────────

def create_accounts(user, today: date) -> SimpleNamespace:
    """Accounts for what Indians actually hold: a mutual fund account, a separate demat for stocks (the first seed made
    the demat hold everything), a recurring deposit, PPF, EPF and NPS, a dollar account and a car."""
    out = SimpleNamespace()
    out.mf = Account.objects.create(user=user, name='Groww Mutual Funds', account_type='MUTUAL_FUND',
                                    balance=D('0.00'), currency='₹')
    out.rd = Account.objects.create(user=user, name='Axis Bank RD', account_type='RD', balance=D('0.00'), currency='₹',
                                    deposit_maturity_date=today + timedelta(days=300), show_accrued_balance=True)
    out.ppf = Account.objects.create(user=user, name='PPF (SBI)', account_type='PPF', balance=D('0.00'), currency='₹')
    out.epf = Account.objects.create(user=user, name='EPF', account_type='EPF', balance=D('0.00'), currency='₹')
    out.nps = Account.objects.create(user=user, name='NPS Tier 1', account_type='NPS', balance=D('0.00'), currency='₹')
    out.usd = Account.objects.create(user=user, name='Wise USD Account', account_type='SAVINGS_ACCOUNT',
                                     balance=D('0.00'), currency='$')
    return out


def fund_accounts(user, acc, window_start: date, today: date) -> None:
    """Opening balances for the accounts above, through ledger-backed income like the rest of the demo."""
    opening = window_start - timedelta(days=1)
    _opening(user, acc.rd, 60000, opening)
    _opening(user, acc.ppf, 92000, opening)
    _opening(user, acc.epf, 185000, opening)
    _opening(user, acc.nps, 109000, opening)
    _ensure_fx()
    _opening(user, acc.usd, 2400, opening, 'Opening Balance')


def _ensure_fx() -> None:
    """A dollar rate for today, without asking the live service while seeding."""
    today = timezone.localdate()
    for source, target, rate in (('USD', 'INR', D('84.20')), ('INR', 'USD', D('0.011876'))):
        exists = m.FXRate.objects.filter(from_currency=source, to_currency=target, as_of_date__gte=today).exists()
        if not exists:
            m.FXRate.objects.create(from_currency=source, to_currency=target, rate=rate, as_of_date=today,
                                    source='demo_seed')


# ── holdings: bought through SIPs and lumps, one stock sold at a profit ───────────────────────────────────────────────

HOLDINGS = [
    # account key, name, type, target units, avg cost, NAV now, scheme code / isin
    ('mf', 'Nifty 50 Index Fund - Direct Growth', 'MF', D('1250.456'), D('145.20'), D('178.65'), {'scheme_code': '120503'}),
    ('mf', 'Parag Parikh Flexi Cap - Direct Growth', 'MF', D('2300.312'), D('68.00'), D('79.80'), {'scheme_code': '122639'}),
    ('mf', 'HDFC Mid-Cap Opportunities - Direct Growth', 'MF', D('540.000'), D('118.00'), D('142.30'), {'scheme_code': '119077'}),
    ('demat', 'Reliance Industries Ltd', 'STOCK', D('25.000000'), D('2450.00'), D('2890.50'), {'isin': 'INE002A01018'}),
    ('demat', 'Infosys Ltd', 'STOCK', D('40.000000'), D('1380.00'), D('1625.75'), {'isin': 'INE009A01021'}),
    ('demat', 'Tata Consultancy Services', 'STOCK', D('15.000000'), D('3800.00'), D('4120.00'), {'isin': 'INE467B01029'}),
]


def seed_holdings(user, acc, today: date, window_start: date, sips: list[Transfer], lump: Transfer | None) -> None:
    """Holdings through the real purchase service: an opening position, SIP instalments linked to their transfers, a
    lump sum, and a part-sale of Infosys so the realised profit shows."""
    from expenses import holdings_service as hs

    accounts = {'mf': acc.mf, 'demat': acc.demat}
    created, opening_cost = {}, {}
    for key, name, kind, units, avg, nav, ids in HOLDINGS:
        account = accounts[key]
        lots = []                                           # (date, units, price, transfer)
        if name.startswith('Nifty'):                         # every SIP instalment buys units at about that month's NAV
            for i, transfer in enumerate(sips):
                price = (nav * (D('0.80') + D('0.025') * i)).quantize(D('0.0001'))
                lots.append((transfer.date, (transfer.amount / price).quantize(D('0.001'), rounding=ROUND_DOWN), price, transfer))
        if name.startswith('Parag') and lump is not None:
            price = (nav * D('0.93')).quantize(D('0.0001'))
            lots.append((lump.date, (lump.amount / price).quantize(D('0.001'), rounding=ROUND_DOWN), price, lump))
        bought = sum((u for _, u, _, _ in lots), D('0'))
        opening_units = units - bought
        spent = sum((u * p for _, u, p, _ in lots), D('0'))
        opening_price = ((units * avg - spent) / opening_units).quantize(D('0.0001')) if opening_units > 0 else avg
        holding = hs.create_holding(account, name=name, units=opening_units, price=opening_price,
                                    date=window_start - timedelta(days=200), **ids)
        opening_cost[account.pk] = opening_cost.get(account.pk, D('0')) + (opening_units * opening_price)
        for when, u, price, transfer in lots:
            hs.add_purchase(holding, date=when, units=u, price=price, transfer=transfer, note='SIP' if transfer in sips else '')
        created[name] = (holding, nav)
        # today's value, and one a month ago for the sparkline
        m.Valuation.objects.create(holding=holding, value=(holding.units * nav).quantize(D('0.01')), as_of_date=today,
                                   unit_nav=nav, source='demo_seed')
        past_nav = (nav * D('0.94')).quantize(D('0.000001'))
        m.Valuation.objects.create(holding=holding, value=(holding.units * past_nav).quantize(D('0.01')),
                                   as_of_date=today - timedelta(days=30), unit_nav=past_nav, source='demo_seed')
    # The money behind the opening positions: without it the Holdings page warns that holdings cost more than the cash
    # recorded in the account. The SIP instalments and the lump sum are funded by their own transfers.
    for account in accounts.values():
        cost = opening_cost.get(account.pk)
        if cost:
            _opening(user, account, (cost + D('5000')).quantize(D('1')), window_start - timedelta(days=201), 'Opening Balance')
    # a part-sale of Infosys: 10 shares at a higher price than the average cost
    infosys, nav = created['Infosys Ltd']
    hs.add_sale(infosys, date=today - timedelta(days=45), units=D('10'), price=(nav * D('0.97')).quantize(D('0.01')),
                note='Booked some profit')
    infosys.refresh_from_db()
    m.Valuation.objects.filter(holding=infosys, as_of_date=today).update(value=(infosys.units * nav).quantize(D('0.01')))
    acc.holdings = created


# ── a recurring SIP split across funds ────────────────────────────────────────────────────────────────────────────────

def seed_sip_schedule(user, acc, today: date, window_start: date) -> None:
    sip = RecurringTransaction.objects.create(
        user=user, transaction_type='TRANSFER', amount=D('15000.00'), description='Monthly SIP', frequency='MONTHLY',
        start_date=window_start, last_processed_date=today.replace(day=5) if today.day >= 5 else (today.replace(day=1) - timedelta(days=26)),
        account=acc.main, to_account=acc.mf)
    nifty, _ = acc.holdings['Nifty 50 Index Fund - Direct Growth']
    flexi, _ = acc.holdings['Parag Parikh Flexi Cap - Direct Growth']
    m.RecurringFundSplit.objects.create(recurring=sip, holding=nifty, amount=D('10000.00'))
    m.RecurringFundSplit.objects.create(recurring=sip, holding=flexi, amount=D('5000.00'))


# ── insurance ─────────────────────────────────────────────────────────────────────────────────────────────────────────

def seed_insurance(user, acc, cats, today: date) -> None:
    """A term plan, a family health floater and an old endowment policy. Premiums are expenses linked to the policy, and
    each policy has the schedule that reminds the family when the next one is due."""
    cat, _ = Category.objects.get_or_create(user=user, name='Insurance', defaults={'limit': 5000, 'icon': 'bi-shield-check'})
    cats['Insurance'] = cat
    specs = [
        # name, account type, policy type, cover, premium, how often, covers, due in days, paid years back, savings value
        ('HDFC Click 2 Protect', 'LIFE_INSURANCE', 'PROTECTION', 20000000, 18500, 'ANNUAL', 'Riya, Arjun', 34, 3, None),
        ('Star Health Family Floater', 'HEALTH_INSURANCE', 'PROTECTION', 1500000, 32000, 'ANNUAL', 'Riya, Arjun, Meera', 19, 2, None),
        ('LIC Jeevan Anand', 'LIFE_INSURANCE', 'SAVINGS', 500000, 24000, 'ANNUAL', 'Riya', 160, 4, 98000),
    ]
    for name, account_type, policy_type, cover, premium, freq, covers, due_in, paid_years, surrender in specs:
        next_due = today + timedelta(days=due_in)
        start = date(next_due.year - paid_years, next_due.month, min(next_due.day, 28))
        asset = PhysicalAsset.objects.create(
            user=user, name=name, asset_class='INSURANCE', sum_assured=D(str(cover)), premium_amount=D(str(premium)),
            premium_frequency=freq, policy_start_date=start, policy_type=policy_type, covers=covers,
            policy_number=f'DEMO-{abs(hash(name)) % 10**8:08d}', currency='₹')
        account = Account.objects.create(user=user, name=name, account_type=account_type, balance=D('0.00'),
                                         currency='₹', linked_physical_asset=asset)
        paid = []
        for years_back in range(paid_years):
            paid_on = date(next_due.year - 1 - years_back, next_due.month, min(next_due.day, 28))
            if paid_on <= today:
                paid.append(Expense.objects.create(
                    user=user, category='Insurance', amount=D(str(premium)), date=paid_on, description=f'{name} premium',
                    payment_method='Net Banking', account=acc.main, linked_physical_asset=asset))
        if surrender:
            m.AssetValuation.objects.create(asset=asset, value=D(str(surrender)), as_of_date=today - timedelta(days=20),
                                            source='Insurer statement')
        RecurringTransaction.objects.create(
            user=user, transaction_type='EXPENSE', amount=D(str(premium)), description=f'{name} premium', category='Insurance',
            frequency='ANNUALLY', start_date=start, last_processed_date=paid[0].date if paid else None, account=acc.main,
            payment_method='Net Banking')


# ── refunds ───────────────────────────────────────────────────────────────────────────────────────────────────────────

def seed_refunds(user, acc, today: date) -> None:
    """Money that came back against something: friends repaying a trip, a returned order, a reimbursed bill."""
    trip = Expense.objects.create(user=user, category='Travel', amount=D('24000.00'), date=today - timedelta(days=52),
                                  description='Goa trip: flights and stay for four', payment_method='Credit Card',
                                  account=acc.cc_amazon)
    for who, amount, ago in (('Kabir', 6000, 49), ('Anika', 6000, 47), ('Sid', 4000, 44)):
        Income.objects.create(user=user, source=REFUND, source_type=REFUND, amount=D(str(amount)), date=today - timedelta(days=ago),
                              description=f'{who} paid back their share of the Goa trip', account=acc.main,
                              refund_for_expense=trip)
    order = Expense.objects.create(user=user, category='Shopping', amount=D('4999.00'), date=today - timedelta(days=21),
                                   description='Running shoes (returned)', payment_method='Credit Card', account=acc.cc_regalia)
    Income.objects.create(user=user, source=REFUND, source_type=REFUND, amount=D('4999.00'), date=today - timedelta(days=14),
                          description='Refund for the returned shoes', account=acc.main, refund_for_expense=order)
    # the card bills for those purchases were paid, so the demo's cards are not left owing for months
    Transfer.objects.create(user=user, from_account=acc.main, to_account=acc.cc_amazon, amount=D('24000.00'),
                            date=today - timedelta(days=30), description='ICICI Amazon Pay card bill (Goa trip)')
    Transfer.objects.create(user=user, from_account=acc.main, to_account=acc.cc_regalia, amount=D('69000.00'),
                            date=today - timedelta(days=60), description='HDFC Regalia card bill (Kerala trip and shoes)')
    event = CapitalEvent.objects.create(
        user=user, amount=D('64000.00'), date=today - timedelta(days=75), subtype='large_purchase',
        note='Family trip to Kerala: homestay, cabs and activities for eight', account=acc.cc_regalia,
        exclude_from_averages=False, exclude_from_budget=False, include_in_net_worth=False)
    for who, amount, ago in (('Dad', 20000, 70), ('Uncle', 20000, 66)):
        Income.objects.create(user=user, source=REFUND, source_type=REFUND, amount=D(str(amount)), date=today - timedelta(days=ago),
                              description=f'{who} paid their share of the Kerala trip', account=acc.main,
                              refund_for_capital_event=event)


# ── more capital events: the big one-off money moves of a year ───────────────────────────────────────────────────────

def seed_more_capital_events(user, acc, today: date) -> None:
    rows = (
        ('large_purchase', 68000, 140, acc.cc_amazon, 'Washing machine and refrigerator', True, False),
        ('gift_given', 21000, 100, acc.main, 'Cousin\'s wedding gift', True, False),
        ('medical_lump_sum', 32000, 35, acc.savings, 'Dental treatment (root canal and crowns)', True, False),
        ('other', 24000, 170, acc.main, 'Society maintenance and sinking fund, annual', True, False),
        ('large_purchase', 42000, 22, acc.cc_regalia, 'Standing desk and monitor for the home office', True, False),
        ('gift_received', 25000, 12, acc.main, 'Diwali gift from the company', False, True),
    )
    # the card bills for the two big card purchases were paid, so the demo's cards do not pile up debt
    Transfer.objects.create(user=user, from_account=acc.main, to_account=acc.cc_amazon, amount=D('68000.00'),
                            date=today - timedelta(days=118), description='ICICI Amazon Pay card bill (appliances)')
    Transfer.objects.create(user=user, from_account=acc.main, to_account=acc.cc_regalia, amount=D('42000.00'),
                            date=today - timedelta(days=8), description='HDFC Regalia card bill (home office)')
    for subtype, amount, ago, account, note, exclude_averages, in_net_worth in rows:
        CapitalEvent.objects.create(
            user=user, amount=D(str(amount)), date=today - timedelta(days=ago), subtype=subtype, note=note, account=account,
            exclude_from_averages=exclude_averages, exclude_from_budget=True, include_in_net_worth=in_net_worth)


# ── budgets that changed over time ────────────────────────────────────────────────────────────────────────────────────

def seed_budgets(user, cats, today: date, window_start: date) -> None:
    """Limits with history (a raise from the start of this year) and one month with its own limit, as the Budget page
    lets people do."""
    first_this_month = today.replace(day=1)
    for name, old, new in (('Groceries', 5000, 6000), ('Dining Out', 4000, 5000), ('Travel', 8000, 10000)):
        cat = cats.get(name)
        if cat is None:
            continue
        m.BudgetLimitVersion.objects.create(user=user, category=cat, category_name=name, amount=D(str(old)),
                                            effective_from=window_start)
        m.BudgetLimitVersion.objects.create(user=user, category=cat, category_name=name, amount=D(str(new)),
                                            effective_from=first_this_month - timedelta(days=95))
    travel = cats.get('Travel')
    if travel is not None:        # a wedding month: Travel is allowed more, just for that month
        m.BudgetMonthOverride.objects.create(user=user, category=travel, category_name='Travel',
                                             month=(first_this_month + timedelta(days=95)).replace(day=1), amount=D('25000.00'))


# ── small things that make an account feel lived in ───────────────────────────────────────────────────────────────────

def seed_habits(user, acc) -> None:
    """The words the quick-add box has learned (so "swiggy 320" files itself) and a bank statement that was matched."""
    for keyword, category, method, uses in (('swiggy', 'Dining Out', 'UPI', 14), ('zepto', 'Groceries', 'UPI', 11),
                                            ('uber', 'Transport', 'UPI', 9), ('netflix', 'Subscriptions', 'Credit Card', 6),
                                            ('petrol', 'Transport', 'Cash', 5)):
        m.ExpenseKeywordHint.objects.create(user=user, keyword=keyword, category=category, payment_method=method, use_count=uses)


def seed_reconciliations(user, acc, today: date) -> None:
    from expenses.ledger_read_service import LedgerReadService

    _, balances = LedgerReadService.get_net_worth(user)
    for account, ago, note in ((acc.main, 6, 'Matched the HDFC statement'), (acc.savings, 33, 'Matched the SBI passbook')):
        balance = D(str(balances.get(account.id, account.balance))).quantize(D('0.01'))
        m.AccountReconciliation.objects.create(
            user=user, account=account, statement_date=today - timedelta(days=ago), statement_balance=balance,
            app_balance=balance, difference=D('0.00'), status='MATCHED', note=note)


def seed_flows(user, today: date) -> None:
    """A few TMR Flows marked as done, so the Flows page shows progress."""
    import uuid
    for key in ('credit_card', 'loan', 'income'):
        m.FinancialFlow.objects.create(user=user, flow_key=key, spec={'demo': True}, idempotency_key=uuid.uuid4())


# ── a car, with its loan ──────────────────────────────────────────────────────────────────────────────────────────────

def seed_car(user, acc, today: date) -> None:
    start = (today.replace(day=1) - timedelta(days=330)).replace(day=9)
    car = PhysicalAsset.objects.create(user=user, name='Hyundai Creta', asset_class='VEHICLE', acquisition_cost=D('1450000.00'),
                                       acquisition_date=start, currency='₹')
    m.AssetValuation.objects.create(asset=car, value=D('1290000.00'), as_of_date=today - timedelta(days=15), source='Used-car estimate')
    Account.objects.create(user=user, name='Hyundai Creta', account_type='VEHICLE', balance=D('1290000.00'), currency='₹',
                           linked_physical_asset=car)
    loan = Loan.objects.create(user=user, name='Car Loan (HDFC)', loan_type='CAR', initial_principal=D('900000.00'),
                               duration_months=60, start_date=start, currency='₹')
    LoanInterestRate.objects.create(loan=loan, interest_rate=D('8.90'), effective_date=start)
    emi = _emi(D('900000'), D('8.90'), 60)
    remaining = D('900000')
    when = start
    for _ in range(10):
        interest = (remaining * D('8.90') / 1200).quantize(D('0.01'))
        principal = (emi - interest).quantize(D('0.01'))
        when_day = when.replace(day=min(9, 28))
        if when_day > today:
            break
        LoanRepayment.objects.create(loan=loan, from_account=acc.main, amount=emi, principal_portion=principal,
                                     interest_portion=interest, date=when_day)
        remaining -= principal
        when = (when.replace(day=28) + timedelta(days=4)).replace(day=1)
    Account.objects.create(user=user, name='Car Loan (HDFC)', account_type='VEHICLE_LOAN', balance=D('0.00'), currency='₹',
                           linked_loan=loan)
    # the down payment
    CapitalEvent.objects.create(user=user, amount=D('550000.00'), date=start - timedelta(days=3), subtype='loan_down_payment',
                                note='Creta down payment', account=acc.main, linked_loan=loan, exclude_from_averages=True,
                                exclude_from_budget=True, include_in_net_worth=False)


def _emi(principal, rate, months):
    r = rate / 1200
    return (principal * r * (1 + r) ** months / ((1 + r) ** months - 1)).quantize(D('0.01'))


# ── foreign currency ──────────────────────────────────────────────────────────────────────────────────────────────────

def seed_foreign_currency(user, acc, today: date) -> None:
    """A dollar account with a freelance payment in and a software subscription out, shown in rupees alongside the rest."""
    Income.objects.create(user=user, source='Freelance Gig', source_type='Freelance / Consulting', amount=D('650.00'),
                          date=today - timedelta(days=26), description='US client invoice (paid in USD)', account=acc.usd, currency='$')
    Expense.objects.create(user=user, category='Subscriptions', amount=D('20.00'), date=today - timedelta(days=12),
                           description='Figma (billed in USD)', payment_method='Debit Card', account=acc.usd, currency='$')
    Expense.objects.create(user=user, category='Subscriptions', amount=D('12.00'), date=today - timedelta(days=9),
                           description='Notion (billed in USD)', payment_method='Debit Card', account=acc.usd, currency='$')


# ── subscriptions ─────────────────────────────────────────────────────────────────────────────────────────────────────

def seed_subscriptions(user, acc, today: date, window_start: date) -> None:
    for name, amount, day, account, method in (('Netflix Premium', 649, 5, acc.cc_regalia, 'Credit Card'),
                                               ('Spotify Family', 299, 5, acc.cc_regalia, 'Credit Card'),
                                               ('Amazon Prime', 1499, 12, acc.cc_amazon, 'Credit Card')):
        last = today.replace(day=min(day, 28))
        if last > today:
            last = (last.replace(day=1) - timedelta(days=1)).replace(day=min(day, 28))
        RecurringTransaction.objects.create(
            user=user, transaction_type='EXPENSE', amount=D(str(amount)), description=name, category='Subscriptions',
            frequency='MONTHLY' if name != 'Amazon Prime' else 'YEARLY', start_date=window_start, last_processed_date=last,
            payment_method=method, account=account)


# ── family sharing ────────────────────────────────────────────────────────────────────────────────────────────────────

def seed_family(user, acc, today: date, window_start: date) -> None:
    """A family of three: Riya (the demo user) and two members with their own accounts, who share some of them. Riya
    shares the same way, and has prepared an emergency sheet, which Arjun has asked to see."""
    from expenses.family import emergency, households, shares

    user = User.objects.get(pk=user.pk)         # a fresh copy: the plan was just set on the profile
    arjun = _make_member('demo_arjun', 'Arjun')
    meera = _make_member('demo_meera', 'Meera')
    opening = window_start - timedelta(days=1)

    a_save = Account.objects.create(user=arjun, name='ICICI Savings', account_type='SAVINGS_ACCOUNT', balance=D('0.00'), currency='₹')
    a_ppf = Account.objects.create(user=arjun, name='Arjun PPF', account_type='PPF', balance=D('0.00'), currency='₹')
    a_fd = Account.objects.create(user=arjun, name='HDFC Fixed Deposit', account_type='FD', balance=D('600000.00'), currency='₹',
                                  deposit_maturity_date=today + timedelta(days=500), show_accrued_balance=True)
    a_card = Account.objects.create(user=arjun, name='Axis Magnus Credit Card', account_type='CREDIT_CARD', balance=D('0.00'),
                                    currency='₹', credit_limit=D('500000.00'), credit_card_billing_day=8)
    a_car_policy = PhysicalAsset.objects.create(
        user=arjun, name='Digit Motor Insurance', asset_class='INSURANCE', sum_assured=D('900000'), premium_amount=D('14500'),
        premium_frequency='ANNUAL', policy_start_date=today - timedelta(days=330), policy_type='PROTECTION', covers='Creta',
        policy_number='DEMO-MOTOR', currency='₹')
    a_policy = Account.objects.create(user=arjun, name='Digit Motor Insurance', account_type='HEALTH_INSURANCE', balance=D('0.00'),
                                      currency='₹', linked_physical_asset=a_car_policy)
    _opening(arjun, a_save, 412000, opening)
    _opening(arjun, a_ppf, 865000, opening)
    Expense.objects.create(user=arjun, category='Shopping', amount=D('38400.00'), date=today - timedelta(days=9),
                           description='Laptop accessories', payment_method='Credit Card', account=a_card)

    m_save = Account.objects.create(user=meera, name='SBI Pension Account', account_type='SAVINGS_ACCOUNT', balance=D('0.00'), currency='₹')
    m_fd = Account.objects.create(user=meera, name='Senior Citizen FD', account_type='FD', balance=D('1500000.00'), currency='₹',
                                  deposit_maturity_date=today + timedelta(days=700), show_accrued_balance=True)
    m_asset = PhysicalAsset.objects.create(
        user=meera, name='LIC Jeevan Shanti', asset_class='INSURANCE', sum_assured=D('500000'), premium_amount=D('24000'),
        premium_frequency='ANNUAL', policy_start_date=today - timedelta(days=120), policy_type='PROTECTION', covers='Meera',
        policy_number='DEMO-LIC', currency='₹')
    m_policy = Account.objects.create(user=meera, name='LIC Jeevan Shanti', account_type='LIFE_INSURANCE', balance=D('0.00'),
                                      currency='₹', linked_physical_asset=m_asset)
    _opening(meera, m_save, 286000, opening)

    household = households.create_household(user, 'The Sharma family')
    for member in (arjun, meera):
        invitation, _token = households.invite(household, user)
        households.accept_invitation(invitation, member)
    households.invite(household, user)                         # an open invitation: the Members page shows how it looks

    by_name = {a.name: a for a in Account.objects.filter(user=user)}
    for name, level, alias in (('HDFC Bank (Main)', 'AMOUNTS', 'Household account'), ('SBI Fixed Deposit (5Y)', 'AMOUNTS', ''),
                               ('2BHK Flat, Baner', 'AMOUNTS', 'Our home'), ('HDFC Regalia Credit Card', 'AMOUNTS', ''),
                               ('Groww Mutual Funds', 'AMOUNTS', ''), ('Zerodha Demat', 'LIST', '')):
        shares.set_share(user, by_name[name], level, alias=alias)
    for name, covers in (('HDFC Click 2 Protect', 'Riya, Arjun'), ('Star Health Family Floater', 'Riya, Arjun, Meera')):
        shares.set_share(user, by_name[name], 'AMOUNTS', covers=covers)
    for account in (a_save, a_ppf, a_fd, a_card):
        shares.set_share(arjun, account, 'AMOUNTS')
    shares.set_share(arjun, a_policy, 'AMOUNTS', covers='Creta')
    for account in (m_save, m_fd):
        shares.set_share(meera, account, 'AMOUNTS')
    shares.set_share(meera, m_policy, 'AMOUNTS', covers='Meera')

    nominees = {by_name['HDFC Bank (Main)'].pk: 'Arjun', by_name['SBI Fixed Deposit (5Y)'].pk: 'Arjun',
                by_name['HDFC Click 2 Protect'].pk: 'Arjun', by_name['Star Health Family Floater'].pk: 'Arjun',
                by_name['Groww Mutual Funds'].pk: 'Arjun'}
    emergency.save_sheet(user, m.EmergencySheet.REQUEST, nominees, [arjun.pk, meera.pk],
                         'Policy papers and property documents are in the grey cupboard at home. Copies are with our CA, Mr. Deshpande.',
                         'Arjun (spouse), our CA Mr. Deshpande, and the HDFC relationship manager.', access_days=90)
    sheet = emergency.sheet_of(user)
    emergency.ask(arjun, sheet)                                  # Arjun asked: Riya sees a request she can answer


# ── history and the finishing touches ─────────────────────────────────────────────────────────────────────────────────

def seed_net_worth_history(user, today: date) -> None:
    """A month-end net worth for the past year, for Riya and for each family member, worked out from the ledger as it
    stood then."""
    from expenses.ledger_read_service import LedgerReadService

    people = [user] + [u for u in User.objects.filter(username__in=FAMILY_USERNAMES)]
    for person in people:
        month_end = today.replace(day=1) - timedelta(days=1)
        for _ in range(12):
            split = {}
            net_worth, balances = LedgerReadService.get_net_worth(person, as_of=month_end, detail=split)
            assets = split.get('assets', net_worth if net_worth > 0 else D('0'))
            liabilities = split.get('liabilities', D('0'))
            m.NetWorthSnapshot.objects.update_or_create(
                user=person, as_of_date=month_end,
                defaults={'total_net_worth': net_worth, 'total_assets': assets, 'total_liabilities': liabilities,
                          'breakdown': {k: str(v) for k, v in balances.items()}})
            month_end = month_end.replace(day=1) - timedelta(days=1)


def seed_family_history(user, today: date) -> None:
    """The family has been sharing for about five months, not since this minute, so the family net worth has a history to
    draw: each share's amounts count from then, starting from what the account held on that day."""
    from expenses.family import catalog, households, summary

    since = today - timedelta(days=150)
    moment = timezone.make_aware(timezone.datetime(since.year, since.month, since.day, 12, 0))
    snapshot_day = since.replace(day=1) - timedelta(days=1)             # the last month-end before then
    for share in m.FamilyShare.objects.filter(household__owner=user).select_related('account'):
        start_value = None
        snap = m.NetWorthSnapshot.objects.filter(user_id=share.owner_id, as_of_date=snapshot_day).first()
        raw = snap.breakdown.get(str(share.account_id)) if snap else None
        if raw is not None and share.level == m.FamilyShare.AMOUNTS:
            start_value = summary._signed(catalog.kind_of(share.account.account_type), D(str(raw)))
        m.FamilyShare.objects.filter(pk=share.pk).update(amounts_since=moment, amounts_start_value=start_value)
    households.bump(m.Household.objects.get(owner=user))         # the family pages were cached before the dates moved


def seed_notifications(user) -> None:
    from django.urls import reverse
    items = (
        ('Your family shared something new', 'Arjun shared the HDFC Fixed Deposit with the family.', 'SYSTEM', reverse('family-home')),
        ('Insurance premium coming up', 'Star Health Family Floater is due in about three weeks.', 'SYSTEM', reverse('account-list')),
        ('Budget heads up', 'Dining Out is at 80% of its limit this month.', 'SYSTEM', reverse('budget')),
    )
    for title, message, kind, link in items:
        m.Notification.objects.create(user=user, title=title, message=message, notification_type=kind, link=link)


def mark_seeded(user) -> None:
    """The marker the demo checks to know it holds this version of the data. Written outside the sections, so a section
    that failed cannot make every visitor trigger a reseed."""
    m.Notification.objects.create(user=user, title='Welcome to the TrackMyRupee demo',
                                  message='This is sample data for a fictional family. Explore freely: nothing here is real.',
                                  notification_type='SYSTEM', slug=MARKER_SLUG, is_read=True)


def refresh_insights(user) -> None:
    from expenses.insights import engine, is_engine_on
    from types import SimpleNamespace as NS
    if not is_engine_on(NS(pk=user.pk)):
        return
    engine.refresh(user, costs=('light', 'heavy'), profile=user.profile)
