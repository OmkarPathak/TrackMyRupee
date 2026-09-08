from decimal import Decimal
from django.test import TestCase, RequestFactory, override_settings
from django.contrib.auth import get_user_model
from django.utils import timezone
from expenses.models import Account, UserProfile, Holding, Valuation
from expenses.views.accounts import AccountListView

User = get_user_model()


@override_settings(
    LEDGER_READ_ENABLED=True,
    LEDGER_WRITE_ENABLED=True,
    NET_WORTH_EXTENDED_MODELS_ENABLED=True
)
class AccountListViewNetWorthTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.user = User.objects.create_user(username='accviewuser', password='password')
        UserProfile.objects.get_or_create(user=self.user, defaults={'currency': 'INR'})

        # Mutual Fund account with 0.00 cash balance but ₹50,000 in holdings
        self.mf_acc = Account.objects.create(
            user=self.user,
            name='Equity Mutual Fund',
            account_type='MUTUAL_FUND',
            balance=Decimal('0.00'),
            currency='INR'
        )
        self.holding = Holding.objects.create(
            account=self.mf_acc,
            instrument_name='Flexi Cap Fund',
            units=Decimal('500.00'),
            avg_cost=Decimal('80.00'),
            currency='INR',
            is_active=True
        )
        Valuation.objects.create(
            holding=self.holding,
            as_of_date=timezone.now().date(),
            value=Decimal('50000.00'),
            cost_basis=Decimal('40000.00')
        )

    def test_account_list_view_shows_market_value_for_mutual_fund(self):
        request = self.factory.get('/accounts/')
        request.user = self.user

        view = AccountListView()
        view.request = request
        view.object_list = view.get_queryset()

        context = view.get_context_data()

        # Find the mutual fund account in context['grouped_accounts']
        mf_account_in_ctx = None
        for group in context['grouped_accounts']:
            for acc in group['accounts']:
                if acc.pk == self.mf_acc.pk:
                    mf_account_in_ctx = acc
                    break

        self.assertIsNotNone(mf_account_in_ctx)
        # display_balance must reflect market valuation (50,000.00), not ledger cash balance (0.00)
        self.assertEqual(mf_account_in_ctx.display_balance, Decimal('50000.00'))
