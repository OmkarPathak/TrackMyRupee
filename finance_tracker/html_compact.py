"""Strip the indentation and blank lines Django templates leave in HTML responses.

Templates here are deeply indented and `{% if %}`/`{% for %}` tags leave blank lines behind, which is up to half the
bytes of a page (`/recurring/` was 516 KB, ~250 KB of it whitespace). Only whitespace at line starts and runs of
blank lines are touched: a newline stays a newline, so the browser collapses it exactly as before. `<script>`,
`<style>`, `<pre>` and `<textarea>` are left alone because whitespace can be meaningful in them.
"""
import re

PROTECTED = re.compile(r'(<(script|style|pre|textarea)\b.*?</\2\s*>)', re.S | re.I)
INDENT_AND_BLANKS = re.compile(r'[ \t]*\n\s*')
TRAILING = re.compile(r'[ \t]+\n')


def compact_html(html):
    parts = PROTECTED.split(html)
    # split() with two groups yields: text, whole block, tag name, text, ...
    out = []
    for i in range(0, len(parts), 3):
        out.append(INDENT_AND_BLANKS.sub('\n', parts[i]))
        if i + 1 < len(parts):
            out.append(parts[i + 1])
    return ''.join(out)


class CompactHtmlMiddleware:
    """Must sit below GZipMiddleware so the compressed body is the compacted one."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if (
            response.streaming
            or response.status_code != 200
            or not response.get('Content-Type', '').startswith('text/html')
            or response.has_header('Content-Encoding')
        ):
            return response
        response.content = compact_html(response.content.decode(response.charset))
        if response.has_header('Content-Length'):
            response['Content-Length'] = str(len(response.content))
        return response
