from functools import wraps

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _
from django.utils.translation import ngettext
from django.views.decorators.http import require_GET, require_POST

from ..family import (
    catalog,
    emergency,
    family_enabled_for,
    households,
    notes,
    shares,
    summary,
)
from ..family import plan as plan_rules
from ..family.emails import send_invitation_email
from ..family.presenters import (
    avatar_tone,
    chart,
    display_name,
    initials,
    member_rows,
    plan_banner,
)
from ..models import (
    Account,
    EmergencyRequest,
    EmergencySheet,
    FamilyShare,
    HouseholdInvitation,
    HouseholdMember,
)

__all__ = [
    'family_home_view',
    'family_create_view',
    'family_invite_view',
    'family_invite_cancel_view',
    'family_invite_renew_view',
    'family_join_view',
    'family_leave_view',
    'family_member_remove_view',
    'family_share_view',
    'family_share_edit_view',
    'family_share_toggle_view',
    'family_share_group_view',
    'family_share_stop_view',
    'family_share_notify_view',
    'family_preview_view',
    'family_networth_view',
    'family_inventory_view',
    'family_note_dismiss_view',
    'family_notes_toggle_view',
    'family_emergency_view',
    'family_emergency_save_view',
    'family_emergency_sheet_view',
    'family_emergency_ask_view',
    'family_emergency_cancel_view',
    'family_emergency_decide_view',
    'family_emergency_extend_view',
]

SESSION_NEW_INVITE = 'family_new_invite'


def family_view(view):
    """Signed-in users only, and only where family sharing is switched on (otherwise the page does not exist)."""
    @login_required
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not family_enabled_for(request.user):
            raise Http404
        return view(request, *args, **kwargs)
    return wrapped


def _owned_household_or_404(request):
    household = households.household_for(request.user)
    if household is None or household.owner_id != request.user.id:
        raise Http404
    return household


def _remember_invite(request, invitation, token, emailed):
    link = request.build_absolute_uri(reverse('family-join', args=[token]))
    request.session[SESSION_NEW_INVITE] = {
        'link': link, 'email': invitation.email, 'emailed': emailed,
    }
    return link


@family_view
def family_home_view(request):
    household = households.household_for(request.user)
    profile = request.user.profile
    context = {'household': household, 'can_create': profile.is_pro, 'max_members': 0}
    if household is None:
        return render(request, 'expenses/family/home.html', context)

    rows, state = member_rows(household, request.user)
    is_owner = household.owner_id == request.user.id
    open_invites = list(household.invitations.filter(
        accepted_at__isnull=True, cancelled_at__isnull=True, expires_at__gt=timezone.now())) if is_owner else []
    context.update({
        'rows': rows,
        'state': state,
        'banner': plan_banner(state, rows, request.user),
        'is_owner': is_owner,
        'pending': open_invites,
        'new_invite': request.session.pop(SESSION_NEW_INVITE, None),
        'max_members': settings.FAMILY_MAX_MEMBERS,
        'seats_used': len(rows) + len(open_invites),
        'family_page': 'home',
    })
    return render(request, 'expenses/family/home.html', context)


@family_view
@require_POST
def family_create_view(request):
    try:
        households.create_household(request.user, request.POST.get('name', ''))
    except households.FamilyError as error:
        messages.error(request, error.message)
        return redirect('family-home')
    messages.success(request, _("Your family is ready. Invite the people you want to share with."))
    return redirect('family-home')


@family_view
@require_POST
def family_invite_view(request):
    household = _owned_household_or_404(request)
    email = request.POST.get('email', '').strip()
    if email:
        try:
            validate_email(email)
        except ValidationError:
            messages.error(request, _("That email address does not look right."))
            return redirect('family-home')
    try:
        invitation, token = households.invite(household, request.user, email)
    except households.FamilyError as error:
        messages.error(request, error.message)
        return redirect('family-home')
    link = request.build_absolute_uri(reverse('family-join', args=[token]))
    emailed = send_invitation_email(invitation, link)
    _remember_invite(request, invitation, token, emailed)
    messages.success(
        request,
        _("Invitation sent to %(email)s.") % {'email': invitation.email} if emailed
        else _("Invitation link created. Copy it and send it to them."))
    return redirect('family-home')


