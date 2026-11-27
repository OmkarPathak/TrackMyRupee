"""The guide describes the loan top-up, the "count all loan payments as spending" switch and the rules for a deposit's
money in and out, in English, Hindi and Marathi, and the links added for them go somewhere."""
import re
from pathlib import Path

from django.test import SimpleTestCase

GUIDE = Path(__file__).resolve().parents[2] / 'guide-src'
SUFFIXES = ('', '.hi', '.mr')

NEEDLES = {
    '08-loans/index': ['Top up', 'Received in account', 'Months added to the term', 'Loan received', 'Add the loan amount to an account'],
    '11-analytics-and-health/index': ['Count all loan payments as spending', 'Net Remaining'],
    '15-dashboard/index': ['Count all loan payments as spending'],
    '02-accounts/index': ['PPF, EPF, VPF, SSY, Post Office, APY', 'Expected', 'Installments'],
}


def slug(heading):
    text = re.sub(r'[*_`]', '', heading).strip().lower()
    return re.sub(r'[\s]+', '-', re.sub(r'[^\w\s-]', '', text))


class GuidePagesTests(SimpleTestCase):
    def test_every_language_describes_each_change(self):
        for page, needles in NEEDLES.items():
            for suffix in SUFFIXES:
                text = (GUIDE / f'{page}{suffix}.md').read_text()
                for needle in needles:
                    self.assertIn(needle, text, (page, suffix, needle))

    def test_the_english_pages_explain_the_rules_in_words(self):
        loans = (GUIDE / '08-loans/index.md').read_text()
        self.assertIn('### Topping up a loan', loans)
        self.assertIn('### Where the money you received shows', loans)
        accounts = (GUIDE / '02-accounts/index.md').read_text()
        self.assertIn('Closing a deposit', accounts)
        self.assertIn('Recurring Deposits do not ask for a principal', accounts)
        analytics = (GUIDE / '11-analytics-and-health/index.md').read_text()
        self.assertIn('### Counting all loan payments as spending', analytics)

    def test_the_savings_rule_no_longer_says_principal_is_never_spending(self):
        text = (GUIDE / '11-analytics-and-health/index.md').read_text()
        self.assertIn('Loan principal is not spending, by default.', text)
        self.assertNotIn('- **Loan principal is not spending.**', text)

    def test_the_links_added_to_these_pages_resolve(self):
        link = re.compile(r'\]\((\.\./[^)#]+\.md|[^)#:]+\.md)(#[^)]*)?\)')
        for page in NEEDLES:
            for suffix in SUFFIXES:
                source = GUIDE / f'{page}{suffix}.md'
                for target, anchor in link.findall(source.read_text()):
                    destination = (source.parent / target).resolve()
                    self.assertTrue(destination.exists(), (source.name, target))
                    if anchor and destination.name.endswith('index.md') and not suffix:
                        headings = {slug(h) for h in re.findall(r'^#{1,6}\s+(.+)$', destination.read_text(), re.M)}
                        self.assertIn(anchor.lstrip('#'), headings, (source.name, target, anchor))
