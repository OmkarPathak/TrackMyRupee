"""The comparison line under each Dashboard metric card is drawn the same way for every card: the currency symbol sits against
the amount (a rounded amount used to get a gap), a change of nothing reads the same everywhere, and percentages are signed alike."""
import re
from decimal import Decimal

from django.template import Context, Template
from django.test import SimpleTestCase

D = Decimal


def render(diff, pct, good_when='up', label='vs last month', currency='₹'):
    template = Template("{% load bento_tags %}{% bento_delta diff pct label currency good_when %}")
    return template.render(Context({'diff': diff, 'pct': pct, 'label': label, 'currency': currency, 'good_when': good_when}))


class LineTests(SimpleTestCase):
    def test_a_rise_in_something_good_is_green_with_an_up_arrow(self):
        html = render(D('260000'), D('4.3'))
        self.assertIn('text-success', html)
        self.assertIn('bi-caret-up-fill', html)
        self.assertIn('+4.3%', html)

    def test_a_fall_in_something_good_is_red_with_a_down_arrow(self):
        html = render(D('-68000'), D('-18.4'))
        self.assertIn('text-danger', html)
        self.assertIn('bi-caret-down-fill', html)
        self.assertIn('-18.4%', html)
        self.assertIn('₹68,000', html)               # (the amount has no sign: the arrow says which way)

    def test_for_spending_a_rise_is_red_and_a_fall_is_green_but_the_arrow_still_follows_the_number(self):
        more = render(D('7603'), D('20.0'), good_when='down')
        less = render(D('-7603'), D('-20.0'), good_when='down')
        self.assertIn('text-danger', more)
        self.assertIn('bi-caret-up-fill', more)
        self.assertIn('text-success', less)
        self.assertIn('bi-caret-down-fill', less)

    def test_no_change_reads_the_same_whichever_card_it_is(self):
        for good_when in ('up', 'down'):
            html = render(D('0'), D('0'), good_when=good_when)
            self.assertIn('No change', html)
            self.assertNotIn('bi-caret', html)
            self.assertIn('vs last month', html)

    def test_nothing_at_all_counts_as_no_change(self):
        self.assertIn('No change', render(None, None))

    def test_a_huge_change_reads_ten_x(self):
        self.assertIn('10x+', render(D('50000'), D('1500')))
        self.assertIn('10x+', render(D('-50000'), D('-1500')))

    def test_a_missing_percentage_shows_no_badge(self):
        self.assertNotIn('badge', render(D('500'), None))
        self.assertNotIn('badge', render(D('500'), D('0')))

    def test_the_label_is_shown_every_time(self):
        for diff in (D('5'), D('-5'), D('0')):
            self.assertIn('this month', render(diff, D('1'), label='this month'))

    def test_the_symbol_and_a_rounded_amount_are_one_piece(self):
        """A rounded amount is wrapped in a tooltip span; with the symbol outside it, the flex gap showed "₹ 2.6L"."""
        html = render(D('260000'), D('4.3'))
        self.assertRegex(html, r'<span>₹<span class="tmr-amt"[^>]*>2\.6L</span></span>')
        self.assertNotRegex(html, r'₹\s+<')

    def test_a_whole_amount_is_just_text_beside_its_symbol(self):
        self.assertIn('<span>₹68,000</span>', render(D('-68000'), D('-18.4')))

    def test_another_currency_symbol_is_used_the_same_way(self):
        self.assertIn('<span>$500</span>', render(D('500'), D('2.0'), currency='$'))