@family_view
@require_POST
def family_invite_cancel_view(request, pk):
    household = _owned_household_or_404(request)
    invitation = get_object_or_404(HouseholdInvitation, uuid=pk, household=household)
    households.cancel_invitation(invitation, request.user)
    messages.success(request, _("Invitation cancelled."))
    return redirect('family-home')


@family_view
@require_POST
def family_invite_renew_view(request, pk):
    household = _owned_household_or_404(request)
    invitation = get_object_or_404(HouseholdInvitation, uuid=pk, household=household)
    try:
        fresh, token = households.renew_invitation(invitation, request.user)
    except households.FamilyError as error:
        messages.error(request, error.message)
        return redirect('family-home')
    link = request.build_absolute_uri(reverse('family-join', args=[token]))
    emailed = send_invitation_email(fresh, link) if fresh.email else False
    _remember_invite(request, fresh, token, emailed)
    messages.success(request, _("A new invitation link is ready."))
    return redirect('family-home')


@family_view
def family_join_view(request, token):
    invitation = households.find_invitation(token)
    problem = households.invitation_problem(invitation)
    profile = request.user.profile
    already = households.household_for(request.user)
    context = {
        'invitation': invitation,
        'problem': problem,
        'inviter_name': display_name(invitation.invited_by) if invitation else '',
        'household': invitation.household if invitation else None,
        'is_pro': profile.is_pro,
        'can_trial': profile.can_start_trial,
        'already_member': already is not None,
        'same_household': bool(already and invitation and already.pk == invitation.household_id),
    }
    if request.method == 'POST' and not problem and profile.is_pro and already is None:
        if request.POST.get('consent') != 'on':
            messages.error(request, _("Please tick the box to confirm you understand what joining means."))
            return render(request, 'expenses/family/join.html', context)
        try:
            households.accept_invitation(invitation, request.user, request)
        except households.FamilyError as error:
            messages.error(request, error.message)
            return render(request, 'expenses/family/join.html', context)
        messages.success(request, _("You have joined the family. Nothing is shared until you choose."))
        return redirect('family-home')
    return render(request, 'expenses/family/join.html', context)


@family_view
@require_POST
def family_leave_view(request):
    try:
        households.leave(request.user)
    except households.FamilyError as error:
        messages.error(request, error.message)
    else:
        messages.success(request, _("You have left the family. Nothing of yours is shared any more."))
    return redirect('family-home')


@family_view
@require_POST
def family_member_remove_view(request, pk):
    household = _owned_household_or_404(request)
    membership = get_object_or_404(HouseholdMember, pk=pk, household=household)
    try:
        households.remove_member(request.user, membership)
    except households.FamilyError as error:
        messages.error(request, error.message)
    else:
        messages.success(request, _("Removed from the family."))
    return redirect('family-home')


# ---- What I share ---------------------------------------------------------------------------------------------

def _household_or_redirect(request):
    household = households.household_for(request.user)
    if household is None:
        messages.info(request, _("Create or join a family first, then choose what to share."))
        return None
    return household


def _own_account_or_404(request, pk):
    return get_object_or_404(Account, uuid=pk, user=request.user, is_active=True)


def _group_rows(rows_by_section):
    """[(key, heading, rows)] for the sections that have rows, in the page's order."""
    return [(key, label, rows_by_section[key]) for key, label, _kinds in catalog.SECTIONS if rows_by_section.get(key)]


