"""The Holdings table: the AMFI code is plain small text, and every row shows its gain or loss (amount and percent)."""
from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from expenses.models import Account, Holding, Valuation


class HoldingListGainTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user('hg', 'hg@example.com', 'pw')
        profile = self.user.profile
        profile.has_seen_tutorial = True
        profile.tier = 'PRO'
        profile.save()
        self.user.refresh_from_db()
        self.client.force_login(self.user)
        self.account = Account.objects.create(user=self.user, name='Mutual Funds', account_type='MUTUAL_FUND', balance=Decimal('0'))

    def holding(self, name, units, avg_cost, nav=None, code=''):
        h = Holding.objects.create(account=self.account, instrument_name=name, units=Decimal(units),
                                   avg_cost=Decimal(avg_cost), instrument_type='MF', scheme_code=code or None)
        if nav is not None:
            Valuation.objects.create(holding=h, value=Decimal(units) * Decimal(nav), unit_nav=Decimal(nav), as_of_date=date.today())
        return h

    def rows(self):
        response = self.client.get(reverse('holding-list'))
        self.assertEqual(response.status_code, 200)
        return {h.instrument_name: h for h in response.context['holdings']}, response

    def test_gain_amount_and_percent_per_fund(self):
        self.holding('Winner Fund', '10', '100', nav='120')
        self.holding('Loser Fund', '10', '100', nav='80')
        rows, response = self.rows()
        win, lose = rows['Winner Fund'], rows['Loser Fund']
        self.assertEqual((win.gain, win.gain_pct), (Decimal('200.00'), Decimal('20.0')))
        self.assertEqual((lose.gain, lose.gain_pct), (Decimal('-200.00'), Decimal('-20.0')))
        self.assertContains(response, 'Gain / Loss')
        self.assertContains(response, '+₹200')
        self.assertContains(response, '+20.0%')
        self.assertContains(response, '&minus;₹200')
        self.assertContains(response, '&minus;20.0%')

    def test_the_row_value_follows_the_units_held_now(self):
        h = self.holding('Edited Fund', '10', '100', nav='120')
        Holding.objects.filter(pk=h.pk).update(units=Decimal('20'))      # units corrected after the valuation was made
        rows, _ = self.rows()
        self.assertEqual(rows['Edited Fund'].current_value, Decimal('2400.00'))
        self.assertEqual(rows['Edited Fund'].gain, Decimal('2400.00') - Decimal('2000.00'))

    def test_no_gain_is_claimed_while_the_value_is_only_the_cost(self):
        self.holding('Unpriced Fund', '10', '100')
        rows, response = self.rows()
        row = rows['Unpriced Fund']
        self.assertFalse(row.has_gain)
        self.assertEqual(row.gain, Decimal('0.00'))
        self.assertContains(response, '(Cost Basis)')

    def test_amfi_code_is_plain_text_not_a_badge(self):
        self.holding('Coded Fund', '1', '100', nav='100', code='118834')
        _, response = self.rows()
        self.assertContains(response, 'AMFI Code: 118834')
        self.assertNotContains(response, 'font-size: 0.7rem;">AMFI Code:')         # the old badge
        self.assertContains(response, 'text-muted fw-normal" style="font-size: 0.75rem;">AMFI Code: 118834')
