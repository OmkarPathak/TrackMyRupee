"""Natural-language expense parser (dry run: never touches the database).

``parse_expense_nl`` turns one typed/spoken line such as ``"swiggy dinner 450 upi"``
into the six composer fields (amount, date, description, category, account,
payment method) plus a currency, each with a confidence level.  Callers supply
pre-fetched context (categories, accounts, learned keyword hints) so the parser
itself stays pure and fast.
"""
import re
from datetime import date as date_cls
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.utils import timezone

from finance_tracker.ai_utils import predict_category_ai

# ── Boundaries that also work next to Devanagari (\b does not, matras are not \w) ──
_L = r'(?<![A-Za-z0-9_ऀ-ॿ])'
_R = r'(?![A-Za-z0-9_ऀ-ॿ])'

_DEVANAGARI_DIGITS = str.maketrans('०१२३४५६७८९', '0123456789')

# ── Currency words → the symbol the app stores in Expense.currency ──────────────
CURRENCY_WORDS = {
    '₹': '₹', 'inr': '₹', 'rs.': '₹', 'rs': '₹', 'rupee': '₹', 'rupees': '₹', 'rupaye': '₹',
    'रुपये': '₹', 'रुपए': '₹', 'रु': '₹',
    '$': '$', 'usd': '$', 'dollar': '$', 'dollars': '$', 'bucks': '$',
    '€': '€', 'eur': '€', 'euro': '€', 'euros': '€',
    '£': '£', 'gbp': '£', 'pound': '£', 'pounds': '£',
    '¥': '¥', 'jpy': '¥', 'yen': '¥',
    'a$': 'A$', 'aud': 'A$',
    'c$': 'C$', 'cad': 'C$',
    'chf': 'CHF',
    '元': '元', 'cny': '元', 'yuan': '元', 'rmb': '元',
    '₩': '₩', 'krw': '₩', 'won': '₩',
}
_CUR_RE = '|'.join(re.escape(w) for w in sorted(CURRENCY_WORDS, key=len, reverse=True))

_MULTIPLIERS = {
    'k': 1000, 'thousand': 1000, 'hazaar': 1000, 'hazar': 1000, 'hajar': 1000, 'हजार': 1000, 'हज़ार': 1000,
    'lakh': 100000, 'lakhs': 100000, 'lac': 100000, 'lacs': 100000, 'lak': 100000, 'लाख': 100000,
    'crore': 10000000, 'crores': 10000000, 'cr': 10000000, 'करोड़': 10000000, 'करोड': 10000000,
}
_MULT_RE = '|'.join(re.escape(w) for w in sorted(_MULTIPLIERS, key=len, reverse=True))

_NUM = r'\d{1,3}(?:,\d{2,3})+(?:\.\d+)?|\d+(?:\.\d+)?'
_AMOUNT_RE = re.compile(
    r'(?<![\w.,])(?P<pre>' + _CUR_RE + r')?\s*(?P<num>' + _NUM + r')\s*(?P<mult>' + _MULT_RE + r')?'
    r'\s*(?P<post>' + _CUR_RE + r')?' + _R,
    re.IGNORECASE | re.UNICODE,
)

# ── Dates ──────────────────────────────────────────────────────────────────────
_MONTHS = {
    'jan': 1, 'feb': 2, 'mar': 3, 'apr': 4, 'may': 5, 'jun': 6,
    'jul': 7, 'aug': 8, 'sep': 9, 'oct': 10, 'nov': 11, 'dec': 12,
}
_MONTH_RE = (
    r'jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|'
    r'sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?'
)
_DATE_DM_RE = re.compile(
    _L + r'(?P<d>\d{1,2})(?:st|nd|rd|th)?\s*(?:of\s+)?(?P<m>' + _MONTH_RE + r')\.?'
    r'(?:\s*,?\s*(?P<y>\d{4}))?' + _R, re.IGNORECASE)
_DATE_MD_RE = re.compile(
    _L + r'(?P<m>' + _MONTH_RE + r')\.?\s+(?P<d>\d{1,2})(?:st|nd|rd|th)?'
    r'(?:\s*,?\s*(?P<y>\d{4}))?' + _R, re.IGNORECASE)
_DATE_SLASH_RE = re.compile(
    _L + r'(?P<d>\d{1,2})[/-](?P<m>\d{1,2})(?:[/-](?P<y>\d{2,4}))?' + _R)