@family_view
def family_share_view(request):
    household = _household_or_redirect(request)
    if household is None:
        return redirect('family-home')
    items = summary.own_items(request.user)
    shared = {s.account_id: s for s in FamilyShare.objects.filter(owner=request.user)}
    by_section = {}
    for account in shares.shareable_accounts(request.user):
        item = items[account.pk]
        by_section.setdefault(item.section, []).append({
            'account': account, 'item': item, 'share': shared.get(account.pk)})
    count_shared, count_total = shares.counts(request.user)
    membership = HouseholdMember.objects.get(user=request.user)
    groups = []
    for key, label, rows in _group_rows(by_section):
        on = sum(1 for r in rows if r['share'])
        groups.append((key, label, rows, {'on': on, 'total': len(rows),
                                          'state': 'true' if on == len(rows) else 'mixed' if on else 'false'}))
    return render(request, 'expenses/family/share.html', {
        'household': household,
        'groups': groups,
        'count_shared': count_shared,
        'count_total': count_total,
        'events': shares.recent_events(request.user),
        'notify': membership.notify_new_shares,
        'others': household.members.exclude(user=request.user).count(),
        'family_page': 'share',
    })


@family_view
def family_share_edit_view(request, pk):
    household = _household_or_redirect(request)
    if household is None:
        return redirect('family-home')
    account = _own_account_or_404(request, pk)
    share = FamilyShare.objects.filter(owner=request.user, account=account).first()
    if request.method == 'POST':
        try:
            _share, created = shares.set_share(
                request.user, account, request.POST.get('level', ''), request.POST.get('alias', ''),
                covers=request.POST.get('covers') if account.linked_physical_asset_id else None)
        except households.FamilyError as error:
            messages.error(request, error.message)
        else:
            messages.success(request, _("Now shared with your family.") if created else _("Sharing saved."))
            return redirect('family-share')
    item = summary.own_items(request.user)[account.pk]
    members = [display_name(m.user) for m in household.members.exclude(user=request.user).select_related('user')]
    return render(request, 'expenses/family/share_edit.html', {
        'household': household,
        'account': account,
        'item': item,
        'share': share,
        'level': share.level if share else FamilyShare.AMOUNTS,
        'alias': share.alias if share else '',
        'policy': account.linked_physical_asset,
        'member_names': [display_name(request.user)] + members,
        'others': members,
        'family_page': 'share',
    })


@family_view
@require_POST
def family_share_toggle_view(request, pk):
    household = _household_or_redirect(request)
    if household is None:
        return redirect('family-home')
    account = _own_account_or_404(request, pk)
    if FamilyShare.objects.filter(owner=request.user, account=account).exists():
        shares.stop_share(request.user, account)
        messages.success(request, _("Stopped sharing %(name)s.") % {'name': account.name})
    else:
        try:
            shares.set_share(request.user, account, FamilyShare.AMOUNTS)
        except households.FamilyError as error:
            messages.error(request, error.message)
        else:
            messages.success(request, _("%(name)s is now shared with your family.") % {'name': account.name})
    return redirect('family-share')


@family_view
@require_POST
def family_share_group_view(request, key):
    """Share (or stop sharing) every account in one group of the What I share page at once."""
    household = _household_or_redirect(request)
    if household is None:
        return redirect('family-home')
    label = next((label for k, label, _kinds in catalog.SECTIONS if k == key), None)
    if label is None:
        raise Http404
    accounts = [a for a in shares.shareable_accounts(request.user)
                if catalog.section_of(catalog.kind_of(a.account_type)) == key]
    try:
        changed, now_shared = shares.set_section(request.user, accounts, str(label))
    except households.FamilyError as error:
        messages.error(request, error.message)
    else:
        if now_shared:
            messages.success(request, ngettext("Shared %(count)d item in %(group)s with your family.",
                                               "Shared %(count)d items in %(group)s with your family.", changed)
                             % {'count': changed, 'group': label})
        else:
            messages.success(request, ngettext("Stopped sharing %(count)d item in %(group)s.",
                                               "Stopped sharing %(count)d items in %(group)s.", changed)
                             % {'count': changed, 'group': label})
    return redirect('family-share')


@family_view
@require_POST
def family_share_stop_view(request, pk):
    account = _own_account_or_404(request, pk)
    if shares.stop_share(request.user, account):
        messages.success(request, _("Stopped sharing %(name)s.") % {'name': account.name})
    return redirect('family-share')


