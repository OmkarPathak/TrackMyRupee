"""Market investments (demat, mutual fund, ETF, SGB, ULIP, gold): purchases, sales, average cost and what they are worth.

Prices are given with every purchase and valuation, so nothing here reaches for a live NAV.
"""
import re
from datetime import timedelta
from decimal import Decimal

from django.utils import timezone

from e2e.base import E2ETestCase, make_account, make_user
from e2e.type_helpers import money, net_worth, open_account

D = Decimal
TODAY = timezone.localdate()
KINDS = ('DEMAT', 'MUTUAL_FUND', 'ETF', 'SGB', 'ULIP', 'GOLD')


class Base(E2ETestCase):
    def setUp(self):
        super().setUp()
        self.user = make_user('invest-edge')
        self.bank = make_account(self.user, 'HDFC Savings', 'SAVINGS_ACCOUNT', 100000)

    def fund_account(self, kind='DEMAT', name='Broker', paid_in=60000):
        """An investment account with `paid_in` moved into it from the bank."""
        from expenses.models import Transfer
        account = make_account(self.user, name, kind, 0)
        Transfer.objects.create(user=self.user, from_account=self.bank, to_account=account, amount=D(paid_in),
                                converted_amount=D(paid_in), date=TODAY - timedelta(days=30))
        account.refresh_from_db()
        return account

    def holding(self, account, name='Fund X'):
        from expenses.models import Holding
        return Holding.objects.create(account=account, instrument_name=name, instrument_type=account.account_type if account.account_type in
                                      ('MUTUAL_FUND', 'ETF', 'SGB', 'ULIP') else 'STOCK', units=D('0'), avg_cost=None, is_active=True)

    def buy(self, holding, units, price, on=None):
        from expenses import holdings_service
        return holdings_service.add_purchase(holding, date=on or TODAY - timedelta(days=20), units=D(str(units)), price=D(str(price)))

    def sell(self, holding, units, price, on=None):
        from expenses import holdings_service
        return holdings_service.add_sale(holding, date=on or TODAY - timedelta(days=5), units=D(str(units)), price=D(str(price)))

    def price(self, holding, nav, on=None):
        from expenses.models import Valuation
        return Valuation.objects.create(holding=holding, value=holding.units * D(str(nav)), unit_nav=D(str(nav)), as_of_date=on or TODAY)

    def row(self, page, label):
        text = page.inner_text('body').replace('\n', ' ')
        found = re.search(re.escape(label) + r'\s*(−?-?₹[\d,]+(?:\.\d+)?)', text)
        self.assertIsNotNone(found, f'no "{label}" on the page: {text[:300]}')
        return money(found.group(1))


class PurchaseTests(Base):
    def test_a_purchase_is_money_invested_and_the_rest_stays_cash(self):
        account = self.fund_account(paid_in=60000)
        holding = self.holding(account)
        self.buy(holding, 100, 500)
        holding.refresh_from_db()
        self.assertEqual((holding.units, holding.avg_cost), (D('100'), D('500')))
        page = self.new_page(self.user)
        open_account(self, page, account)
        self.assertEqual(self.row(page, 'Invested in holdings:'), D('50000'))
        self.assertEqual(self.row(page, 'Uninvested cash:'), D('10000'))

    def test_a_second_purchase_at_another_price_gives_the_average_cost(self):
        account = self.fund_account(paid_in=60000)
        holding = self.holding(account)
        self.buy(holding, 40, 500)
        self.buy(holding, 20, 800)
        holding.refresh_from_db()
        self.assertEqual(holding.units, D('60'))
        self.assertEqual(holding.avg_cost, D('600'))                 # (40×500 + 20×800) / 60 = 36,000 / 60

    def test_units_can_be_fractional_to_six_places(self):
        account = self.fund_account(paid_in=10000)
        holding = self.holding(account)
        self.buy(holding, '12.345678', '81.0125')
        holding.refresh_from_db()
        self.assertEqual(holding.units, D('12.345678'))

    def test_any_two_of_units_price_and_amount_are_enough(self):
        from expenses import holdings_service
        self.assertEqual(holdings_service.derive_purchase(units='10', amount='5000'), (D('10.000000'), D('500.000000'), D('5000.00')))
        self.assertEqual(holdings_service.derive_purchase(price='250', amount='1000'), (D('4.000000'), D('250.000000'), D('1000.00')))
        self.assertEqual(holdings_service.derive_purchase(units='3', price='33.33'), (D('3.000000'), D('33.330000'), D('99.99')))

    def test_nothing_is_recorded_for_zero_negative_missing_or_one_only(self):
        from expenses import holdings_service
        for kwargs in (dict(units='0', price='10'), dict(units='-1', price='10'), dict(units='5'), dict(), dict(units='abc', price='10')):
            with self.subTest(kwargs), self.assertRaises(holdings_service.HoldingError):
                holdings_service.derive_purchase(**kwargs)

    def test_each_kind_of_account_holds_its_own_instruments(self):
        account_kinds = []
        for kind in KINDS:
            account = make_account(self.user, f'Acct {kind}', kind, 0)
            holding = self.holding(account, f'{kind} holding')
            self.buy(holding, 10, 100)
            holding.refresh_from_db()
            account_kinds.append((kind, holding.units, holding.avg_cost))
        self.assertEqual(account_kinds, [(kind, D('10'), D('100')) for kind in KINDS])


