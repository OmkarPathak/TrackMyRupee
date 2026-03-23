from django.utils.translation import gettext_lazy as _

# Plan Tiers Configuration
# Central source of truth for all plan limits, pricing, and features.

PLAN_DETAILS = {
    'FREE': {
        'name': _('Free'),
        'tagline': _('Taste it, trust it'),
        'price_yearly': 0,
        'price_monthly': 0,
        'pricing_note': _('forever'),
        'limits': {
            'expenses_per_month': 30,
            'accounts': 3,
            'recurring_transactions': 0,
            'budget_categories': 3,
            'savings_goals': 1,
            'email_notifications': False,
            'history_access': _('3 months'),
            'dashboard': _('Full'),
            'net_worth': False,
            'ai_insights': False,
            'export_csv': False,
            'year_in_review': False,
            'support': _('Community'),
        }
    },
    'PLUS': {
        'name': _('Plus'),
        'tagline': _('For the serious saver'),
        'price_yearly': 299,
        'price_monthly': 49,
        'pricing_note': _('per year'),
        'limits': {
            'expenses_per_month': -1,  # -1 for Unlimited
            'accounts': 5,
            'recurring_transactions': 3,
            'budget_categories': 10,
            'savings_goals': 3,
            'email_notifications': True,
            'history_access': _('2 years'),
            'dashboard': _('Full'),
            'net_worth': True,
            'ai_insights': False,
            'export_csv': True,
            'year_in_review': True,
            'support': _('Email'),
        }
    },
    'PRO': {
        'name': _('Pro'),
        'tagline': _('Full financial command'),
        'price_yearly': 499,
        'price_monthly': 99,
        'pricing_note': _('per year'),
        'limits': {
            'expenses_per_month': -1,
            'accounts': -1,
            'recurring_transactions': -1,
            'budget_categories': -1,
            'savings_goals': -1,
            'email_notifications': True,
            'history_access': _('Unlimited'),
            'dashboard': _('Full'),
            'net_worth': True,
            'ai_insights': -1, # Unlimited
            'export_csv': True,
            'year_in_review': True,
            'support': _('Priority email'),
        }
    }
}

def get_limit(tier, limit_key):
    """Utility to get a specific limit for a tier."""
    return PLAN_DETAILS.get(tier, PLAN_DETAILS['FREE'])['limits'].get(limit_key)
