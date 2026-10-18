"""Build a trimmed copy of the vendored Bootstrap stylesheet.

The full bootstrap.min.css is ~230 KB and blocks first paint on every page. This keeps only the rules whose
class names appear somewhere in the project's own templates, scripts and Python (plus a safelist of the state classes Bootstrap's JS adds
at runtime and of the classes for markup that lives in the database, such as blog posts).

Regenerate with `python manage.py purge_css`; a test fails when the committed file is out of date, so a newly used
class can never silently lose its styling.
"""
import re
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
SOURCE_CSS = BASE / 'static/vendor/bootstrap/bootstrap.min.css'
OUTPUT_CSS = BASE / 'static/vendor/bootstrap/bootstrap.purged.min.css'

SCAN_DIRS = ['templates', 'static/js', 'expenses', 'blog', 'finance_tracker']
SCAN_FILES = ['static/style.css', 'static/skeleton-loader.css', 'static/css/expense_toast.css']
SCAN_SUFFIXES = {'.html', '.js', '.py', '.css', '.txt', '.json'}
SKIP_PARTS = {'tests', 'migrations', 'management', '__pycache__'}

# Markup stored in the database (blog posts, admin announcements) cannot be scanned, so keep the families an author
# is likely to reach for.
SAFELIST = re.compile(
    r'^(table|alert|img-|blockquote|lead$|list-unstyled|list-inline|display-|figure|ratio|small$|mark$|'
    r'text-(start|end|center|muted|primary|success|danger|warning|info|secondary|dark|body)$|fw-|fst-|'
    r'rounded$|shadow(-sm)?$|border$|table-responsive|btn$|btn-(primary|secondary|outline-primary)$|'
    r'collaps|fade$|show$|showing$|hiding$|active$|disabled$|d-(none|block|flex)$|'
    r'(bs-)?(tooltip|popover)|modal-(open|static|backdrop)$|dropdown-(menu|backdrop)$)'
)

TOKEN = re.compile(r'[A-Za-z0-9_-]+')
CLASS_IN_SELECTOR = re.compile(r'\.(-?[_a-zA-Z][\w-]*|\\[^\s,{>+~.:#\[)]+[\w\\-]*)')


def _source_files():
    for name in SCAN_DIRS:
        root = BASE / name
        for path in sorted(root.rglob('*')):
            if path.is_file() and path.suffix in SCAN_SUFFIXES and not (SKIP_PARTS & set(path.parts)):
                yield path
    for name in SCAN_FILES:
        yield BASE / name


def used_tokens():
    tokens = set()
    for path in _source_files():
        tokens.update(TOKEN.findall(path.read_text(encoding='utf-8', errors='ignore')))
    return tokens


def _split_top_level(text, sep=','):
    parts, depth, cur = [], 0, []
    for ch in text:
        if ch in '([':
            depth += 1
        elif ch in ')]':
            depth -= 1
        if ch == sep and depth == 0:
            parts.append(''.join(cur))
            cur = []
        else:
            cur.append(ch)
    parts.append(''.join(cur))
    return parts


def _unescape(name):
    return re.sub(r'\\(.)', r'\1', name)


def _without_negations(selector):
    """Drop `:not(...)` groups: a class that must be absent never needs to exist for the rule to apply."""
    out, i = [], 0
    while i < len(selector):
        if selector.startswith(':not(', i):
            depth, i = 1, i + 5
            while i < len(selector) and depth:
                depth += {'(': 1, ')': -1}.get(selector[i], 0)
                i += 1
        else:
            out.append(selector[i])
            i += 1
    return ''.join(out)


def _selector_kept(selector, tokens, prefixes):
    for raw in CLASS_IN_SELECTOR.findall(_without_negations(selector)):
        name = _unescape(raw)
        if name in tokens or SAFELIST.match(name):
            continue
        # Responsive/utility classes such as `col-md-6` or `mt-lg-3` are found by their pieces, and
        # classes built at runtime (`'bg-' + colour`) by the dangling prefix.
        if any(name.startswith(p) for p in prefixes):
            continue
        return False
    return True


def _blocks(css):
    """Yield (prelude, body) for each top-level block; body is None for statements like @charset."""
    i, n = 0, len(css)
    while i < n:
        j = i
        while j < n and css[j] not in '{;':
            j += 1
        if j >= n:
            break
        prelude = css[i:j]
        if css[j] == ';':
            yield prelude, None
            i = j + 1
            continue
        depth, k = 1, j + 1
        while k < n and depth:
            depth += {'{': 1, '}': -1}.get(css[k], 0)
            k += 1
        yield prelude, css[j + 1:k - 1]
        i = k


def _filter(css, tokens, prefixes):
    out = []
    for prelude, body in _blocks(css):
        stripped = prelude.strip()
        if body is None:
            out.append(f'{stripped};')
        elif stripped.startswith(('@media', '@supports', '@layer')):
            inner = _filter(body, tokens, prefixes)
            if inner:
                out.append(f'{stripped}{{{inner}}}')
        elif stripped.startswith('@'):
            out.append(f'{stripped}{{{body}}}')
        else:
            kept = [s for s in _split_top_level(stripped) if _selector_kept(s, tokens, prefixes)]
            if kept:
                out.append(f'{",".join(kept)}{{{body}}}')
    return ''.join(out)


def build():
    tokens = used_tokens()
    prefixes = {t for t in tokens if t.endswith('-') and len(t) > 2}
    css = re.sub(r'/\*.*?\*/', '', SOURCE_CSS.read_text(encoding='utf-8'), flags=re.S)
    return _filter(css, tokens, prefixes) + '\n'
