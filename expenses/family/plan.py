"""The Pro rule: family view needs every member on Pro.

A warning shows from FAMILY_PRO_WARNING_DAYS before any member's Pro ends. When a member's Pro ends, family
view pauses for everyone. Nothing is deleted: shares, members and each person's own data are untouched, and
family view resumes by itself once every member is on Pro again.
"""
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from django.conf import settings
from django.utils import timezone

ON, WARNING, PAUSED = 'on', 'warning', 'paused'


@dataclass(frozen=True)
class MemberPlan:
    user_id: int
    is_pro: bool
    ends_at: datetime | None = None     # when their Pro ends (None: it does not, or they are not on Pro)
    ending_soon: bool = False


@dataclass(frozen=True)
class PlanState:
    state: str
    members: tuple = field(default_factory=tuple)
    ending: tuple = field(default_factory=tuple)    # MemberPlan of everyone whose Pro ends within the warning window
    lapsed: tuple = field(default_factory=tuple)    # MemberPlan of everyone not on Pro

    @property
    def active(self):
        return self.state != PAUSED

    @property
    def earliest_end(self):
        ends = [m.ends_at for m in self.ending if m.ends_at]
        return min(ends) if ends else None


def member_plan(profile, now=None):
    now = now or timezone.now()
    if not profile.is_pro:
        return MemberPlan(user_id=profile.user_id, is_pro=False)
    ends_at = None if profile.is_lifetime else profile.subscription_end_date
    soon = bool(ends_at and ends_at - now <= timedelta(days=settings.FAMILY_PRO_WARNING_DAYS))
    return MemberPlan(user_id=profile.user_id, is_pro=True, ends_at=ends_at, ending_soon=soon)


def household_plan_state(household, now=None):
    """The plan state of a household, from its members' profiles (one query)."""
    now = now or timezone.now()
    plans = tuple(
        member_plan(m.user.profile, now)
        for m in household.members.select_related('user__profile')
    )
    lapsed = tuple(p for p in plans if not p.is_pro)
    ending = tuple(sorted((p for p in plans if p.ending_soon), key=lambda p: p.ends_at))
    state = PAUSED if lapsed else (WARNING if ending else ON)
    return PlanState(state=state, members=plans, ending=ending, lapsed=lapsed)
