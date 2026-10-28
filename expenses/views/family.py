from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from ..family import family_enabled_for, households
from ..family.emails import send_invitation_email
from ..family.presenters import display_name, member_rows, plan_banner
from ..models import HouseholdInvitation, HouseholdMember

__all__ = [
    'family_home_view',
    'family_create_view',
    'family_invite_view',
    'family_invite_cancel_view',
    'family_invite_renew_view',
    'family_join_view',
    'family_leave_view',
    'family_member_remove_view',
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

    from django.conf import settings
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