# "kal" means yesterday or tomorrow; an expense is something already spent, so yesterday.
_DATE_WORDS = {
    'day before yesterday': -2, 'yesterday': -1, 'today': 0,
    'parso': -2, 'parson': -2, 'kal': -1, 'aaj': 0,
    'परसों': -2, 'परसो': -2, 'कल': -1, 'आज': 0, 'परवा': -2, 'काल': -1,
}
_DATE_WORD_RE = re.compile(
    _L + '(?P<w>' + '|'.join(re.escape(w) for w in sorted(_DATE_WORDS, key=len, reverse=True)) + ')' + _R,
    re.IGNORECASE)

# ── Payment methods (values match Expense.PAYMENT_OPTIONS exactly) ────────────
_PAYMENT_PHRASES = {
    'UPI': ['upi', 'gpay', 'g pay', 'google pay', 'googlepay', 'phonepe', 'phone pe', 'paytm', 'bhim', 'यूपीआई'],
    'Credit Card': ['credit card', 'creditcard', 'cc', 'क्रेडिट कार्ड'],
    'Debit Card': ['debit card', 'debitcard', 'dc', 'डेबिट कार्ड'],
    'Cash': ['cash', 'कैश', 'नकद', 'रोख'],
    'NetBanking': ['net banking', 'netbanking', 'neft', 'imps', 'rtgs', 'नेट बैंकिंग'],
}
_PAYMENT_LOOKUP = {p: m for m, phrases in _PAYMENT_PHRASES.items() for p in phrases}
_PAYMENT_RE = re.compile(
    _L + '(?P<p>' + '|'.join(re.escape(p) for p in sorted(_PAYMENT_LOOKUP, key=len, reverse=True)) + ')' + _R,
    re.IGNORECASE)
_BARE_CARD_RE = re.compile(_L + r'card' + _R, re.IGNORECASE)

_BANK_TYPES = {'SAVINGS_ACCOUNT', 'SALARY_ACCOUNT', 'CURRENT_ACCOUNT', 'BANK'}

# Words inside account names that say nothing about *which* account.
_ACCOUNT_GENERIC = {
    'account', 'accounts', 'savings', 'saving', 'bank', 'card', 'cards', 'credit', 'debit', 'wallet',
    'current', 'salary', 'loan', 'the', 'and', 'of', 'my',
}

# Connector words dropped from the description when they touch a recognised token.
_CONNECTORS = {
    'spent', 'paid', 'pay', 'on', 'for', 'at', 'via', 'using', 'by', 'with', 'from', 'in', 'through',
    'liye', 'ko', 'se', 'mein', 'me', 'par', 'ka', 'ki', 'ke',
}

_KEYWORD_STOPWORDS = _CONNECTORS | {'a', 'an', 'the', 'and', 'to', 'of', 'my', 'expense'}


def keyword_candidates(text):
    """Lower-cased word tokens (len >= 3, non-numeric) used to look up learned hints."""
    if not text:
        return []
    text = text.translate(_DEVANAGARI_DIGITS).lower()
    seen = []
    for tok in re.findall(r"[^\W\d_]{3,}", text, re.UNICODE):
        if tok not in _KEYWORD_STOPWORDS and tok not in seen:
            seen.append(tok)
    return seen[:12]


def keyword_for_description(description):
    """The single keyword remembered for a description (its first meaningful word)."""
    cands = keyword_candidates(description)
    return cands[0] if cands else ''


def _mask(text, spans):
    chars = list(text)
    for start, end in spans:
        for i in range(start, end):
            chars[i] = ' '
    return ''.join(chars)


def _safe_date(year, month, day):
    try:
        return date_cls(year, month, day)
    except ValueError:
        return None


def _resolve_year(day, month, year, today):
    """Pick a year for a date written without one: this year unless that is far in the future."""
    if year:
        year = int(year)
        if year < 100:
            year += 2000
        return _safe_date(year, month, day)
    candidate = _safe_date(today.year, month, day)
    if candidate and candidate > today + timedelta(days=31):
        candidate = _safe_date(today.year - 1, month, day)
    return candidate


