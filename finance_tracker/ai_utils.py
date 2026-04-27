
import os
import re

# Simple Rule-Based Keyword Mapping
KEYWORD_MAPPING = {
    'Groceries': ['groceries', 'vegetables', 'fruits'],
    'Dining Out': ['food', 'dinner', 'lunch', 'breakfast', 'snack', 'coffee', 'tea', 'cafe', 'restaurant', 'burger', 'pizza', 'zomato', 'swiggy', 'dining'],
    'Transport': ['taxi', 'uber', 'ola', 'auto', 'bus', 'train', 'metro', 'flight', 'ticket', 'fuel', 'petrol', 'diesel', 'parking', 'toll', 'travel'],
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

def predict_category_ai(description, user=None, categories=None):
    """
    Predicts category using:
    1. Historical Data (User-specific Custom Categories)
    2. Rule-Based Keywords (General)
    3. Generative AI (Gemini) - Fallback
    """
    description = description.strip()
    
    # 0. Check Historical Data (Personalization)
    if user:
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
            print(f"Historical Prediction Error: {e}")
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

    # 2. Try Gemini AI (if configured)
    api_key = os.getenv('GEMINI_API_KEY')
    if api_key:
        try:
            # Lazy import to avoid import errors if library not installed
            import google.generativeai as genai
            
            genai.configure(api_key=api_key)
            model = genai.GenerativeModel('gemini-1.5-flash') # Use Flash for speed/cost
            
            # Use user's categories if available, otherwise fallback to defaults
            category_list = ", ".join(categories) if categories else "Food, Groceries, Transport, Shopping, Bills, Health, Education, Entertainment, Rent, Investment, Other"
            
            prompt = f"""
            Classify the following expense description into one of these categories: 
            {category_list}
            
            Description: "{description}"
            
            Return ONLY the category name. If none fit perfectly, pick the closest one.
            """
            
            response = model.generate_content(prompt)
            if response.text:
                return response.text.strip()
                
        except Exception as e:
            print(f"Gemini API Error: {e}")
            pass
            
    return None
