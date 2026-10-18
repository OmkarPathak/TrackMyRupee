from django.test import SimpleTestCase

from finance_tracker import css_purge


class PurgedBootstrapTests(SimpleTestCase):
    def test_committed_stylesheet_is_up_to_date(self):
        """A class added to a template must not lose its Bootstrap styling because the trimmed file is stale."""
        committed = css_purge.OUTPUT_CSS.read_text(encoding='utf-8')
        self.assertEqual(
            committed, css_purge.build(),
            'bootstrap.purged.min.css is out of date: run `python manage.py purge_css` and commit the result.',
        )

    def test_it_is_much_smaller_but_keeps_what_pages_and_scripts_need(self):
        committed = css_purge.OUTPUT_CSS.read_text(encoding='utf-8')
        self.assertLess(len(committed), css_purge.SOURCE_CSS.stat().st_size * 0.7)
        for needle in ('.btn{', '.modal{', '.dropdown-menu{', '.form-control{', '.modal-backdrop{',
                       '.tooltip{', '.collapsing{', '.fade:not(.show)', '[data-bs-theme=dark]'):
            self.assertIn(needle, committed)

    def test_runtime_and_dynamic_class_families_survive(self):
        committed = css_purge.OUTPUT_CSS.read_text(encoding='utf-8')
        # Built at runtime in templates/JS (`'bg-' + colour`) or added by Bootstrap's scripts.
        for needle in ('.bg-success', '.text-danger', '.btn-outline-danger', '.show>', '.bs-tooltip-auto'):
            self.assertIn(needle, committed)
