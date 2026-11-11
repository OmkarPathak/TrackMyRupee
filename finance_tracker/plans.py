from django.utils.translation import gettext
from django.utils.translation import gettext_lazy as _

# Plan Tiers Configuration
# Central source of truth for all plan limits, pricing, and features.

PLAN_DETAILS = {
    'FREE': {
        'name': _('Free'),
        'tagline': _('Try it at your own pace'),
        'price_yearly': 0,
        'price_monthly': 0,
        'pricing_note': _('forever'),
        'limits': {
            'expenses_per_month': 90,
            'accounts': 2,
            'loans': 0,
            'recurring_transactions': 2,
            'budget_categories': 5,
            'savings_goals': 1,
            'email_notifications': False,
            'dashboard': _('Full'),
            'net_worth': True,
            'net_worth_history': 2,
            'ai_insights': True,
            'year_in_review': False,
            'support': _('Community'),
        }
    },
    'PLUS': {
        'name': _('Plus'),
        'tagline': _('For the serious saver'),
        'price_yearly': 499,
        'price_monthly': 49,
        'pricing_note': _('per year'),
        'limits': {
            'expenses_per_month': -1,  # -1 for Unlimited
            'accounts': 10,
            'loans': 1,
            'recurring_transactions': 5,
            'budget_categories': 15,
            'savings_goals': 5,
            'email_notifications': True,
            'dashboard': _('Full'),
            'net_worth': True,
            'net_worth_history': 6,
            'ai_insights': True,
            'year_in_review': True,
            'support': _('Email'),
        }
    },
    'PRO': {
        'name': _('Pro'),
        'tagline': _('Everything, unlimited'),
        'price_yearly': 999,
        'price_monthly': 99,
        'pricing_note': _('per year'),
        'limits': {
            'expenses_per_month': -1,
            'accounts': -1,
            'loans': -1,
            'recurring_transactions': -1,
            'budget_categories': -1,
            'savings_goals': -1,
            'email_notifications': True,
            'dashboard': _('Full'),
            'net_worth': True,
            'net_worth_history': -1,
            'ai_insights': True,
            'year_in_review': True,
            'support': _('Priority email'),
        }
    }
}

def get_limit(tier, limit_key):
    """Utility to get a specific limit for a tier."""
    return PLAN_DETAILS.get(tier, PLAN_DETAILS['FREE'])['limits'].get(limit_key)


NEXT_TIER = {'FREE': 'PLUS', 'PLUS': 'PRO'}


def lowest_tier_with(limit_key):
    """The cheapest plan where `limit_key` is not zero (a feature the lower plans do not have), or None."""
    for tier in ('FREE', 'PLUS', 'PRO'):
        if get_limit(tier, limit_key) != 0:
            return tier
    return None


def plan_unlocks(tier):
    """What the next plan adds to `tier`, as short plain sentences in the active language, most useful first.
    Empty for the top plan. Built from the limits above, so the list never disagrees with the pricing page."""
    next_tier = NEXT_TIER.get(tier)
    if next_tier is None:
        return []
    cur = PLAN_DETAILS[tier]['limits']
    new = PLAN_DETAILS[next_tier]['limits']
    lines = []

    if cur['loans'] == 0 and new['loans'] != 0:
        lines.append(gettext('Track loans: the balance left, the interest and the end date'))
    elif new['loans'] == -1 and cur['loans'] != -1:
        lines.append(gettext('Unlimited loans'))

    def counted(key, unlimited, limited):
        before, after = cur[key], new[key]
        if after == before or before == -1:
            return
        if after == -1:
            lines.append(gettext(unlimited) % {'cur': before})
        else:
            lines.append(gettext(limited) % {'cur': before, 'new': after})

    counted('accounts', 'Unlimited accounts, instead of %(cur)s',
            'Up to %(new)s accounts, instead of %(cur)s')
    counted('recurring_transactions', 'Unlimited recurring payments, instead of %(cur)s',
            'Up to %(new)s recurring payments, instead of %(cur)s')
    counted('budget_categories', 'Unlimited budget categories, instead of %(cur)s',
            'Up to %(new)s budget categories, instead of %(cur)s')
    counted('savings_goals', 'Unlimited savings goals, instead of %(cur)s',
            'Up to %(new)s savings goals, instead of %(cur)s')
    if cur['expenses_per_month'] != -1 and new['expenses_per_month'] == -1:
        lines.append(gettext('Unlimited expenses each month, instead of %(cur)s') % {'cur': cur['expenses_per_month']})
    counted('net_worth_history', 'Your whole net worth history, instead of the last %(cur)s months',
            'Net worth history for %(new)s months, instead of %(cur)s')
    if not cur['email_notifications'] and new['email_notifications']:
        lines.append(gettext('Reminders and alerts by email'))
    if not cur['year_in_review'] and new['year_in_review']:
        lines.append(gettext('Your yearly review, with the year in numbers'))
    return lines