@family_view
@require_POST
def family_share_notify_view(request):
    membership = get_object_or_404(HouseholdMember, user=request.user)
    membership.notify_new_shares = not membership.notify_new_shares
    membership.save(update_fields=['notify_new_shares'])
    return redirect('family-share')


@family_view
def family_preview_view(request, pk=None):
    """What a chosen member sees of the signed-in user's shared items, built by the same code they see it from."""
    household = _household_or_redirect(request)
    if household is None:
        return redirect('family-home')
    rows, _state = member_rows(household, request.user)
    others = [r for r in rows if not r['is_you']]
    subject = None
    if pk is not None:
        subject = next((r for r in others if r['membership'].pk == pk), None)
        if subject is None:
            raise Http404
    elif others:
        subject = others[0]
    items = summary.shared_items(request.user, household)
    by_section = {}
    for item in items:
        by_section.setdefault(item.section, []).append(item)
    private = request.user.accounts.filter(is_active=True, family_share__isnull=True).order_by('name', 'id')
    listed_only = [i for i in items if not i.show_amounts]
    return render(request, 'expenses/family/preview.html', {
        'household': household,
        'subject': subject,
        'others': others,
        'groups': _group_rows(by_section),
        'item_count': len(items),
        'owner_tone': avatar_tone(next(i for i, r in enumerate(rows) if r['is_you'])),
        'owner_initials': initials(request.user),
        'private_names': [a.name for a in private],
        'listed_only': [i.name for i in listed_only],
        'family_page': 'share',
    })


# ---- The family's combined view ---------------------------------------------------------------------------------

def _family_context(request):
    """(household, banner, summary) for a family page. ``summary`` is None while family view is paused (some member
    is no longer on Pro): nothing is read, so nothing is shown. None for the household if there is no family."""
    household = households.household_for(request.user)
    if household is None:
        return None, None, None
    rows, state = member_rows(household, request.user)
    banner = plan_banner(state, rows, request.user)
    if state.state == plan_rules.PAUSED:
        return household, banner, None
    return household, banner, summary.family_summary(household, request.user)


@family_view
@require_GET
def family_networth_view(request):
    household, banner, data = _family_context(request)
    if household is None:
        messages.info(request, _("Create or join a family first to see its combined net worth."))
        return redirect('family-home')
    months = 3 if request.GET.get('range') == '3m' else None
    graph = None
    if data is not None:
        graph = chart(data.history, data.net_worth, timezone.localdate(), months, start=data.since)
    return render(request, 'expenses/family/networth.html', {
        'household': household, 'banner': banner, 'data': data, 'graph': graph,
        'range': '3m' if months else 'all', 'family_page': 'networth',
    })


@family_view
@require_GET
def family_inventory_view(request):
    household, banner, data = _family_context(request)
    if household is None:
        messages.info(request, _("Create or join a family first to see what it holds."))
        return redirect('family-home')
    sections = {}
    if data is not None:
        style = {m.user_id: m for m in data.members}
        for item in data.items:
            sections.setdefault(item.kind, []).append({'item': item, 'member': style[item.owner_id]})
    cards = sections.get(catalog.CARD, [])
    card_totals = None
    shown = [r['item'] for r in cards if r['item'].show_amounts and r['item'].limit is not None]
    if shown:
        used = sum((-i.amount for i in shown), summary.ZERO)
        limit = sum((i.limit for i in shown), summary.ZERO)
        card_totals = {'used': used, 'limit': limit, 'available': limit - used,
                       'percent': int(round(used / limit * 100)) if limit else 0,
                       'currency': shown[0].currency}
    policies = sections.get(catalog.INSURANCE, [])
    cover_by_type, yearly = {}, summary.ZERO
    for row in policies:
        item = row['item']
        if item.cover:
            cover_by_type[item.type_label] = cover_by_type.get(item.type_label, summary.ZERO) + item.cover
        if item.premium_yearly:
            yearly += item.premium_yearly
    gentle, gentle_on = notes.notes_for(request.user, household, data) if data else ([], True)
    return render(request, 'expenses/family/inventory.html', {
        'household': household, 'banner': banner, 'data': data,
        'gentle_notes': gentle, 'gentle_on': gentle_on,
        'cards': cards, 'card_totals': card_totals,
        'policies': policies, 'cover_by_type': list(cover_by_type.items()), 'premium_yearly': yearly,
        'banks': sections.get(catalog.BANK, []) + sections.get(catalog.INVESTMENT, []),
        'loans_property': sections.get(catalog.LOAN, []) + sections.get(catalog.PROPERTY, []),
        'account_count': len(sections.get(catalog.BANK, [])) + len(sections.get(catalog.INVESTMENT, []))
                         + len(sections.get(catalog.LOAN, [])) + len(sections.get(catalog.PROPERTY, [])),
        'currency': data.currency if data else '',
        'family_page': 'inventory',
    })


