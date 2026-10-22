"""Loan rules. They describe where a loan stands and what one option would change; they never suggest a lender, a
product or a rate to chase. Loans are a sensitive subject: the heavier ones are marked sensitive (extra gentle, no
share, never pushed). Loan maths is the loan service's, run on loans the context has already totalled."""
from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from django.urls import reverse
from django.utils.translation import gettext as _
from django.utils.translation import ngettext

from ...services import LoanService
from .. import sampling
from ..format import Fmt
from ..registry import rule
from ..types import Action, Evidence, Insight, Kind, Module, Plan, Surface, Tone

D = Decimal
MIN_MONTHS_SAVED = 3            # an extra EMI that saves fewer months than this is not worth a mention
RATE_GAP = D('1.0')             # two loans this many points apart are worth putting side by side
MILESTONES = (90, 75, 50, 25)   # share of the principal repaid
MILESTONE_WINDOW = 10           # shown while the share is within this many points past a milestone
FINAL_YEAR_MONTHS = 12
RATE_CHANGE_DAYS = 30


def _months_left(loan, today) -> int:
    passed = max(0, (today.year - loan.start_date.year) * 12 + today.month - loan.start_date.month)
    return loan.duration_months - passed


def _remaining(loan) -> Decimal:
    return D(str(loan._cached_summary['remaining_principal']))


def _loan_link(loan) -> str:
    return reverse('loan-detail', args=[loan.uuid])


@rule(id='loans.interest_paid_year', module=Module.LOANS, surfaces={Surface.DASHBOARD, Surface.MODULE, Surface.MONTHLY, Surface.YEAR_IN_REVIEW},
      needs={'loan_paid'}, cooldown_days=90, min_plan=Plan.PLUS, requires={'loans'}, sample=sampling.loans_sample)
def interest_paid_year(ctx) -> list[Insight]:
    """Interest and principal paid on loans so far this financial year (calendar year outside India)."""
    paid = ctx.loan_paid
    if paid.repayments < 1 or paid.interest <= 0:
        return []
    fmt, link = ctx.fmt, reverse('loan-list')
    first, last = ctx.financial_year
    args = {'interest': fmt.approx(paid.interest), 'principal': fmt.approx(paid.principal)}
    message = (_('So far this financial year, about %(interest)s of your loan payments went to interest and '
                 '%(principal)s to principal.') if fmt.indian else
               _('So far this year, about %(interest)s of your loan payments went to interest and '
                 '%(principal)s to principal.')) % args
    return [Insight(
        rule_id='loans.interest_paid_year', module=Module.LOANS, kind=Kind.OBSERVATION, tone=Tone.CALM,
        priority=32, icon='bank', sensitive=True,
        title=_('Interest on your loans this year'), message=message,
        evidence=(Evidence(_('Interest'), fmt.money(paid.interest), link), Evidence(_('Principal'), fmt.money(paid.principal))),
        action=Action(_('View your loans'), link),
        dedupe_key=f'{first.year}:{ctx.key:%Y-%m}', valid_until=ctx.cycle[1],
        surfaces=frozenset({Surface.DASHBOARD, Surface.MODULE, Surface.MONTHLY, Surface.YEAR_IN_REVIEW}),
        numbers={'interest': str(paid.interest.quantize(D('0.01'))), 'principal': str(paid.principal.quantize(D('0.01')))},
    )]


@rule(id='loans.extra_emi_effect', module=Module.LOANS, surfaces={Surface.DASHBOARD, Surface.MODULE, Surface.MONTHLY}, needs={'loans'},
      cooldown_days=90, min_plan=Plan.PLUS, requires={'loans'}, sample=sampling.loans_sample)
