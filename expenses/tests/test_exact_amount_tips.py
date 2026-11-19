"""A rounded amount (2.3L, or 14,792 for 14,791.67) carries its exact value, which a tap or click shows in a tooltip.

An amount that is shown in full stays plain text. Python code that builds sentences keeps plain text too."""
import glob
import re
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.contrib.auth.models import User
from django.template import Context, Template
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import timezone, translation
from django.utils.safestring import SafeData

from expenses.models import Account, Expense, Income, UserProfile
from expenses.templatetags.digit_filters import compact_amount, compact_money, with_exact_tip

D = Decimal


def render(source, **context):
    return Template('{% load digit_filters %}' + source).render(Context(context)).strip()


def tip(exact, shown):
    return (f'<span class="tmr-amt" data-exact="{exact}" aria-label="{exact}" tabindex="0" role="button">{shown}</span>')


class FilterTests(SimpleTestCase):
    def test_an_abbreviated_amount_carries_its_exact_value(self):
        self.assertEqual(render('{{ v|compact_amount:"₹" }}', v=231233), tip('₹2,31,233', '2.3L'))

    def test_a_crore(self):
        self.assertEqual(render('{{ v|compact_amount:"₹" }}', v=12500000), tip('₹1,25,00,000', '1.2Cr'))

    def test_an_amount_shown_in_full_stays_plain(self):
        self.assertEqual(render('{{ v|compact_amount:"₹" }}', v=4500), '4,500')
        self.assertEqual(render('{{ v|compact_amount:"₹" }}', v=99999), '99,999')
        self.assertEqual(render('{{ v|compact_amount:"₹" }}', v=0), '0')

    def test_paise_that_are_not_shown_are_in_the_tooltip(self):
        self.assertEqual(render('{{ v|compact_amount:"₹" }}', v=D('14791.67')), tip('₹14,791.67', '14,792'))

    def test_an_amount_that_rounds_to_zero_still_has_its_exact_value(self):
        self.assertEqual(render('{{ v|compact_amount:"₹" }}', v=D('0.4')), tip('₹0.40', '0'))

    def test_exactly_a_lakh(self):
        self.assertEqual(render('{{ v|compact_amount:"₹" }}', v=100000), tip('₹1,00,000', '1L'))

    def test_a_negative_amount(self):
        self.assertEqual(render('{{ v|compact_amount:"₹" }}', v=-231233), tip('-₹2,31,233', '-2.3L'))

    def test_other_currencies_group_in_threes(self):
        self.assertEqual(render('{{ v|compact_amount:"$" }}', v=2500000), tip('$2,500,000', '2.5M'))

    def test_compact_money_carries_the_symbol_and_the_sign_in_front(self):
        self.assertEqual(render('{{ v|compact_money:"₹" }}', v=-150000), tip('-₹1,50,000', '-₹1.5L'))
        self.assertEqual(render('{{ v|compact_money:"₹" }}', v=D('-59772.40')), tip('-₹59,772.40', '-₹59,772'))

    def test_what_is_not_a_number_is_left_alone(self):
        self.assertEqual(render('{{ v|compact_amount:"₹" }}', v='n/a'), 'n/a')
        self.assertEqual(with_exact_tip('x', float('inf')), 'x')
        self.assertEqual(with_exact_tip('x', float('nan')), 'x')
        self.assertEqual(with_exact_tip('x', None), 'x')

    def test_the_result_is_markup_not_text_to_be_escaped(self):
        self.assertIn('<span class="tmr-amt"', render('{{ v|compact_amount:"₹"|translate_digits }}', v=231233))

    def test_a_currency_with_markup_in_it_is_escaped(self):
        html = render('{{ v|compact_amount:c }}', v=231233, c='"><b>x</b>')
        self.assertNotIn('<b>', html)
        self.assertNotIn('"><b', html)

    def test_python_callers_get_plain_text(self):
        self.assertEqual(compact_amount(231233, '₹'), '2.3L')
        self.assertEqual(compact_money(-150000, '₹'), '-₹1.5L')
        self.assertNotIsInstance(compact_amount(231233, '₹'), SafeData)

    def test_the_boundaries_of_what_counts_as_rounded(self):
        self.assertNotIn('tmr-amt', render('{{ v|compact_amount:"₹" }}', v=99999))
        self.assertIn('tmr-amt', render('{{ v|compact_amount:"₹" }}', v=D('99999.99')))
        self.assertIn('tmr-amt', render('{{ v|compact_amount:"₹" }}', v=100000))


