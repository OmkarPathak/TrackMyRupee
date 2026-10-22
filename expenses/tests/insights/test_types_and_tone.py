import re
from datetime import date

from django.test import SimpleTestCase
from django.urls import resolve
from django.utils import translation
from django.utils.translation import pgettext

from expenses.insights import msgids, registry, tone
from expenses.insights.sampling import SampleContext
from expenses.insights.types import (
    Action,
    Evidence,
    Insight,
    Kind,
    Module,
    Plan,
    Surface,
    Tone,
    sentence_count,
)

PLACEHOLDER = re.compile(r'%\([^)]*\)[sdifrx]')
DEVANAGARI = re.compile(r'[ऀ-ॿ]')


def make(**over):
    base = dict(rule_id='cashflow.test', module=Module.CASHFLOW, kind=Kind.OBSERVATION, tone=Tone.CALM, priority=50,
                title='A calm title', message='One sentence. Another one.', dedupe_key='2026-10',
                valid_until=date(2026, 10, 31), surfaces=frozenset({Surface.DASHBOARD}))
    base.update(over)
    return Insight(**base)


class InsightValidationTests(SimpleTestCase):
    def test_a_valid_insight_round_trips_through_its_payload(self):
        insight = make(evidence=(Evidence('Spent', '₹1,000', '/transactions/'),), action=Action('Open', '/budget/'),
                       plan=Plan.PLUS, detail='If it is on purpose, you can dismiss this.')
        self.assertEqual(Insight.from_payload(insight.to_payload()), insight)

    def test_title_is_at_most_eight_words(self):
        make(title='one two three four five six seven eight')
        with self.assertRaises(ValueError):
            make(title='one two three four five six seven eight nine')

    def test_message_is_at_most_two_sentences(self):
        with self.assertRaises(ValueError):
            make(message='One. Two. Three.')

    def test_decimals_and_amounts_do_not_count_as_sentence_breaks(self):
        self.assertEqual(sentence_count('About ₹9.2k, up 4.5% from last time. That is fine.'), 2)
        self.assertEqual(sentence_count('यह एक वाक्य है। यह दूसरा है।'), 2)

    def test_links_must_be_site_relative(self):
        for bad in ('https://evil.example/', '//evil.example/', 'javascript:alert(1)'):
            with self.assertRaises(ValueError):
                make(action=Action('Go', bad))
        with self.assertRaises(ValueError):
            make(evidence=(Evidence('x', 'y', 'https://evil.example/'),))

    def test_a_sensitive_insight_has_no_share_text_and_is_never_pushed(self):
        make(sensitive=True)
        with self.assertRaises(ValueError):
            make(sensitive=True, share_text='look')
        with self.assertRaises(ValueError):
            make(sensitive=True, surfaces=frozenset({Surface.PUSH}))

    def test_priority_is_zero_to_one_hundred(self):
        with self.assertRaises(ValueError):
            make(priority=101)


class ToneLinterTests(SimpleTestCase):
    def test_flags_each_kind_of_problem(self):
        for text, expected in [
            ('This is a warning', 'banned word'), ('You must act', 'banned word'), ('You need to act', 'banned word'),
            ('Acting immediately helps', 'banned word'), ('Dangerously high', 'banned word'), ('So wasted', 'banned word'),
            ('A bad month', 'banned word'), ('Careless', 'banned word'), ('Overspent', 'banned word'),
            ('You failed', 'banned word'), ('Urgent', 'banned word'), ('Well done!', 'exclamation'),
            ('This is URGENT', 'ALL CAPS'), ('Nice 🎉', 'emoji'), ('Hourglass ⏳', 'emoji'),
        ]:
            self.assertTrue(any(expected in problem for problem in tone.check(text)), text)

    def test_calm_words_and_short_acronyms_pass(self):
        for text in ('A little higher', 'worth a look when you have a moment', 'Your EMI and SIP are on time',
                     'Bank FD matures on 3 Nov', 'About ₹9.2k, around 40%'):
            self.assertEqual(tone.check(text), [], text)


class EveryStringIsCalmAndTranslatedTests(SimpleTestCase):
    """Reads the source, so a branch no sample reaches is still checked."""

    def test_every_english_string_passes_the_tone_linter(self):
        problems = []
        for path, _ctx, msgid, plural in msgids.all_strings():
            for text in filter(None, (msgid, plural)):
                found = tone.check(text)
                if found:
                    problems.append(f'{path.rsplit("/", 1)[-1]}: {text!r}: {found}')
        self.assertEqual(problems, [])

    def test_every_string_has_hindi_and_marathi_that_keep_the_placeholders(self):
        missing = []
        for lang in ('hi', 'mr'):
            with translation.override(lang):
                for _path, ctx, msgid, _plural in msgids.all_strings():
                    got = pgettext(ctx, msgid) if ctx else translation.gettext(msgid)
                    if got == msgid:
                        missing.append((lang, msgid[:60]))
                    elif set(PLACEHOLDER.findall(got)) != set(PLACEHOLDER.findall(msgid)):
                        missing.append((lang, 'placeholders', msgid[:60]))
                    elif '!' in got or tone.EMOJI.search(got):
                        missing.append((lang, 'tone', msgid[:60]))
                    elif not DEVANAGARI.search(got):
                        missing.append((lang, 'not Devanagari', msgid[:60]))
        self.assertEqual(missing, [])


class EveryRuleSampleTests(SimpleTestCase):
    """Each rule's own sample, in English, Hindi and Marathi."""

    def run_rule(self, rule, lang):
        with translation.override(lang):
            ctx = rule.sample(lang)
            self.assertIsInstance(ctx, SampleContext)
            with ctx.declared(rule.needs):
                return rule.evaluate(ctx)

    def test_every_rule_says_something_calm_in_every_language(self):
        for rule in registry.all_rules():
            for lang in ('en', 'hi', 'mr'):
                out = self.run_rule(rule, lang)
                self.assertTrue(out, f'{rule.id} has nothing to say on its own sample ({lang})')
                for insight in out:
                    self.assertEqual(tone.check_insight(insight), [], (rule.id, lang))
                    self.assertEqual(insight.rule_id, rule.id)
                    self.assertLessEqual(len(insight.title.split()), 8)

    def test_every_link_leads_somewhere_real(self):
        for rule in registry.all_rules():
            for insight in self.run_rule(rule, 'en'):
                links = [e.link for e in insight.evidence if e.link] + ([insight.action.url] if insight.action else [])
                for link in links:
                    self.assertTrue(resolve(link.split('?')[0]), (rule.id, link))

    def test_hindi_and_marathi_use_devanagari_digits_and_words_for_amounts(self):
        pace = registry.get('cashflow.pace_vs_usual')
        for lang in ('hi', 'mr'):
            message = self.run_rule(pace, lang)[0].message
            self.assertRegex(message, '[०-९]')
            self.assertNotRegex(message, '[0-9]')
            self.assertIn('हज़ार' if lang == 'hi' else 'हजार', message)
