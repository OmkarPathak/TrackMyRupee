import re
from datetime import timedelta
from decimal import Decimal
from django.utils import timezone
from finance_tracker.ai_utils import predict_category_ai

def parse_expense_nl(text, user_categories=None, user_accounts=None, user=None):
    if not text:
        return None

    now = timezone.localdate()
    date = now
    amount = None
    category = "Other"
    account = None
    is_clue_found = False

    amount_pattern = r'(?:₹|\$|€|£|¥)?\s*(\d+(?:\.\d+)?)\s*(k|K)?\b'
    amount_match = re.search(amount_pattern, text)
    
    text_for_others = text
    if amount_match:
        try:
            val_str = amount_match.group(1)
            val = float(val_str)
            suffix = amount_match.group(2)
            if suffix and suffix.lower() == 'k':
                val *= 1000
            amount = Decimal(str(val)).quantize(Decimal('0.01'))
            is_clue_found = True
            text_for_others = text[:amount_match.start()] + " " + text[amount_match.end():]
        except (ValueError, ArithmeticError):
            pass

    text_for_others = text_for_others.strip()

    date_keywords = {
        'yesterday': now - timedelta(days=1),
        'today': now,
    }
    
    for kw, dt in date_keywords.items():
        if re.search(rf'\b{kw}\b', text_for_others, re.IGNORECASE):
            date = dt
            text_for_others = re.sub(rf'\b{kw}\b', '', text_for_others, flags=re.IGNORECASE).strip()
            is_clue_found = True
            break
            
    # Extract account
    if user_accounts:
        for acc in user_accounts:
            if re.search(rf'\b{re.escape(acc)}\b', text_for_others, re.IGNORECASE):
                account = acc
                text_for_others = re.sub(rf'\b{re.escape(acc)}\b', '', text_for_others, flags=re.IGNORECASE).strip()
                is_clue_found = True
                break

    description = re.sub(r'\s+', ' ', text_for_others).strip()
    
    category_found = False
    # 0. Check User Categories First (Explicit Match)
    if user_categories:
        desc_lower = description.lower()
        for cat in user_categories:
            if cat.lower() in desc_lower:
                category = cat
                category_found = True
                is_clue_found = True
                break

    # 1. Use AI Prediction for Category (if not found in user_categories)
    if not category_found:
        predicted_category = predict_category_ai(description, user=user, categories=user_categories)
        if predicted_category:
            category = predicted_category
            is_clue_found = True

    if not description:
        description = "Expense"
    else:
        description = description[0].upper() + description[1:]

    return {
        'amount': str(amount) if amount else None,
        'category': category,
        'description': description,
        'account': account,
        'date': date.isoformat(),
        'success': amount is not None,
        'is_clue_found': is_clue_found
    }
