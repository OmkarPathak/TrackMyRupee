"""The "In one line" sentence at the top of the Spending tab, and Analytics' place in the menu."""
import re

from django.urls import reverse
from django.utils import translation

from expenses.analytics import spending
from expenses.tests.test_analytics_module import month_start, today
from expenses.tests.test_analytics_page import PageCase


class SpendLineTests(PageCase):
    def line(self, **params):
        return spending.build(self.page('spend', **params))['spend_line']

    def test_it_says_what_was_spent_and_which_category_is_the_biggest(self):
        self.income(100000)
        self.expense(30000, category='Food')
        self.expense(10000, category='Rent')
        text = self.line()
        self.assertIn('You spent ₹40,000 in this period', text)
        self.assertTrue(text.endswith('Food is the biggest part, at ₹30,000.'), text)

    def test_it_agrees_with_the_spending_tile(self):
        self.expense(12345, category='Food')
        self.expense(5000, category='Travel')
        data = spending.build(self.page('spend'))
        tile = next(t for t in data['tiles'] if t['key'] == 'spending')
        self.assertIn(tile['value'], data['spend_line'])

    def test_with_less_spending_than_the_comparison_it_says_so(self):
        self.expense(80000, when=month_start(1), category='Food')
        self.expense(40000, when=month_start(0), category='Food')
        text = self.line(time_period='this_month')
        self.assertIn('50% less than the same days last month', text)

    def test_with_more_spending_than_the_comparison_it_says_so(self):
        self.expense(40000, when=month_start(1), category='Food')
        self.expense(60000, when=month_start(0), category='Food')
        self.assertIn('50% more than the same days last month', self.line(time_period='this_month'))

    def test_with_about_the_same_spending_it_says_about_the_same(self):
        self.expense(40000, when=month_start(1), category='Food')
        self.expense(40000, when=month_start(0), category='Food')
        self.assertIn('about the same as the same days last month', self.line(time_period='this_month'))

    def test_with_nothing_to_compare_it_just_says_what_was_spent(self):
        self.expense(7000, category='Food')
        text = self.line()
        self.assertTrue(text.startswith('You spent ₹7,000 in this period'), text)
        self.assertNotIn('than', text)

    def test_one_category_alone_is_not_called_the_biggest_part(self):
        self.expense(7000, category='Food')
        self.assertNotIn('biggest part', self.line())

    def test_with_no_spending_there_is_no_sentence_and_no_card(self):
        self.income(50000)
        self.assertEqual(self.line(), '')
        response = self.tab()
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'In one line')

    def test_the_sentence_is_on_the_page_above_the_tiles(self):
        self.expense(30000, category='Food')
        self.expense(10000, category='Rent')
        html = self.tab().content.decode()
        self.assertIn('In one line', html)
        self.assertLess(html.index('In one line'), html.index('anx-tile'))

    def test_the_other_tabs_do_not_get_it(self):
        self.expense(30000, category='Food')
        self.assertNotContains(self.tab('analytics-cashflow'), 'In one line')
        self.assertNotContains(self.tab('analytics-networth'), 'In one line')

    def test_it_reads_in_hindi_and_marathi_without_leaving_placeholders(self):
        self.expense(30000, when=month_start(1), category='Food')
        self.expense(20000, when=month_start(0), category='Food')
        self.expense(5000, when=month_start(0), category='Rent')
        english = self.line()
        for lang in ('hi', 'mr'):
            with translation.override(lang):
                text = self.line()
            self.assertNotEqual(text, english, lang)
            self.assertTrue(re.search('[ऀ-ॿ]', text), (lang, text))
            self.assertNotIn('%(', text, lang)


class InsightsMenuTests(PageCase):
    def page_html(self):
        response = self.client.get(reverse('settings-home'))
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def test_analytics_sits_under_insights_not_overview(self):
        html = self.page_html()
        sidebar = html[html.index('id="sidebar"'):]
        overview = sidebar.index('>Overview<')
        cash_flow = sidebar.index('>Cash Flow<')
        insights = sidebar.index('>Insights<')
        link = sidebar.index(reverse('analytics'))
        self.assertFalse(overview < link < cash_flow, 'Analytics is still listed under Overview')
        self.assertLess(insights, link)
        self.assertLess(link, sidebar.index('>Settings<'))

    def test_on_the_phone_analytics_is_one_of_the_everyday_places(self):
        html = self.page_html()
        sheet = html[html.index('id="moreMenuSheet"'):]
        everyday = sheet.index('Everyday')
        money = sheet.index('Money & plans')
        self.assertLess(everyday, sheet.index(reverse('analytics')))
        self.assertLess(sheet.index(reverse('analytics')), money)
