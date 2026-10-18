"""Telling that a statement row is something already recorded, or is not spending at all.

Two judgements, both pure functions so each rule can be pinned down in a small test:
  * a row that is probably an expense the person already typed in, though not word for word (a bank writes
    "UPI-SWIGGY-9876@ybl" where they wrote "Swiggy dinner"): same amount, within two days, and a shared word;
  * a row that is probably money moved between the person's own accounts (a credit card bill paid, a "self"
    transfer): it names one of their other accounts, and says it is a transfer.

Neither judgement is trusted to act alone. A possible duplicate is shown unticked and a detected transfer is
shown with the account it was matched to, so the person decides in the preview.
"""
import re
from dataclasses import dataclass
from typing import Dict, FrozenSet, Iterable, List, Optional, Tuple

DATE_WINDOW_DAYS = 2
SIMILARITY_THRESHOLD = 0.5

# Words a bank adds to a narration that say nothing about who was paid.
_NOISE = frozenset("""
upi pos neft imps rtgs ach ecs nach mandate txn ref payment pay paid to from bank ltd limited pvt india online
transfer trf debit credit card the and for via by
ybl okaxis okhdfcbank okicici oksbi paytm apl ibl axl ptyes ptsbi
""".split())

# What an account's own name says about every account, so it cannot tell two accounts apart.
_GENERIC_ACCOUNT_WORDS = frozenset("""
bank account acct ac savings saving salary current card credit debit loan wallet cash fd rd demat ltd limited
of the my and payments pay
""".split())

_STRONG_TRANSFER = re.compile(
    r"(?i)\b(self|own account|my account|cc payment|cc pmt|ccpayment|credit card|card bill|card payment|"
    r"card pmt|card dues|bill payment of card)\b")
_PLAIN_TRANSFER = re.compile(r"(?i)\b(transfer|trf|fund transfer|ft|tpt)\b")
_WORD = re.compile(r"[a-z0-9]+")


def meaningful_tokens(text) -> FrozenSet[str]:
    """The words of a description that identify who was paid: no numbers, no bank boilerplate, no short bits."""
    return frozenset(
        t for t in _WORD.findall(str(text or '').lower())
        if len(t) >= 3 and not t.isdigit() and t not in _NOISE
    )


def _same_word(a, b):
    if a == b:
        return True
    return len(a) >= 4 and len(b) >= 4 and (a.startswith(b) or b.startswith(a))


def similarity(a: FrozenSet[str], b: FrozenSet[str]) -> float:
    """How much of the shorter description appears in the other, 0 to 1."""
    if not a or not b:
        return 0.0
    shared = sum(1 for x in a if any(_same_word(x, y) for y in b))
    return shared / min(len(a), len(b))


@dataclass
class Candidate:
    """An expense already stored, as the matcher needs it."""
    id: int
    date: object
    amount: object
    description: str
    tokens: FrozenSet[str]
    used: bool = False


def best_match(date, amount, tokens, pool: Iterable[Candidate], window=DATE_WINDOW_DAYS,
               threshold=SIMILARITY_THRESHOLD) -> Optional[Candidate]:
    """The stored expense this row most probably is, or None. Same amount, within `window` days, and enough of
    the description in common. Each stored expense can account for one row only (`used`)."""
    best, best_key = None, None
    for c in pool:
        if c.used or c.amount != amount:
            continue
        days = abs((c.date - date).days)
        if days > window:
            continue
        score = similarity(tokens, c.tokens)
        if score < threshold:
            continue
        key = (score, -days)
        if best_key is None or key > best_key:
            best, best_key = c, key
    return best


@dataclass
class AccountNames:
    id: int
    name: str
    tokens: FrozenSet[str]


def account_names(accounts) -> List[AccountNames]:
    out = []
    for account in accounts:
        words = frozenset(t for t in _WORD.findall(account.name.lower())
                          if len(t) >= 3 and not t.isdigit() and t not in _GENERIC_ACCOUNT_WORDS)
        out.append(AccountNames(account.id, account.name, words))
    return out


def looks_like_transfer(description) -> bool:
    """True when the narration says it moves money (a transfer, a card bill, "self"), whoever it names."""
    text = str(description or '')
    return bool(_STRONG_TRANSFER.search(text) or _PLAIN_TRANSFER.search(text))


def find_transfer_target(description, accounts: List[AccountNames]) -> Optional[int]:
    """The id of the person's own account this narration moves money to, or None when it is not clearly one.

    Needs the narration to say it is a transfer AND to name exactly one of the accounts: every distinctive word of
    the account's name must be in the narration, and a plain "transfer" is enough only when the name has two such
    words ("HDFC Regalia"), while a "self" / "credit card" / "card bill" marker is enough with one ("HDFC")."""
    text = str(description or '')
    words = frozenset(_WORD.findall(text.lower()))
    strong = bool(_STRONG_TRANSFER.search(text))
    plain = bool(_PLAIN_TRANSFER.search(text))
    if not (strong or plain):
        return None
    matches: List[Tuple[int, int]] = []             # (distinctive words matched, account id)
    for account in accounts:
        if not account.tokens or not account.tokens <= words:
            continue
        if len(account.tokens) >= 2 or strong:
            matches.append((len(account.tokens), account.id))
    if not matches:
        return None
    matches.sort(reverse=True)
    if len(matches) > 1 and matches[0][0] == matches[1][0]:
        return None                                 # two accounts fit equally well: do not guess
    return matches[0][1]