def _extract_date(text, today):
    """Return (date, span) for the first date expression, or (None, None)."""
    for rx in (_DATE_DM_RE, _DATE_MD_RE):
        m = rx.search(text)
        if m:
            found = _resolve_year(int(m.group('d')), _MONTHS[m.group('m')[:3].lower()], m.group('y'), today)
            if found:
                return found, m.span()
    m = _DATE_SLASH_RE.search(text)
    if m:
        day, month = int(m.group('d')), int(m.group('m'))
        if 1 <= day <= 31 and 1 <= month <= 12:
            found = _resolve_year(day, month, m.group('y'), today)
            if found:
                return found, m.span()
    m = _DATE_WORD_RE.search(text)
    if m:
        return today + timedelta(days=_DATE_WORDS[m.group('w').lower()]), m.span()
    return None, None


def _to_decimal(raw, mult):
    try:
        value = Decimal(raw.replace(',', ''))
    except InvalidOperation:
        return None
    if mult:
        value *= _MULTIPLIERS[mult.lower()]
    return value.quantize(Decimal('0.01'))


def _extract_amount(text):
    """Return (Decimal|None, currency symbol|None, span|None, confidence)."""
    candidates = []
    for m in _AMOUNT_RE.finditer(text):
        value = _to_decimal(m.group('num'), m.group('mult'))
        if value is None or value <= 0:
            continue
        marker = m.group('pre') or m.group('post')
        marked = bool(marker or m.group('mult'))
        currency = CURRENCY_WORDS[marker.lower()] if marker else None
        candidates.append((marked, value, currency, m.span()))
    if not candidates:
        return None, None, None, None
    marked = [c for c in candidates if c[0]]
    if marked:
        pick = marked[0]
        return pick[1], pick[2], pick[3], 'high' if len(marked) == 1 else 'medium'
    if len(candidates) == 1:
        return candidates[0][1], None, candidates[0][3], 'high'
    # Several bare numbers ("pizza 2 450"): the biggest is almost always the price.
    pick = max(candidates, key=lambda c: c[1])
    return pick[1], None, pick[3], 'low'


def _match_account(text, accounts):
    """Return (account dict|None, span|None, confidence)."""
    best = []
    for acc in accounts:
        name = acc['name']
        full = re.search(_L + re.escape(name) + _R, text, re.IGNORECASE)
        if full:
            best.append((len(name) + 100, acc, full.span()))
            continue
        for tok in re.findall(r'[^\W_]+', name, re.UNICODE):
            if len(tok) < 3 or tok.lower() in _ACCOUNT_GENERIC:
                continue
            m = re.search(_L + re.escape(tok) + _R, text, re.IGNORECASE)
            if m:
                best.append((len(tok), acc, m.span()))
                break
    if not best:
        return None, None, None
    best.sort(key=lambda b: -b[0])
    top = best[0]
    tied = [b for b in best if b[0] == top[0] and b[1] is not top[1]]
    return top[1], top[2], ('low' if tied else 'high')


