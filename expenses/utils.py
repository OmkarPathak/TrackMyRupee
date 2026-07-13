import logging
from decimal import Decimal

import requests
from django.core.cache import cache
from django.db.models import Count, Sum
from django.db.models.functions import ExtractMonth
from django.utils.translation import get_language

logger = logging.getLogger(__name__)


def get_exchange_rate(from_curr, to_curr):
    """
    Fetches the exchange rate between two currencies using Frankfurter API.
    Uses Django cache to avoid repeated external requests.
    """
    if from_curr == to_curr:
        return Decimal('1.0')

    # Currency mappings (if symbols are used in DB)
    symbol_to_code = {
        '₹': 'INR',
        '$': 'USD',
        '€': 'EUR',
        '£': 'GBP',
        '¥': 'JPY',
        'A$': 'AUD',
        'C$': 'CAD',
        'CHF': 'CHF',
        '元': 'CNY',
        '₩': 'KRW',
    }

    from_code = symbol_to_code.get(from_curr, from_curr)
    to_code = symbol_to_code.get(to_curr, to_curr)

    if from_code == to_code:
        return Decimal('1.0')

    cache_key = f"xr_{from_code}_{to_code}"
    cached_rate = cache.get(cache_key)
    if cached_rate:
        return Decimal(str(cached_rate))

    try:
        # Primary: Frankfurter API
        url = f"https://api.frankfurter.app/latest?from={from_code}&to={to_code}"
        response = requests.get(url, timeout=5)
        response.raise_for_status()
        data = response.json()
        rate = data['rates'][to_code]
        
        # Cache for 24 hours
        cache.set(cache_key, rate, 60*60*24)
        
        try:
            FXRate.objects.update_or_create(
                from_currency=from_code,
                to_currency=to_code,
                as_of_date=timezone.now().date(),
                defaults={'rate': Decimal(str(rate)), 'source': 'Frankfurter'}
            )
        except Exception as db_err:
            logger.warning("Failed to record FXRate: %s", db_err)
            
        return Decimal(str(rate))
    except Exception as e:
        print(f"Frankfurter API error: {e}. Trying fallback...")
        
        try:
            # Fallback: ExchangeRate-API (v4 - free tier, no key needed for simple pairs)
            # Standard URL: https://api.exchangerate-api.com/v4/latest/{base}
            fallback_url = f"https://api.exchangerate-api.com/v4/latest/{from_code}"
            fb_response = requests.get(fallback_url, timeout=5)
            fb_response.raise_for_status()
            fb_data = fb_response.json()
            rate = fb_data['rates'][to_code]
            
            # Cache for 24 hours
            cache.set(cache_key, rate, 60*60*24)
            
            try:
                FXRate.objects.update_or_create(
                    from_currency=from_code,
                    to_currency=to_code,
                    as_of_date=timezone.now().date(),
                    defaults={'rate': Decimal(str(rate)), 'source': 'ExchangeRate-API'}
                )
            except Exception as db_err:
                logger.warning("Failed to record FXRate: %s", db_err)
                
            return Decimal(str(rate))
        except Exception as fb_e:
            print(f"Fallback API error: {fb_e}")
            logger.warning("All exchange rate APIs failed for %s->%s.", from_code, to_code)
            raise RuntimeError(
                f"Exchange rate unavailable for {from_code}->{to_code}."
            ) from fb_e


