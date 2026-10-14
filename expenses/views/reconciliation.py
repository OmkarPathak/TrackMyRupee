from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.shortcuts import redirect, render
from django.utils.translation import gettext as _
from django.views.decorators.http import require_http_methods, require_POST

from .. import reconciliation
from ..forms import AccountReconcileForm
from ..models import Account, AccountReconciliation
from .utils import get_object_by_uuid_or_pk

__all__ = [
    'account_reconcile_view',
    'account_reconcile_confirm_view',
    'account_reconciliation_history_view',
]

HISTORY_PAGE_SIZE = 20


def _reconcilable_account_or_redirect(request, pk):
    """The user's own active account, or the redirect that explains why it cannot be used.

    Mirrors the plan rules of the other account screens: accounts beyond the plan's limit stay
    locked until the user upgrades. Returns (account, None) or (None, response).
    """
    account = get_object_by_uuid_or_pk(Account.objects.all(), pk, user=request.user, is_active=True)
    if request.user.profile.is_account_locked(account):
        messages.error(request, _("This account is locked. Please upgrade your plan to reconcile it."))
        return None, redirect('pricing')
    if not reconciliation.is_reconcilable(account):
        messages.info(request, _("Only bank, cash, wallet and credit card accounts can be reconciled against a statement."))
        return None, redirect('account-detail', pk=account.uuid)
    return account, None


@login_required
@require_http_methods(['GET', 'POST'])
def account_reconcile_view(request, pk):
    account, early = _reconcilable_account_or_redirect(request, pk)
    if early:
        return early

    comparison = None
    if request.method == 'POST':
        form = AccountReconcileForm(request.POST)
        if form.is_valid():
            comparison = reconciliation.compare(
                account, form.cleaned_data['statement_date'], form.cleaned_data['statement_balance'],
            )
    else:
        form = AccountReconcileForm()

    last = AccountReconciliation.objects.filter(account=account).first()
    return render(request, 'expenses/account_reconcile.html', {
        'account': account,
        'form': form,
        'comparison': comparison,
        'note': form.cleaned_data.get('note', '') if comparison else '',
        'last_reconciliation': last,
        'is_demo': request.user.username == 'demo',
    })


@login_required
@require_POST
def account_reconcile_confirm_view(request, pk):
    account, early = _reconcilable_account_or_redirect(request, pk)
    if early:
        return early
    if request.user.username == 'demo':
        messages.warning(request, _("Reconciliation is disabled in the demo."))
        return redirect('account-detail', pk=account.uuid)

    form = AccountReconcileForm(request.POST)
    action = request.POST.get('action')
    if not form.is_valid() or action not in {'adjust', 'accept', 'open'}:
        messages.error(request, _("Those statement details are not valid. Please check them and try again."))
        return redirect('account-reconcile', pk=account.uuid)

    try:
        result = reconciliation.record(
            account,
            form.cleaned_data['statement_date'],
            form.cleaned_data['statement_balance'],
            action=action,
            note=form.cleaned_data['note'],
        )
    except reconciliation.ReconciliationError as exc:
        messages.error(request, str(exc))
        return redirect('account-reconcile', pk=account.uuid)

    if result.status == 'MATCHED':
        messages.success(request, _("The balance matches your statement. Nothing needed changing."))
    elif result.status == 'ADJUSTED':
        messages.success(request, _("Balance corrected to match your statement."))
    elif result.status == 'ACCEPTED':
        messages.success(request, _("Saved. Your app balance was kept as correct, and no balance was changed."))
    else:
        messages.info(request, _("Saved. The difference is still open, and your balance has not been changed."))
    return redirect('account-reconciliation-history', pk=account.uuid)


@login_required
def account_reconciliation_history_view(request, pk):
    account = get_object_by_uuid_or_pk(Account.objects.all(), pk, user=request.user)
    rows = AccountReconciliation.objects.filter(account=account, user=request.user).select_related('adjustment_entry')
    page = Paginator(rows, HISTORY_PAGE_SIZE).get_page(request.GET.get('page'))
    return render(request, 'expenses/account_reconciliation_history.html', {
        'account': account,
        'page_obj': page,
        'can_reconcile': (
            account.is_active
            and reconciliation.is_reconcilable(account)
            and not request.user.profile.is_account_locked(account)
        ),
    })