def extra_emi_effect(ctx) -> list[Insight]:
    """What one extra EMI a year, all of it against the principal, would change for an amortising loan: the loan
    service's own estimate (the same one the loan page shows)."""
    best = None
    for loan in ctx.loans:
        if loan.repayment_type != 'EMI' or D(str(loan.paid_principal)) <= 0:
            continue
        result = LoanService.calculate_extra_emi_savings(loan)
        if result and result['months_saved'] >= MIN_MONTHS_SAVED and result['interest_saved'] > 0:
            if best is None or result['interest_saved'] > best[1]['interest_saved']:
                best = (loan, result)
    if best is None:
        return []
    loan, result = best
    own, months = Fmt(loan.currency), result['months_saved']
    args = {'loan': loan.name, 'amount': own.approx(result['interest_saved']), 'n': ctx.fmt.number(months),
            'years': ctx.fmt.number(f"{result['years_saved']:g}")}
    message = (_('One extra EMI a year on %(loan)s could close it about %(years)s years earlier and save around '
                 '%(amount)s in interest. It is only an estimate, and entirely optional.') if months >= 24 else
               _('One extra EMI a year on %(loan)s could close it about %(n)s months earlier and save around '
                 '%(amount)s in interest. It is only an estimate, and entirely optional.')) % args
    link = _loan_link(loan)
    return [Insight(
        rule_id='loans.extra_emi_effect', module=Module.LOANS, kind=Kind.OPPORTUNITY, tone=Tone.CALM,
        priority=30, icon='calculator', sensitive=True,
        title=_('What one extra EMI could do'), message=message,
        evidence=(Evidence(_('Your EMI'), own.money(result['emi']), link),
                  Evidence(_('Interest saved'), own.money(result['interest_saved']))),
        action=Action(_('See the full picture'), link),
        dedupe_key=f'{loan.uuid}:{ctx.today.year}-Q{(ctx.today.month - 1) // 3 + 1}', valid_until=ctx.cycle[1],
        surfaces=frozenset({Surface.DASHBOARD, Surface.MODULE, Surface.MONTHLY}),
        numbers={'loan': loan.name.strip().lower(), 'months_saved': months},
    )]


@rule(id='loans.rate_gap', module=Module.LOANS, surfaces={Surface.MODULE}, needs={'loans'}, cooldown_days=120,
      min_plan=Plan.PRO, requires={'loans'}, sample=sampling.loans_sample)
def rate_gap(ctx) -> list[Insight]:
    """Two active loans whose current rates are a point or more apart, side by side. A comparison, nothing more."""
    rated = sorted(((LoanService.current_rate(loan, ctx.today), loan) for loan in ctx.loans), key=lambda t: t[0])
    if len(rated) < 2 or rated[-1][0] - rated[0][0] < RATE_GAP:
        return []
    (low_rate, low), (high_rate, high) = rated[0], rated[-1]
    fmt, link = ctx.fmt, reverse('loan-list')
    return [Insight(
        rule_id='loans.rate_gap', module=Module.LOANS, kind=Kind.OBSERVATION, tone=Tone.CALM,
        priority=26, icon='percent', sensitive=True,
        title=_('Your loan rates differ'),
        message=_('%(high)s is at %(high_rate)s, about %(gap)s points above %(low)s at %(low_rate)s. '
                  'This is a comparison only, for whenever you next look at your loans.') % {
            'high': high.name, 'high_rate': fmt.rate(high_rate), 'low': low.name, 'low_rate': fmt.rate(low_rate),
            'gap': fmt.number(f'{high_rate - low_rate:.1f}')},
        evidence=(Evidence(high.name, fmt.rate(high_rate), _loan_link(high)), Evidence(low.name, fmt.rate(low_rate), _loan_link(low))),
        action=Action(_('View your loans'), link),
        dedupe_key=f'{high.uuid}:{low.uuid}:{ctx.today.year}-H{(ctx.today.month - 1) // 6 + 1}',
        valid_until=ctx.cycle[1], surfaces=frozenset({Surface.MODULE}),
        numbers={'high': high.name.strip().lower(), 'low': low.name.strip().lower()},
    )]


@rule(id='loans.milestone_ahead', module=Module.LOANS, surfaces={Surface.DASHBOARD, Surface.MODULE}, needs={'loans'},
      cooldown_days=30, min_plan=Plan.PLUS, requires={'loans'}, sample=sampling.loans_sample)