def generate_year_in_review_data(user, year):
    """
    Aggregates financial data for a specific year to generate a 'Year in Review' summary.
    Returns a dictionary of statistics.
    """
    from .models import Expense, Income, SavingsGoal
    data = {}
    
    # Base querysets
    expenses = Expense.objects.filter(user=user, date__year=year)
    incomes = Income.objects.filter(user=user, date__year=year)
    goals = SavingsGoal.objects.filter(user=user, created_at__year=year, is_completed=True)
    
    # 1. Total Spend and Total Income (using base_amount to handle multi-currency)
    total_spent = expenses.aggregate(total=Sum('base_amount'))['total'] or Decimal('0.00')
    total_earned = incomes.aggregate(total=Sum('base_amount'))['total'] or Decimal('0.00')
    
    data['total_spent'] = total_spent
    data['total_earned'] = total_earned
    data['net_saved'] = total_earned - total_spent
    data['transaction_count'] = expenses.count()
    
    if data['transaction_count'] == 0:
        data['has_data'] = False
        return data
        
    data['has_data'] = True
    
    # 2. Top 3 Categories
    top_categories = expenses.values('category').annotate(
        total=Sum('base_amount'),
        count=Count('id')
    ).order_by('-total')[:3]
    data['top_categories'] = list(top_categories)
    
    # 3. Highest and Lowest Spend Month
    monthly_spends = expenses.annotate(month=ExtractMonth('date')).values('month').annotate(
        total=Sum('base_amount')
    ).order_by('-total')
    
    month_names = {
        1: 'January', 2: 'February', 3: 'March', 4: 'April', 
        5: 'May', 6: 'June', 7: 'July', 8: 'August', 
        9: 'September', 10: 'October', 11: 'November', 12: 'December'
    }
    
    if monthly_spends:
        highest_month = monthly_spends[0]
        lowest_month = monthly_spends.last()
        
        data['highest_month'] = {
            'name': month_names.get(highest_month['month'], 'Unknown'),
            'total': highest_month['total']
        }
        data['lowest_month'] = {
            'name': month_names.get(lowest_month['month'], 'Unknown'),
            'total': lowest_month['total']
        }
    else:
        data['highest_month'] = None
        data['lowest_month'] = None

    # 4. Favorite Payment Method
    top_payment_method = expenses.values('payment_method').annotate(
        count=Count('id')
    ).order_by('-count').first()
    
    if top_payment_method:
        data['favorite_payment_method'] = top_payment_method['payment_method']
        data['payment_method_count'] = top_payment_method['count']
    else:
        data['favorite_payment_method'] = 'Unknown'
        data['payment_method_count'] = 0
        
    # 5. Goals crushed
    data['goals_completed'] = goals.count()
    
    # 6. Biggest single purchase
    biggest_expense = expenses.order_by('-base_amount').first()
    if biggest_expense:
        data['biggest_expense'] = {
            'amount': biggest_expense.base_amount,
            'description': biggest_expense.description,
            'date': biggest_expense.date,
            'category': biggest_expense.category
        }
    else:
        data['biggest_expense'] = None

    # 7. Total Invested (Transfers to INVESTMENT or FIXED_DEPOSIT)
    from .models import Account, Transfer
    investments = Transfer.objects.filter(user=user, date__year=year, to_account__account_type__in=['INVESTMENT', 'FIXED_DEPOSIT'])
    data['total_invested'] = investments.aggregate(total=Sum('converted_amount'))['total'] or Decimal('0.00')

    # 8. Accounts Used
    data['account_count'] = Account.objects.filter(user=user, is_active=True).count()

    return data


BOOTSTRAP_ICONS = [
    ('bi-tag', 'Tag (General)'),
    ('bi-egg-fried', 'Food'),
    ('bi-cart3', 'Groceries'),
    ('bi-car-front', 'Transport'),
    ('bi-receipt', 'Bills'),
    ('bi-arrow-repeat', 'Subscriptions'),
    ('bi-piggy-bank', 'Savings'),
    ('bi-film', 'Entertainment'),
    ('bi-house', 'Rent/Home'),
    ('bi-lightning', 'Electricity'),
    ('bi-droplet', 'Water'),
    ('bi-phone', 'Phone/Internet'),
    ('bi-suit-heart', 'Health'),
    ('bi-gift', 'Gifts'),
    ('bi-briefcase', 'Work/Business'),
    ('bi-mortarboard', 'Education'),
    ('bi-airplane', 'Travel'),
    ('bi-controller', 'Gaming'),
    ('bi-music-note', 'Music'),
    ('bi-shop', 'Shopping'),
    ('bi-tools', 'Maintenance'),
    ('bi-credit-card', 'Credit/Debt'),
    ('bi-cash-stack', 'Cash'),
    ('bi-cup-hot', 'Coffee/Cafe'),
    ('bi-tsunami', 'Insurance'),
    ('bi-capsule', 'Medicines'),
    ('bi-fuel-pump', 'Fuel'),
    ('bi-gem', 'Jewellery'),
    ('bi-basket', 'Vegetables'),
    ('bi-upc-scan', 'Groceries'),
    ('bi-handbag', 'Clothing'),
]


def format_indian_number(amount):
    """
    Formats a number according to the Indian Numbering System (Lakhs/Crores).
    Ex: 1234567 -> 12,34,567.00
    """
    try:
        # Handle sign
        is_negative = float(amount) < 0
        abs_amount = abs(float(amount))
        
        integer_part = str(int(round(abs_amount)))
        
        if len(integer_part) <= 3:
            result = integer_part
        else:
            last_three = integer_part[-3:]
            remaining = integer_part[:-3]
            
            groups = []
            while len(remaining) > 0:
                if len(remaining) > 2:
                    groups.append(remaining[-2:])
                    remaining = remaining[:-2]
                else:
                    groups.append(remaining)
                    remaining = ""
            groups.reverse()
            
            result = ",".join(groups) + "," + last_three
            
        return f"-{result}" if is_negative else result
    except (ValueError, TypeError):
        return amount

def translate_digits(value):
    if value is None:
        return ""
    
    lang = get_language()
    if lang not in ['mr', 'hi']:
        return value
    
    value_str = str(value)
    arabic_to_devanagari = {
        '0': '०', '1': '१', '2': '२', '3': '३', '4': '४',
        '5': '५', '6': '६', '7': '७', '8': '८', '9': '९'
    }
    
    return ''.join(arabic_to_devanagari.get(char, char) for char in value_str)