def parse_expense_nl(text, user_categories=None, user_accounts=None, user=None, account_info=None,
                     hints=None, default_currency='₹', default_account=None, skip_genai=False):
    """Parse one line of free text into expense fields.

    Args:
        user_categories: names of the user's categories.
        user_accounts: names of the user's active accounts (legacy; ``account_info`` is richer).
        account_info: list of ``{'id', 'name', 'type'}`` dicts for the user's active accounts.
        hints: ``{keyword: {'category', 'account_id', 'account', 'payment_method'}}`` learned from
            the user's own earlier expenses.  These beat generic guesses.
        default_currency: symbol used when the text carries no currency signal.
        default_account: info dict of the account that will be pre-selected (used only to infer
            what a bare "card" means).
        skip_genai: never call the generative fallback (keeps the endpoint fast).
    """
    if not text or not text.strip():
        return None

    today = timezone.localdate()
    text = text.translate(_DEVANAGARI_DIGITS)
    if account_info is None:
        account_info = [{'id': None, 'name': n, 'type': ''} for n in (user_accounts or [])]
    hints = hints or {}

    consumed = []
    confidence = {}

    # 1. Date first, so "3 oct" / "03/10" are never mistaken for amounts.
    parsed_date, span = _extract_date(text, today)
    date = parsed_date or today
    if span:
        consumed.append(span)
        confidence['date'] = 'high'
    is_clue_found = bool(span)

    # 2. Amount and currency.
    amount, currency, span, amount_conf = _extract_amount(_mask(text, consumed))
    if span:
        consumed.append(span)
        is_clue_found = True
        confidence['amount'] = amount_conf
    if currency:
        confidence['currency'] = 'high'

    # 3. Payment method: explicit words first.
    methods = []
    pay_spans = []
    for m in _PAYMENT_RE.finditer(text):
        methods.append(_PAYMENT_LOOKUP[m.group('p').lower()])
        pay_spans.append(m.span())
    payment_method = None
    payment_ambiguous = False
    if methods:
        payment_method = methods[0]
        confidence['payment_method'] = 'high' if len(set(methods)) == 1 else 'low'
        consumed.extend(pay_spans)
        is_clue_found = True

    # 4. Account hint (bank / account name in the text).
    account, span, account_conf = _match_account(text, account_info)
    if account:
        confidence['account'] = account_conf
        consumed.append(span)
        is_clue_found = True

    # 5. A bare "card" is only usable when the account tells us which kind.
    if not payment_method:
        card = next((m for m in _BARE_CARD_RE.finditer(text)
                     if not any(s <= m.start() < e for s, e in pay_spans)), None)
        if card:
            consumed.append(card.span())
            ref = account or default_account
            if ref and ref.get('type') == 'CREDIT_CARD':
                payment_method, confidence['payment_method'] = 'Credit Card', 'medium'
            elif ref and ref.get('type') in _BANK_TYPES:
                payment_method, confidence['payment_method'] = 'Debit Card', 'medium'
            else:
                payment_ambiguous = True
                confidence['payment_method'] = 'low'
            is_clue_found = True

    # 6. Description: whatever no other field claimed.
    blanked = list(text)
    for start, end in consumed:
        for i in range(start, end):
            blanked[i] = '\x00'
    blanked = ''.join(blanked)
    conn = '|'.join(sorted(_CONNECTORS, key=len, reverse=True))
    blanked = re.sub(_L + r'(?:' + conn + r')' + _R + r'\s*\x00', '\x00', blanked, flags=re.IGNORECASE)
    blanked = re.sub(r'\x00\s*' + _L + r'(?:' + conn + r')' + _R, '\x00', blanked, flags=re.IGNORECASE)
    description = re.sub(r'\x00+', ' ', blanked)
    description = re.sub(r'\s+', ' ', description).strip(' \t,.;:-')
    description = re.sub(r'^(?:' + conn + r')\s+', '', description, flags=re.IGNORECASE).strip()

    # 7. Category: user's own category named in the text > learned hint > rules/history.
    category = 'Other'
    category_conf = 'low'
    category_found = False
    desc_lower = description.lower()
    if user_categories:
        for cat in user_categories:
            if cat.lower() in desc_lower:
                category, category_conf, category_found = cat, 'high', True
                is_clue_found = True
                break

    hint = None
    for tok in keyword_candidates(description or text):
        if tok in hints and (hint is None or hints[tok].get('count', 0) > hint.get('count', 0)):
            hint = hints[tok]
    if hint and not category_found and hint.get('category'):
        category, category_conf, category_found = hint['category'], 'high', True
        is_clue_found = True

    if not category_found and description:
        predicted = predict_category_ai(description, user=user, categories=user_categories, skip_genai=skip_genai)
        if predicted:
            category, category_conf = predicted, 'medium'
            is_clue_found = True
    confidence['category'] = category_conf

    # Learned account / payment method fill the gaps the text left open.
    if hint:
        if not account and hint.get('account_id'):
            account = next((a for a in account_info if a['id'] == hint['account_id']), None)
            if account:
                confidence['account'] = 'medium'
        if not payment_method and not payment_ambiguous and hint.get('payment_method'):
            payment_method = hint['payment_method']
            confidence['payment_method'] = 'medium'

    if not description:
        description = 'Expense'
    else:
        description = description[0].upper() + description[1:]

    check = [f for f, c in confidence.items() if c == 'low' and f != 'currency']
    if amount is None and 'amount' not in check:
        check.append('amount')

    return {
        'amount': str(amount) if amount else None,
        'currency': currency or default_currency,
        'currency_found': bool(currency),
        'category': category,
        'description': description,
        'account': account['name'] if account else None,
        'account_id': account['id'] if account else None,
        'payment_method': payment_method,
        'payment_ambiguous': payment_ambiguous,
        'date': date.isoformat(),
        'date_found': parsed_date is not None,
        'success': amount is not None,
        'is_clue_found': is_clue_found,
        'confidence': confidence,
        'check': check,
    }
