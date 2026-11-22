"""Accounts > Summary report: the investments and liabilities summary, a page made to be printed or saved as a PDF."""
from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.views.decorators.http import require_GET

from ..portfolio_summary import build_portfolio_summary
from ..posthog_utils import ph_capture


@login_required
@require_GET
def portfolio_summary(request):
    summary = build_portfolio_summary(request.user)
    ph_capture(request.user, 'portfolio_summary_viewed', {
        'investment_blocks': len(summary['blocks']), 'has_liabilities': bool(summary['liability_rows']),
        'has_sips': bool(summary['sip_rows']),
    })
    response = render(request, 'expenses/portfolio_summary.html', {
        'summary': summary,
        'currency': request.user.profile.currency,
    })
    response['Cache-Control'] = 'private, no-store'
    return response
