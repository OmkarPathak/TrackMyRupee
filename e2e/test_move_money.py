"""Move Money: balances change, and the notes explain what a move does to Invested."""
from decimal import Decimal

from django.urls import reverse

from e2e.base import E2ETestCase, make_account, make_user


class MoveMoneyTests(E2ETestCase):
    def setUp(self):
        super().setUp()
        self.user = make_user('mover')
        self.bank = make_account(self.user, 'ICICI Savings', 'SAVINGS_ACCOUNT', 300000)
        self.cash = make_account(self.user, 'Cash', 'CASH_WALLET', 1000)
        self.fd = make_account(self.user, 'ICICI FD', 'FD', 0)
        self.fd2 = make_account(self.user, 'HDFC FD', 'FD', 100000)

    def pick(self, page, source, destination):
        self.set_select(page, 'select[name="from_account"]', str(source.pk))
        self.set_select(page, 'select[name="to_account"]', str(destination.pk))

    def balance(self, account):
        from expenses.models import Account
        return Account.objects.get(pk=account.pk).balance

    def test_moving_money_updates_both_balances(self):
        page = self.new_page(self.user)
        self.open(page, reverse('transfer-create'))
        self.pick(page, self.bank, self.cash)
        page.fill('input[name="amount"]', '5000')
        page.click('#moveMoneySubmit')
        page.wait_for_url(lambda url: '/transfers/add' not in url)
        self.assertEqual(self.balance(self.bank), Decimal('295000.00'))
        self.assertEqual(self.balance(self.cash), Decimal('6000.00'))

    def test_the_note_says_what_each_kind_of_move_does_to_invested(self):
        page = self.new_page(self.user)
        self.open(page, reverse('transfer-create'))
        note = page.locator('#transferInvestNote')
        self.pick(page, self.bank, self.cash)
        self.assertFalse(note.is_visible())                       # an ordinary move: no note
        self.pick(page, self.bank, self.fd)
        self.assertIn('money invested', note.inner_text())
        self.pick(page, self.fd2, self.bank)
        self.assertIn('lowers Invested', note.inner_text())
        self.pick(page, self.fd2, self.fd)
        self.assertIn('not counted as new investing', note.inner_text())

    def test_a_matured_fd_renewed_into_another_counts_only_the_new_money(self):
        """The ICICI example: 1L in, 1.1L back at maturity, 1.1L into a new FD: Invested is 1L, not 2.1L."""
        from expenses.investing import invested_totals
        from expenses.models import Transfer
        page = self.new_page(self.user)
        for source, destination, amount in ((self.bank, self.fd, '100000'), (self.fd, self.bank, '110000'),
                                            (self.bank, self.fd2, '110000')):
            self.open(page, reverse('transfer-create'))
            self.pick(page, source, destination)
            page.fill('input[name="amount"]', amount)
            page.click('#moveMoneySubmit')
            page.wait_for_url(lambda url: '/transfers/add' not in url)
        self.assertEqual(Transfer.objects.filter(user=self.user).count(), 3)
        totals = invested_totals(Transfer.objects.filter(user=self.user))
        self.assertEqual((totals['inflow'], totals['outflow'], totals['net']),
                         (Decimal('210000'), Decimal('110000'), Decimal('100000')))
        # ...and the Dashboard shows the net figure
        self.open(page, reverse('home'))
        lines = [line.strip() for line in page.locator('#bento-card-invested').inner_text().splitlines()]
        headline = next(line for line in lines if '₹' in line)
        self.assertEqual(headline, '₹1L')                          # (not ₹2.1L)
        self.assertTrue(any('2.1L in' in line and '1.1L out' in line for line in lines), lines)