@family_view
@require_POST
def family_note_dismiss_view(request):
    household, _banner, data = _family_context(request)
    if household is not None and data is not None:
        notes.dismiss(request.user, household, data, request.POST.get('key', ''))
    return redirect('family-inventory')


@family_view
@require_POST
def family_notes_toggle_view(request):
    household = households.household_for(request.user)
    if household is not None:
        notes.set_enabled(request.user, household, request.POST.get('on') == '1')
    return redirect('family-inventory')


# ---- Emergency access ---------------------------------------------------------------------------------------------

def _days_hours(request):
    days, hours = emergency.time_left(request)
    return {'days': days, 'hours': hours}


@family_view
def family_emergency_view(request):
    household = _household_or_redirect(request)
    if household is None:
        return redirect('family-home')
    emergency.notify_due_releases(request.user)
    emergency.notify_expiring_access(request.user)
    sheet = emergency.sheet_of(request.user)
    items = summary.own_items(request.user)
    on_sheet = {i.account_id: i.nominee for i in sheet.items.all()} if sheet else {}
    by_section = {}
    for account in shares.shareable_accounts(request.user):
        item = items[account.pk]
        by_section.setdefault(item.section, []).append({
            'account': account, 'item': item, 'on': account.pk in on_sheet, 'nominee': on_sheet.get(account.pk, ''),
            'liability': item.kind in (catalog.CARD, catalog.LOAN)})
    rows, _state = member_rows(household, request.user)
    allowed = set(sheet.allowed.values_list('user_id', flat=True)) if sheet else set()
    others = [dict(r, allowed=r['user'].pk in allowed) for r in rows if not r['is_you']]
    pending = [{'request': r, 'left': _days_hours(r), 'name': display_name(r.requester)}
               for r in emergency.open_requests(sheet)] if sheet else []
    access = [{'request': r, 'name': display_name(r.requester)} for r in emergency.people_with_access(sheet)] \
        if sheet and sheet.mode == EmergencySheet.REQUEST else []
    # sheets other members have shared with you
    available = []
    for row in others:
        their = emergency.sheet_of(row['user'])
        state, req = emergency.state_for(request.user, their) if their else (emergency.NONE, None)
        if state != emergency.NONE:
            available.append({'row': row, 'state': state, 'request': req,
                              'left': _days_hours(req) if state == emergency.PENDING else None})
    return render(request, 'expenses/family/emergency.html', {
        'household': household, 'sheet': sheet, 'groups': _group_rows(by_section), 'others': others,
        'pending': pending, 'access': access, 'available': available,
        'mode': sheet.mode if sheet else EmergencySheet.REQUEST,
        'access_days': sheet.access_days if sheet else None,
        'access_choices': emergency.ACCESS_CHOICES,
        'use_nicknames': sheet.use_nicknames if sheet else False,
        'delay_days': settings.FAMILY_EMERGENCY_DELAY_DAYS,
        'family_page': 'emergency',
    })


