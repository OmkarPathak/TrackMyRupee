
import logging
import re

logger = logging.getLogger(__name__)

# Simple Rule-Based Keyword Mapping
KEYWORD_MAPPING = {
    'Groceries': ['groceries', 'vegetables', 'fruits', 'milk', 'kirana', 'sabzi', 'किराना', 'सब्जी', 'भाजी', 'दूध'],
    'Dining Out': ['food', 'dinner', 'lunch', 'breakfast', 'snack', 'coffee', 'tea', 'cafe', 'restaurant', 'burger', 'pizza', 'zomato', 'swiggy', 'dining', 'chai', 'khana', 'चाय', 'खाना', 'जेवण', 'नाश्ता'],
    'Transport': ['taxi', 'uber', 'ola', 'auto', 'bus', 'train', 'metro', 'flight', 'ticket', 'fuel', 'petrol', 'diesel', 'parking', 'toll', 'travel', 'rickshaw', 'रिक्शा', 'ऑटो'],
    'Shopping': ['amazon', 'flipkart', 'myntra', 'clothes', 'shoes', 'mall', 'store', 'shop', 'electronics', 'gadget'],
    'Utilities': ['electricity', 'water', 'gas', 'bill', 'recharge', 'wifi', 'internet', 'broadband', 'phone', 'mobile', 'subscription', 'netflix', 'spotify', 'prime', 'utility', 'utilities'],
    'Health': ['doctor', 'hospital', 'medicine', 'pharmacy', 'clinic', 'gym', 'fitness', 'workout', 'yoga'],
    'Education': ['book', 'course', 'udemy', 'coursera', 'school', 'college', 'fee', 'tuition', 'stationary'],
    'Entertainment': ['movie', 'cinema', 'theatre', 'game', 'concert', 'show', 'event', 'party', 'pub', 'bar'],
    'Rent': ['rent', 'house', 'maintenance'],
    'Salary': ['salary', 'wage', 'paycheck', 'bonus', 'stipend'],
    'Investment': ['stock', 'mutual fund', 'sip', 'gold', 'fd', 'rd', 'crypto'],
    'Cab': ['cab', 'taxi', 'ola', 'uber', 'auto', 'rental', 'car', 'rapido'],
}

def predict_category_rule_based(description):
    """
    Predicts category based on keywords in the description.
    Returns the probable category or None.
    """
    description = description.lower()
    
    # Normalize description
    words = re.findall(r'\w+', description)
    
    # direct match
    for category, keywords in KEYWORD_MAPPING.items():
        for keyword in keywords:
            if keyword in words or keyword in description:
                # Check for exact word match or substring if useful
                return category
    return None

def predict_category_ai(description, user=None, categories=None, history_map=None):
    """
    Predicts category using:
    1. Historical Data (User-specific Custom Categories or pre-fetched history_map)
    2. Rule-Based Keywords (General)
    """
    description = description.strip()
    
    # 0. Check Historical Data (Personalization)
    if history_map is not None:
        desc_lower = description.lower()
        if isinstance(history_map, dict):
            if 'exact' in history_map and desc_lower in history_map['exact']:
                return history_map['exact'][desc_lower]
            elif desc_lower in history_map:
                return history_map[desc_lower]

            words = desc_lower.split()
            if len(words) >= 1:
                first_word = words[0]
                if len(first_word) > 3 and 'prefix' in history_map and first_word in history_map['prefix']:
                    return history_map['prefix'][first_word]
    elif user:
        try:
            # Avoid circular import
            from django.db.models import Count

            from expenses.models import Expense
            
            # 1. Exact match (case insensitive)
            exact_match = Expense.objects.filter(user=user, description__iexact=description).values('category').annotate(count=Count('category')).order_by('-count').first()
            if exact_match:
                return exact_match['category']
                
            # 2. Starts With matches from history
            words = description.split()
            if len(words) >= 1:
                first_word = words[0]
                if len(first_word) > 3:
                     similar = Expense.objects.filter(user=user, description__istartswith=first_word).values('category').annotate(count=Count('category')).order_by('-count').first()
                     if similar:
                          return similar['category']

        except Exception as e:
            logger.debug("Historical category prediction failed: %s", e)
            pass

    # 1. Try Rule-Based First (Fastest, Free)
    category = predict_category_rule_based(description)
    if category:
        # If we have a specific list of user categories, check if the rule-based one matches any
        if categories:
            cat_lower = category.lower()
            cat_words = set(re.findall(r'\w+', cat_lower))
            for uc in categories:
                uc_lower = uc.lower()
                # 1. Exact or substring match
                if uc_lower == cat_lower or uc_lower in cat_lower or cat_lower in uc_lower:
                    return uc
                # 2. Word overlap match (excluding tiny words like 'and', 'the', '&')
                uc_words = set(re.findall(r'\w+', uc_lower))
                significant_overlap = [w for w in cat_words.intersection(uc_words) if len(w) > 2]
                if significant_overlap:
                    return uc
        return category

    return None
