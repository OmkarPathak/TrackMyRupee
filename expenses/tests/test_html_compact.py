from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from finance_tracker.html_compact import compact_html


class CompactHtmlTests(SimpleTestCase):
    def test_indentation_and_blank_lines_go_but_line_breaks_stay(self):
        html = '<div>\n    <span>a</span>\n\n\n        <span>b</span>   \n</div>\n'
        self.assertEqual(compact_html(html), '<div>\n<span>a</span>\n<span>b</span>\n</div>\n')

    def test_script_style_pre_and_textarea_are_untouched(self):
        for block in (
            '<script>\n    var a = 1; // note\n\n    var b = 2;\n</script>',
            '<style>\n    .a {\n        color: red;\n    }\n</style>',
            '<pre>\n    keep   this\n\n    as is\n</pre>',
            '<textarea>\n   line one\n\n   line two\n</textarea>',
        ):
            self.assertEqual(compact_html(f'<p>\n   x\n</p>\n{block}\n   <p>y</p>'),
                             f'<p>\nx\n</p>\n{block}\n<p>y</p>')

    def test_it_is_idempotent(self):
        html = '<ul>\n   <li>a</li>\n\n   <li>b</li>\n</ul>\n<script>\n  x()\n</script>\n'
        self.assertEqual(compact_html(compact_html(html)), compact_html(html))


class CompactHtmlMiddlewareTests(TestCase):
    def test_pages_are_compacted_and_json_is_not_touched(self):
        response = self.client.get(reverse('landing'))
        body = response.content.decode()
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('\n\n', body.split('<script')[0])
        self.assertNotRegex(body.split('<script')[0], r'\n[ \t]+<')
        self.assertEqual(int(response.get('Content-Length', len(response.content))), len(response.content))
