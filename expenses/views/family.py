import uuid
from datetime import timedelta
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _

from ..models import Family, FamilyInvitation, UserProfile

@login_required
def family_settings(request):
    user_profile = request.user.profile
    family = user_profile.family
    
    if request.method == 'POST':
        action = request.POST.get('action')
        
        if action == 'create_family':
            if family:
                messages.error(request, _('You are already part of a family.'))
                return redirect('family-settings')
            
            family_name = request.POST.get('family_name', '').strip()
            if not family_name:
                family_name = _("%(user)s's Family") % {'user': request.user.username}
                
            new_family = Family.objects.create(name=family_name, owner=request.user)
            user_profile.family = new_family
            user_profile.save()
            messages.success(request, _('Family created successfully.'))
            return redirect('family-settings')
            
        elif action == 'leave_family':
            if family:
                if family.owner == request.user:
                    messages.error(request, _('You cannot leave a family you own. Please disband it.'))
                else:
                    user_profile.family = None
                    user_profile.save()
                    messages.success(request, _('You have left the family.'))
            return redirect('family-settings')
            
        elif action == 'disband_family':
            if family and family.owner == request.user:
                family.delete()
                user_profile.family = None
                user_profile.save()
                messages.success(request, _('Family has been disbanded.'))
            return redirect('family-settings')
            
        elif action == 'remove_member':
            member_id = request.POST.get('member_id')
            if family and family.owner == request.user:
                member_profile = get_object_or_404(UserProfile, pk=member_id, family=family)
                if member_profile.user == request.user:
                    messages.error(request, _("You cannot remove yourself."))
                else:
                    member_profile.family = None
                    member_profile.save()
                    messages.success(request, _('Member removed.'))
            return redirect('family-settings')

    context = {
        'family': family,
        'invitations': family.invitations.filter(expires_at__gt=timezone.now(), is_redeemed=False) if family else [],
        'members': family.members.all().select_related('user') if family else [],
    }
    return render(request, 'expenses/family_settings.html', context)

@login_required
def family_invite(request):
    user_profile = request.user.profile
    family = user_profile.family
    
    if not family or family.owner != request.user:
        messages.error(request, _('You do not have permission to send invites.'))
        return redirect('family-settings')
        
    if not user_profile.can_add_family_member():
        messages.error(request, _('You have reached the maximum number of family members allowed on your current plan. Please upgrade to add more.'))
        return redirect('profile')
        
    if request.method == 'POST':
        email = request.POST.get('email', '').strip()
        if not email:
            messages.error(request, _('Email is required.'))
            return redirect('family-settings')
            
        active_invites = FamilyInvitation.objects.filter(
            family=family, email=email, is_redeemed=False, expires_at__gt=timezone.now()
        )
        if active_invites.exists():
            messages.warning(request, _('An active invite has already been sent to this email.'))
            return redirect('family-settings')
            
        invite = FamilyInvitation.objects.create(
            family=family,
            email=email,
            expires_at=timezone.now() + timedelta(days=7)
        )
        link = request.build_absolute_uri(reverse('family-join', kwargs={'code': invite.code}))
        messages.success(request, _('Invitation generated! Share this link: %(link)s') % {'link': link})
        
    return redirect('family-settings')

@login_required
def family_join(request, code):
    user_profile = request.user.profile
    if user_profile.family:
        messages.warning(request, _('You are already part of a family.'))
        return redirect('home')
        
    invite = get_object_or_404(FamilyInvitation, code=code)
    
    if invite.expires_at < timezone.now() or invite.is_redeemed:
        messages.error(request, _('This invitation is expired or has already been used.'))
        return redirect('home')
        
    owner_profile = invite.family.owner.profile
    if not owner_profile.can_add_family_member():
        messages.error(request, _('The family owner has reached their maximum limit of members.'))
        return redirect('home')
        
    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'accept':
            with transaction.atomic():
                user_profile.family = invite.family
                user_profile.save()
                
                invite.is_redeemed = True
                invite.save()
                
            messages.success(request, _('You have joined %(family)s!') % {'family': invite.family.name})
            return redirect('home')
        elif action == 'decline':
            invite.delete()
            messages.info(request, _('You declined the invitation.'))
            return redirect('home')
            
    context = {
        'invite': invite,
    }
    return render(request, 'expenses/family_join.html', context)