class SaleTests(Base):
    def setUp(self):
        super().setUp()
        self.account = self.fund_account(paid_in=60000)
        self.holding_ = self.holding(self.account)
        self.buy(self.holding_, 100, 500)

    def test_selling_part_keeps_the_average_cost_of_what_is_left(self):
        self.sell(self.holding_, 40, 650)
        self.holding_.refresh_from_db()
        self.assertEqual((self.holding_.units, self.holding_.avg_cost), (D('60'), D('500')))

    def test_the_sale_proceeds_become_cash_and_the_profit_is_recorded(self):
        self.sell(self.holding_, 40, 650)                            # 26,000 received for 20,000 of cost: 6,000 profit
        page = self.new_page(self.user)
        open_account(self, page, self.account)
        self.assertEqual(self.row(page, 'Invested in holdings:'), D('30000'))
        self.assertEqual(self.row(page, 'Uninvested cash:'), D('36000'))   # 10,000 left + 26,000

    def test_selling_more_than_is_held_is_refused(self):
        from expenses import holdings_service
        with self.assertRaises(holdings_service.HoldingError):
            self.sell(self.holding_, 100.000001, 650)
        self.holding_.refresh_from_db()
        self.assertEqual(self.holding_.units, D('100'))

    def test_selling_everything_leaves_no_units_and_no_invested_money(self):
        self.sell(self.holding_, 100, 480)                           # a loss of 2,000
        self.holding_.refresh_from_db()
        self.assertEqual(self.holding_.units, D('0'))
        page = self.new_page(self.user)
        open_account(self, page, self.account)
        self.assertEqual(self.row(page, 'Invested in holdings:'), D('0'))
        self.assertEqual(self.row(page, 'Uninvested cash:'), D('58000'))


class WorthTests(Base):
    def test_net_worth_follows_the_latest_price_not_the_cost(self):
        account = self.fund_account(paid_in=60000)
        holding = self.holding(account)
        self.buy(holding, 100, 500)
        page = self.new_page(self.user)
        self.assertEqual(net_worth(self, page), D('100000'))              # 40,000 bank + 50,000 at cost + 10,000 cash
        self.price(holding, 600, on=TODAY - timedelta(days=10))
        self.assertEqual(net_worth(self, page), D('110000'))
        self.price(holding, 450, on=TODAY)                                # the newest price wins
        self.assertEqual(net_worth(self, page), D('95000'))

    def test_an_older_price_entered_later_does_not_replace_a_newer_one(self):
        account = self.fund_account(paid_in=60000)
        holding = self.holding(account)
        self.buy(holding, 100, 500)
        self.price(holding, 700, on=TODAY)
        self.price(holding, 100, on=TODAY - timedelta(days=60))
        page = self.new_page(self.user)
        self.assertEqual(net_worth(self, page), D('120000'))

    def test_after_a_sale_the_remaining_units_are_valued_at_the_latest_price(self):
        account = self.fund_account(paid_in=60000)
        holding = self.holding(account)
        self.buy(holding, 100, 500)
        self.sell(holding, 40, 650)
        holding.refresh_from_db()
        self.price(holding, 600)                                          # 60 units × 600
        page = self.new_page(self.user)
        # 40,000 bank + 36,000 cash + 36,000 holdings
        self.assertEqual(net_worth(self, page), D('112000'))

    def test_a_holding_with_no_price_yet_is_counted_at_cost(self):
        account = self.fund_account(paid_in=60000)
        holding = self.holding(account)
        self.buy(holding, 10, 1234.5)
        page = self.new_page(self.user)
        self.assertEqual(net_worth(self, page), D('100000'))

    def test_a_holding_marked_inactive_leaves_its_money_in_the_account_as_cash(self):
        from expenses.models import Holding
        account = self.fund_account(paid_in=60000)
        holding = self.holding(account)
        self.buy(holding, 100, 500)
        self.price(holding, 600)
        Holding.objects.filter(pk=holding.pk).update(is_active=False)
        page = self.new_page(self.user)
        # the 60,000 paid in is still in the account, now with nothing bought: 40,000 bank + 60,000 cash
        self.assertEqual(net_worth(self, page), D('100000'))