def milestone_ahead(ctx) -> list[Insight]:
    """For each loan, at most one thing worth knowing, in this order: a rate change the person recorded that takes
    effect within 30 days, the final year of the term, or a share of the principal (25, 50, 75, 90%) just repaid."""
    out = []
    fmt = ctx.fmt
    for loan in ctx.loans:
        own, link, remaining = Fmt(loan.currency), _loan_link(loan), _remaining(loan)
        upcoming = sorted((r for r in loan.interest_rates.all()
                           if ctx.today < r.effective_date <= ctx.today + timedelta(days=RATE_CHANGE_DAYS)),
                          key=lambda r: r.effective_date)
        left = _months_left(loan, ctx.today)
        initial = D(str(loan.initial_principal))
        repaid = (initial - remaining) / initial * 100 if initial > 0 else D(0)
        if upcoming:
            change = upcoming[0]
            out.append(Insight(
                rule_id='loans.milestone_ahead', module=Module.LOANS, kind=Kind.UPCOMING, tone=Tone.CALM, priority=48,
                icon='calendar-event',
                title=_('%(loan)s rate changes soon') % {'loan': fmt.short(loan.name)},
                message=_('A rate change you recorded for %(loan)s takes effect on %(date)s, from %(old)s to %(new)s. '
                          'Your payments will follow the new rate.') % {
                    'loan': loan.name, 'date': fmt.day(change.effective_date),
                    'old': fmt.rate(LoanService.current_rate(loan, ctx.today)), 'new': fmt.rate(change.interest_rate)},
                evidence=(Evidence(_('Takes effect on'), fmt.day(change.effective_date), link),),
                action=Action(_('View loan'), link),
                dedupe_key=f'rate:{loan.uuid}:{change.effective_date:%Y-%m-%d}', valid_until=change.effective_date,
                surfaces=frozenset({Surface.DASHBOARD, Surface.MODULE}),
                numbers={'loan': loan.name.strip().lower(), 'kind': 'rate'}))
        elif 0 < left <= FINAL_YEAR_MONTHS and remaining > 0:
            out.append(Insight(
                rule_id='loans.milestone_ahead', module=Module.LOANS, kind=Kind.PROGRESS, tone=Tone.POSITIVE, priority=42,
                icon='flag',
                title=_('%(loan)s is in its final year') % {'loan': fmt.short(loan.name)},
                message=ngettext(
                    '%(loan)s has about %(n)s month left on its schedule, with %(amount)s still to repay. '
                    'A steady finish is in sight.',
                    '%(loan)s has about %(n)s months left on its schedule, with %(amount)s still to repay. '
                    'A steady finish is in sight.', left) % {
                    'loan': loan.name, 'n': fmt.number(left), 'amount': own.approx(remaining)},
                evidence=(Evidence(_('Still to repay'), own.money(remaining), link),),
                action=Action(_('View loan'), link),
                dedupe_key=f'last-year:{loan.uuid}', valid_until=ctx.today + timedelta(days=31 * left),
                surfaces=frozenset({Surface.DASHBOARD, Surface.MODULE}),
                numbers={'loan': loan.name.strip().lower(), 'kind': 'final-year', 'months': left}))
        else:
            reached = next((m for m in MILESTONES if m <= repaid < m + MILESTONE_WINDOW), None)
            if reached is None:
                continue
            out.append(Insight(
                rule_id='loans.milestone_ahead', module=Module.LOANS, kind=Kind.MILESTONE, tone=Tone.POSITIVE, priority=40,
                icon='trophy',
                title=_('%(loan)s is %(pct)s repaid') % {'loan': fmt.short(loan.name), 'pct': fmt.percent(reached)},
                message=_('You have repaid about %(pct)s of %(loan)s, with about %(amount)s still to go. '
                          'Steady payments got you here.') % {
                    'pct': fmt.percent(repaid), 'loan': loan.name, 'amount': own.approx(remaining)},
                evidence=(Evidence(_('Still to repay'), own.money(remaining), link),),
                action=Action(_('View loan'), link),
                dedupe_key=f'pct{reached}:{loan.uuid}', valid_until=ctx.today + timedelta(days=60),
                surfaces=frozenset({Surface.DASHBOARD, Surface.MODULE}),
                numbers={'loan': loan.name.strip().lower(), 'kind': 'pct', 'milestone': reached}))
    return out[:2]
