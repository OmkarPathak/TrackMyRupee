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
            'recurring_transactions': 2,
            'budget_categories': 5,
            'savings_goals': 1,
            'email_notifications': False,
            'dashboard': _('Full'),
            'net_worth': True,
            'net_worth_history': 2,
            'ai_insights': True,
            'export_csv': False,
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
            'recurring_transactions': 5,
            'budget_categories': 15,
            'savings_goals': 5,
            'email_notifications': True,
            'dashboard': _('Full'),
            'net_worth': True,
            'net_worth_history': 6,
            'ai_insights': True,
            'export_csv': True,
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
            'recurring_transactions': -1,
            'budget_categories': -1,
            'savings_goals': -1,
            'email_notifications': True,
            'dashboard': _('Full'),
            'net_worth': True,
            'net_worth_history': -1,
            'ai_insights': True,
            'export_csv': True,
            'year_in_review': True,
            'support': _('Priority email'),
        }
    }
}

def get_limit(tier, limit_key):
    """Utility to get a specific limit for a tier."""
    return PLAN_DETAILS.get(tier, PLAN_DETAILS['FREE'])['limits'].get(limit_key)
