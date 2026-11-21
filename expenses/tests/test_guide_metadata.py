"""The user guide (guide-src, published at docs.trackmyrupee.com): every page, in all three languages, has its own
description for search results, the front matter parses, and search engines are pointed at the sitemap."""
from pathlib import Path

from django.test import SimpleTestCase

GUIDE = Path(__file__).resolve().parents[2] / 'guide-src'
PAGES = sorted(p for p in GUIDE.rglob('*.md') if 'screenshots' not in p.parts)


def leading_block(text):
    """The lines of a page's front matter: between --- lines, or (the other style mkdocs reads) up to the first blank line."""
    lines = text.split('\n')
    if lines and lines[0].strip() == '---':
        end = lines.index('---', 1) if '---' in lines[1:] else len(lines)
        return lines[1:end]
    block = []
    for line in lines:
        if not line.strip():
            break
        block.append(line)
    return block


def fields(text):
    return {line.split(':', 1)[0]: line.split(':', 1)[1].strip() for line in leading_block(text)
            if ':' in line and not line.startswith('#')}


def language_of(page):
    return page.suffixes[-2][1:] if len(page.suffixes) > 1 else 'en'


class GuideMetadataTests(SimpleTestCase):
    def test_there_are_pages_to_check(self):
        self.assertGreater(len(PAGES), 60)

    def test_every_page_has_a_description_of_its_own(self):
        missing = [str(p.relative_to(GUIDE)) for p in PAGES if not fields(p.read_text(encoding='utf-8')).get('description')]
        self.assertEqual(missing, [], 'these pages show the site-wide description in search results')

    def test_descriptions_are_a_sensible_length(self):
        for page in PAGES:
            description = fields(page.read_text(encoding='utf-8')).get('description', '').strip('"')
            self.assertTrue(40 <= len(description) <= 320, f'{page.relative_to(GUIDE)}: {len(description)} characters')

    def test_an_unquoted_value_with_a_colon_would_not_parse(self):
        """(YAML reads "key: a: b" as an error, and mkdocs then drops the page's metadata silently)"""
        bad = []
        for page in PAGES:
            for key, value in fields(page.read_text(encoding='utf-8')).items():
                if value and value[0] not in '"\'|>' and ': ' in value:
                    bad.append(f'{page.relative_to(GUIDE)}: {key}')
        self.assertEqual(bad, [])

    def test_descriptions_are_not_shared_between_pages_of_a_language(self):
        seen = {}
        for page in PAGES:
            key = (language_of(page), fields(page.read_text(encoding='utf-8')).get('description'))
            self.assertNotIn(key, seen, f'{page.name} repeats {seen.get(key)}')
            seen[key] = page.name

    def test_search_engines_are_pointed_at_the_sitemap_and_nothing_is_blocked(self):
        robots = (GUIDE / 'robots.txt').read_text()
        self.assertIn('Sitemap: https://docs.trackmyrupee.com/sitemap.xml', robots)
        self.assertNotIn('Disallow', robots)