class DigitTests(SimpleTestCase):
    def test_hindi_digits_are_translated_in_the_text_and_the_exact_value_but_not_in_the_tags(self):
        with translation.override('hi'):
            html = render('{{ v|compact_amount:"₹"|translate_digits }}', v=231233)
        self.assertIn('data-exact="₹२,३१,२३३"', html)
        self.assertIn('aria-label="₹२,३१,२३३"', html)
        self.assertIn('>२.३L</span>', html)
        self.assertIn('tabindex="0"', html)                      # (the markup keeps its own digits)
        self.assertIn('class="tmr-amt"', html)

    def test_marathi_works_the_same_way(self):
        with translation.override('mr'):
            html = render('{{ v|compact_amount:"₹"|translate_digits }}', v=D('14791.67'))
        self.assertIn('data-exact="₹१४,७९१.६७"', html)
        self.assertIn('>१४,७९२</span>', html)

    def test_plain_text_is_translated_as_before(self):
        with translation.override('hi'):
            self.assertEqual(render('{{ v|translate_digits }}', v='12,500'), '१२,५००')
            self.assertEqual(render('{{ v|compact_amount:"₹"|translate_digits }}', v=4500), '४,५००')

    def test_english_leaves_markup_untouched(self):
        with translation.override('en'):
            html = render('{{ v|compact_amount:"₹"|translate_digits }}', v=231233)
        self.assertEqual(html, tip('₹2,31,233', '2.3L'))


class TemplateAuditTests(SimpleTestCase):
    """The amount filters now give markup, which only works in the text of a page: never inside a tag's attribute or a
    script, where it would break the page."""

    def test_no_amount_filter_is_used_inside_a_tag_or_a_script(self):
        wrong = []
        for path in glob.glob(str(Path(settings.BASE_DIR) / 'templates' / '**' / '*.html'), recursive=True):
            source = Path(path).read_text(encoding='utf-8')
            for match in re.finditer(r'\|\s*compact_(amount|money)\b', source):
                before = re.sub(r'\{\{.*?\}\}|\{%.*?%\}|\{#.*?#\}', '', source[:match.start()], flags=re.S)
                in_tag = before.rfind('<') > before.rfind('>')
                in_script = max(before.rfind('<script'), before.rfind('<style')) > max(before.rfind('</script'), before.rfind('</style'))
                if in_tag or in_script:
                    wrong.append(f'{Path(path).name}:{source.count(chr(10), 0, match.start()) + 1}')
        self.assertEqual(wrong, [], 'these would put markup inside an attribute or a script: ' + ', '.join(wrong))

    def test_every_use_is_followed_by_translate_digits_so_the_markup_stays_safe_in_hindi(self):
        wrong = []
        for path in glob.glob(str(Path(settings.BASE_DIR) / 'templates' / '**' / '*.html'), recursive=True):
            for number, line in enumerate(Path(path).read_text(encoding='utf-8').splitlines(), 1):
                for match in re.finditer(r'\{\{[^}]*\|\s*compact_(amount|money)\b[^}]*\}\}', line):
                    if 'translate_digits' not in match.group(0):
                        wrong.append(f'{Path(path).name}:{number}')
        self.assertEqual(wrong, [])


class PageTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('tips', password='pw')
        UserProfile.objects.filter(user=self.user).update(tier='PRO', has_seen_tutorial=True, consent_granted=True,
                                                          currency='₹')
        self.client.force_login(self.user)
        self.bank = Account.objects.create(user=self.user, name='Big Bank', account_type='SAVINGS_ACCOUNT',
                                           balance=D('231233'), currency='₹')

    def spend(self, amount, description='Spend'):
        return Expense.objects.create(user=self.user, category='Food', amount=D(amount), base_amount=D(amount),
                                      currency='₹', date=timezone.localdate(), account=self.bank, description=description)

    def test_the_transactions_list_shows_the_exact_amount_of_a_rounded_one(self):
        self.spend('231233')
        html = self.client.get(reverse('all-transactions')).content.decode()
        self.assertIn('data-exact="₹2,31,233"', html)

    def test_a_small_amount_in_the_list_stays_plain(self):
        self.spend('450', 'Tea')
        html = self.client.get(reverse('all-transactions')).content.decode()
        self.assertNotIn('data-exact="₹450"', html)

    def test_the_dashboard_cards_show_the_exact_amount_of_a_rounded_one(self):
        Income.objects.create(user=self.user, amount=D('231233'), base_amount=D('231233'), currency='₹',
                              date=timezone.localdate(), account=self.bank, source_type='Salary', source='Salary')
        self.spend('10')
        html = self.client.get(reverse('home')).content.decode()
        self.assertIn('data-exact="₹2,31,233"', html)

    def test_the_list_a_filter_reloads_has_it_too(self):
        self.spend('231233')
        html = self.client.get(reverse('all-transactions'), HTTP_HX_REQUEST='true').content.decode()
        self.assertIn('data-exact="₹2,31,233"', html)

    def test_every_page_loads_the_script_that_shows_it(self):
        html = self.client.get(reverse('all-transactions')).content.decode()
        self.assertIn('js/exact_amounts.js', html)

    def test_in_hindi_the_exact_amount_is_in_hindi_digits(self):
        UserProfile.objects.filter(user=self.user).update(language='hi')
        self.spend('231233')
        html = self.client.get(reverse('all-transactions')).content.decode()
        self.assertIn('data-exact="₹२,३१,२३३"', html)