@family_view
@require_POST
def family_emergency_save_view(request):
    household = _household_or_redirect(request)
    if household is None:
        return redirect('family-home')
    post = request.POST
    included = {}
    for account in request.user.accounts.filter(is_active=True):
        if post.get(f'include_{account.uuid}') == 'on':
            included[account.pk] = post.get(f'nominee_{account.uuid}', '')
    allowed = [m.user_id for m in household.members.exclude(user=request.user) if post.get(f'ask_{m.pk}') == 'on']
    try:
        emergency.save_sheet(request.user, post.get('mode', ''), included, allowed,
                             post.get('documents', ''), post.get('contacts', ''),
                             access_days=post.get('access_days', ''), use_nicknames=post.get('use_nicknames') == 'on')
    except households.FamilyError as error:
        messages.error(request, error.message)
    else:
        messages.success(request, _("Your emergency sheet is saved."))
    return redirect('family-emergency')


@family_view
def family_emergency_sheet_view(request, pk):
    """A sheet: the owner previews their own; someone else sees it only if the rules in emergency.state_for allow."""
    household = _household_or_redirect(request)
    if household is None:
        return redirect('family-home')
    membership = get_object_or_404(HouseholdMember, pk=pk, household=household)
    owner = membership.user
    sheet = emergency.sheet_of(owner)
    context = {'household': household, 'owner_name': display_name(owner), 'membership': membership,
               'family_page': 'emergency', 'print': request.GET.get('print') == '1'}
    if owner.pk == request.user.pk:
        if sheet is None:
            return redirect('family-emergency')
        context.update(state=emergency.GRANTED, content=emergency.sheet_content(sheet), preview=True)
        return render(request, 'expenses/family/emergency_sheet.html', context)
    state, req = emergency.state_for(request.user, sheet)
    if state == emergency.NONE:
        raise Http404
    context.update(state=state, request_obj=req, delay_days=settings.FAMILY_EMERGENCY_DELAY_DAYS,
                   mode=sheet.mode)
    if state == emergency.PENDING:
        context['left'] = _days_hours(req)
    if state == emergency.GRANTED:
        context['content'] = emergency.sheet_content(sheet)
    return render(request, 'expenses/family/emergency_sheet.html', context)


@family_view
@require_POST
def family_emergency_ask_view(request, pk):
    household = _household_or_redirect(request)
    if household is None:
        return redirect('family-home')
    membership = get_object_or_404(HouseholdMember, pk=pk, household=household)
    sheet = emergency.sheet_of(membership.user)
    if sheet is None or emergency.state_for(request.user, sheet)[0] == emergency.NONE:
        raise Http404
    try:
        emergency.ask(request.user, sheet)
    except households.FamilyError as error:
        messages.error(request, error.message)
    else:
        messages.success(request, _("Request sent. They have been told."))
    return redirect('family-emergency-sheet', pk=pk)


@family_view
@require_POST
def family_emergency_cancel_view(request, pk):
    req = get_object_or_404(EmergencyRequest, uuid=pk, requester=request.user)
    try:
        emergency.cancel(request.user, req)
    except households.FamilyError as error:
        messages.error(request, error.message)
    else:
        messages.success(request, _("Request cancelled."))
    owner_membership = get_object_or_404(HouseholdMember, user=req.sheet.owner)
    return redirect('family-emergency-sheet', pk=owner_membership.pk)


@family_view
@require_POST
def family_emergency_decide_view(request, pk, decision):
    req = get_object_or_404(EmergencyRequest, uuid=pk, sheet__owner=request.user)
    try:
        emergency.decide(request.user, req, approve=decision == 'approve')
    except households.FamilyError as error:
        messages.error(request, error.message)
    else:
        messages.success(request, _("Approved.") if decision == 'approve' else _("Access withdrawn."))
    return redirect('family-emergency')


@family_view
@require_POST
def family_emergency_extend_view(request, pk):
    req = get_object_or_404(EmergencyRequest, uuid=pk, sheet__owner=request.user)
    try:
        emergency.extend(request.user, req)
    except households.FamilyError as error:
        messages.error(request, error.message)
    else:
        messages.success(request, _("Access extended."))
    return redirect('family-emergency')
