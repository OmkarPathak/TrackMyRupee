from datetime import date
from decimal import Decimal
from django.contrib.auth.models import User
from django.test import RequestFactory, TestCase
from django.core.cache import cache
from django.urls import reverse

from ..filters.definitions import (
    DASHBOARD_FILTERS,
    EXPENSE_FILTERS,
    INCOME_FILTERS,
    get_user_accounts,
    get_user_categories,
    get_user_merchants,
)
from ..filters.engine import apply_filter_config
from ..filters.schema import FilterDef, FilterSetConfig
from ..models import Account, CapitalEvent, Category, Expense, Income, RecurringTransaction


class FilterSystemTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='filteruser', password='password123')
        self.factory = RequestFactory()

        # Accounts
        self.account_cash = Account.objects.create(user=self.user, name='Cash Account', balance=10000)
        self.account_card = Account.objects.create(user=self.user, name='HDFC Credit Card', balance=50000)

        # Expenses
        self.e1 = Expense.objects.create(
            user=self.user,
            date=date(2026, 9, 15),
            amount=Decimal('250.00'),
            category='Food',
            payment_method='Cash',
            account=self.account_cash,
            description='Zomato Momos',
        )
        self.e2 = Expense.objects.create(
            user=self.user,
            date=date(2026, 9, 14),
            amount=Decimal('1500.00'),
            category='Bills',
            payment_method='Credit Card',
            account=self.account_card,
            description='Netflix Subscription (Recurring)',
        )
        self.e3 = Expense.objects.create(
            user=self.user,
            date=date(2026, 9, 10),
            amount=Decimal('12000.00'),
            category='Shopping',
            payment_method='Credit Card',
            account=self.account_card,
            description='Amazon Electronics Purchase',
        )

        # Incomes
        self.i1 = Income.objects.create(
            user=self.user,
            date=date(2026, 9, 1),
            amount=Decimal('80000.00'),
            source='Acme Corp',
            source_type='Salary',
            account=self.account_cash,
            description='Monthly Salary Payment',
        )
        self.i2 = Income.objects.create(
            user=self.user,
            date=date(2026, 9, 5),
            amount=Decimal('5000.00'),
            source='Dividend',
            source_type='Investment Returns',
            account=self.account_card,
            description='Quarterly Mutual Fund Dividend',
        )

    def test_schema_serialization(self):
        dict_output = EXPENSE_FILTERS.to_dict()
        self.assertEqual(dict_output['page_key'], 'expenses')
        self.assertTrue(len(dict_output['filters']) > 0)
        
        json_str = EXPENSE_FILTERS.to_json()
        self.assertIn('"page_key": "expenses"', json_str)

    def test_expense_filter_engine_multi_select(self):
        # Filter by Category = Food
        request = self.factory.get('/expenses/?category=Food&time_period=all')
        request.user = self.user

        qs = Expense.objects.filter(user=self.user)
        filtered_qs, state = apply_filter_config(qs, request, EXPENSE_FILTERS)

        self.assertEqual(filtered_qs.count(), 1)
        self.assertEqual(filtered_qs.first(), self.e1)
        self.assertEqual(state['filters']['category'], ['Food'])

    def test_expense_filter_engine_account_and_search(self):
        # Filter by Account ID + Search query
        request = self.factory.get(f'/expenses/?account={self.account_card.id}&search=Netflix&time_period=all')
        request.user = self.user

        qs = Expense.objects.filter(user=self.user)
        filtered_qs, state = apply_filter_config(qs, request, EXPENSE_FILTERS)

        self.assertEqual(filtered_qs.count(), 1)
        self.assertEqual(filtered_qs.first(), self.e2)
        self.assertEqual(state['search'], 'Netflix')

    def test_expense_filter_engine_amount_range(self):
        # Filter amount_range = "Over ₹10,000"
        request = self.factory.get('/expenses/?amount_range=Over%20%E2%82%B910,000&time_period=all')
        request.user = self.user

        qs = Expense.objects.filter(user=self.user)
        filtered_qs, _ = apply_filter_config(qs, request, EXPENSE_FILTERS)

        self.assertEqual(filtered_qs.count(), 1)
        self.assertEqual(filtered_qs.first(), self.e3)

    def test_income_filter_engine_income_group(self):
        # Filter Income Group = PASSIVE
        request = self.factory.get('/income/list/?income_group=PASSIVE&time_period=all')
        request.user = self.user

        qs = Income.objects.filter(user=self.user)
        filtered_qs, state = apply_filter_config(qs, request, INCOME_FILTERS)

        self.assertEqual(filtered_qs.count(), 1)
        self.assertEqual(filtered_qs.first(), self.i2)

    def test_tampered_or_invalid_filter_keys_ignored(self):
        # Invalid query key malformed=123 should be ignored without error
        request = self.factory.get('/expenses/?invalid_key=malformed&category=Bills&time_period=all')
        request.user = self.user

        qs = Expense.objects.filter(user=self.user)
        filtered_qs, state = apply_filter_config(qs, request, EXPENSE_FILTERS)

        self.assertEqual(filtered_qs.count(), 1)
        self.assertEqual(filtered_qs.first(), self.e2)
        self.assertNotIn('invalid_key', state['filters'])

    def test_filter_options_api_endpoint(self):
        self.client.login(username='filteruser', password='password123')

        # Test Accounts Option Endpoint
        url = reverse('filter-options-api') + '?page=expenses&filter=account'
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data['page'], 'expenses')
        self.assertEqual(data['filter'], 'account')
        self.assertTrue(len(data['options']) >= 2)

        # Test Search query on Category Options
        url_search = reverse('filter-options-api') + '?page=expenses&filter=category&q=Food'
        resp_search = self.client.get(url_search)
        self.assertEqual(resp_search.status_code, 200)
        data_search = resp_search.json()
        self.assertTrue(any(opt['value'] == 'Food' for opt in data_search['options']))

        # Test Accounts page options
        resp_acc = self.client.get(reverse('filter-options-api') + '?page=accounts&filter=type')
        self.assertEqual(resp_acc.status_code, 200)
        self.assertEqual(resp_acc.json()['page'], 'accounts')

        # Test Account Detail page options
        resp_accd = self.client.get(reverse('filter-options-api') + '?page=account_detail&filter=category')
        self.assertEqual(resp_accd.status_code, 200)
        self.assertEqual(resp_accd.json()['page'], 'account_detail')

        # Test Recurring page options
        resp_rec = self.client.get(reverse('filter-options-api') + '?page=recurring&filter=category')
        self.assertEqual(resp_rec.status_code, 200)
        self.assertEqual(resp_rec.json()['page'], 'recurring')

        # Test Capital Events page options
        resp_cap = self.client.get(reverse('filter-options-api') + '?page=capital_events&filter=account')
        self.assertEqual(resp_cap.status_code, 200)
        self.assertEqual(resp_cap.json()['page'], 'capital_events')

        # Test Dashboard page category options
        resp_dash = self.client.get(reverse('filter-options-api') + '?page=dashboard&filter=category')
        self.assertEqual(resp_dash.status_code, 200)
        self.assertEqual(resp_dash.json()['page'], 'dashboard')
        self.assertTrue(any(opt['value'] == 'Food' for opt in resp_dash.json()['options']))

        # Test Invalid Page key
        resp_bad = self.client.get(reverse('filter-options-api') + '?page=unknown&filter=account')
        self.assertEqual(resp_bad.status_code, 400)

    def test_all_transactions_account_filtering(self):
        self.client.login(username='filteruser', password='password123')
        url = reverse('all-transactions') + f'?account={self.account_cash.id}&time_period=all'
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)

        applied_state = resp.context['applied_state']
        self.assertEqual(applied_state['filters']['account'], [str(self.account_cash.id)])

        # Verify filtered transactions only belong to account_cash
        tx_list = resp.context['transactions']
        for tx in tx_list:
            # For expense, income, loan, capital event: source_account_id == account_cash.id
            # For transfer: source_account_id or target_account_id == account_cash.id
            is_match = (tx['source_account_id'] == self.account_cash.id or tx['target_account_id'] == self.account_cash.id)
            self.assertTrue(is_match)

    def test_account_list_filtering(self):
        self.client.login(username='filteruser', password='password123')
        acc_archived = Account.objects.create(user=self.user, name='Old Closed Bank', balance=Decimal('0.00'), is_active=False)
        self.account_card.is_pinned = True
        self.account_card.account_type = 'CREDIT_CARD'
        self.account_card.save()

        # 1. Search filter
        url = reverse('account-list') + '?search=HDFC'
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        accounts = list(resp.context['accounts'])
        self.assertEqual(len(accounts), 1)
        self.assertEqual(accounts[0].name, 'HDFC Credit Card')
        self.assertEqual(resp.context['applied_state']['search'], 'HDFC')

        # 2. Status filter: inactive (archived)
        url = reverse('account-list') + '?status=inactive'
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        accounts = list(resp.context['accounts'])
        self.assertEqual(len(accounts), 1)
        self.assertEqual(accounts[0].name, 'Old Closed Bank')

        # 3. Pinned filter
        url = reverse('account-list') + '?pinned=pinned'
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        accounts = list(resp.context['accounts'])
        self.assertTrue(all(a.is_pinned for a in accounts))
        self.assertIn(self.account_card, accounts)

        # 4. Type filter
        url = reverse('account-list') + '?type=CREDIT_CARD'
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        accounts = list(resp.context['accounts'])
        self.assertTrue(all(a.account_type == 'CREDIT_CARD' for a in accounts))

    def test_account_detail_filtering_and_sorting(self):
        self.client.login(username='filteruser', password='password123')

        # 1. Search in account detail
        url = reverse('account-detail', args=[self.account_card.pk]) + '?search=Netflix'
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        items = list(resp.context['ledger'])
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].description, 'Netflix Subscription (Recurring)')
        self.assertEqual(resp.context['applied_state']['search'], 'Netflix')

        # 2. Filter tx_type=EXPENSE
        url = reverse('account-detail', args=[self.account_card.pk]) + '?tx_type=EXPENSE'
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        items = list(resp.context['ledger'])
        self.assertTrue(all(it.transaction_type == 'EXPENSE' for it in items))

        # 3. Filter amount_range = "Over ₹10,000"
        url = reverse('account-detail', args=[self.account_card.pk]) + '?amount_range=Over%20%E2%82%B910,000'
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        items = list(resp.context['ledger'])
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].description, 'Amazon Electronics Purchase')

        # 4. Sorting: amount_desc
        url = reverse('account-detail', args=[self.account_card.pk]) + '?sort=amount_desc'
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        items = list(resp.context['ledger'])
        self.assertTrue(len(items) >= 2)
        self.assertGreaterEqual(items[0].amount, items[1].amount)

    def test_recurring_transaction_filtering(self):
        self.client.login(username='filteruser', password='password123')
        RecurringTransaction.objects.create(
            user=self.user,
            transaction_type='EXPENSE',
            amount=Decimal('499.00'),
            base_amount=Decimal('499.00'),
            description='Spotify Premium Sub',
            category='Entertainment',
            frequency='MONTHLY',
            start_date=date(2026, 1, 1),
            next_due_date=date(2026, 10, 1),
            is_active=True,
            account=self.account_card,
        )
        RecurringTransaction.objects.create(
            user=self.user,
            transaction_type='INCOME',
            amount=Decimal('50000.00'),
            base_amount=Decimal('50000.00'),
            description='Consulting Retainer',
            category='Consulting',
            frequency='MONTHLY',
            start_date=date(2026, 1, 1),
            next_due_date=date(2026, 10, 5),
            is_active=True,
            account=self.account_cash,
        )
        RecurringTransaction.objects.create(
            user=self.user,
            transaction_type='EXPENSE',
            amount=Decimal('15000.00'),
            base_amount=Decimal('15000.00'),
            description='Gym Annual Pass',
            category='Fitness',
            frequency='YEARLY',
            start_date=date(2025, 1, 1),
            next_due_date=date(2026, 1, 1),
            is_active=False,
            account=self.account_card,
        )

        # 1. Search filter
        url = reverse('recurring-list') + '?search=Spotify'
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        active_subs = resp.context['active_subs']
        self.assertEqual(len(active_subs), 1)
        self.assertEqual(active_subs[0].description, 'Spotify Premium Sub')
        self.assertEqual(resp.context['applied_state']['search'], 'Spotify')

        # 2. Filter transaction_type=INCOME
        url = reverse('recurring-list') + '?transaction_type=INCOME'
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        active_subs = resp.context['active_subs']
        self.assertEqual(len(active_subs), 1)
        self.assertEqual(active_subs[0].description, 'Consulting Retainer')

        # 3. Filter status=cancelled
        url = reverse('recurring-list') + '?status=cancelled'
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        cancelled_subs = resp.context['cancelled_subs']
        self.assertEqual(len(cancelled_subs), 1)
        self.assertEqual(cancelled_subs[0].description, 'Gym Annual Pass')
        self.assertEqual(resp.context['current_status'], 'cancelled')

        # 4. Filter account
        url = reverse('recurring-list') + f'?account={self.account_cash.id}'
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        active_subs = resp.context['active_subs']
        self.assertEqual(len(active_subs), 1)
        self.assertEqual(active_subs[0].account_id, self.account_cash.id)

        # 5. Filter frequency=YEARLY
        url = reverse('recurring-list') + '?frequency=YEARLY&status=cancelled'
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        cancelled_subs = resp.context['cancelled_subs']
        self.assertEqual(len(cancelled_subs), 1)
        self.assertEqual(cancelled_subs[0].frequency, 'YEARLY')

        # 6. Sorting by amount_desc
        url = reverse('recurring-list') + '?sort=amount_desc'
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        active_subs = resp.context['active_subs']
        self.assertTrue(len(active_subs) >= 2)
        self.assertGreaterEqual(active_subs[0].base_amount, active_subs[1].base_amount)

    def test_capital_event_filtering(self):
        self.client.login(username='filteruser', password='password123')
        ce1 = CapitalEvent.objects.create(
            user=self.user,
            date=date(2026, 9, 15),
            amount=Decimal('500000.00'),
            base_amount=Decimal('500000.00'),
            subtype='loan_down_payment',
            account=self.account_cash,
            note='Down payment on flat',
        )
        ce2 = CapitalEvent.objects.create(
            user=self.user,
            date=date(2026, 9, 10),
            amount=Decimal('25000.00'),
            base_amount=Decimal('25000.00'),
            subtype='medical_lump_sum',
            account=self.account_card,
            note='Emergency hospital surgery',
        )
        ce3 = CapitalEvent.objects.create(
            user=self.user,
            date=date(2026, 9, 5),
            amount=Decimal('1000.00'),
            base_amount=Decimal('1000.00'),
            subtype='gift_given',
            account=self.account_cash,
            note='Wedding gift gold coin',
        )

        # 1. Search filter
        url = reverse('capital-event-list') + '?search=hospital&time_period=all'
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        events = list(resp.context['events'])
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].note, 'Emergency hospital surgery')
        self.assertEqual(resp.context['applied_state']['search'], 'hospital')

        # 2. Filter by subtype
        url = reverse('capital-event-list') + '?subtype=loan_down_payment&time_period=all'
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        events = list(resp.context['events'])
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].subtype, 'loan_down_payment')

        # 3. Filter by account
        url = reverse('capital-event-list') + f'?account={self.account_cash.id}&time_period=all'
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        events = list(resp.context['events'])
        self.assertEqual(len(events), 2)
        self.assertTrue(all(e.account_id == self.account_cash.id for e in events))

        # 4. Filter by amount_range
        url = reverse('capital-event-list') + '?amount_range=%E2%82%B9500%20to%20%E2%82%B92,000&time_period=all'
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        events = list(resp.context['events'])
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].id, ce3.id)

        # 5. Sorting by amount_desc
        url = reverse('capital-event-list') + '?sort=amount_desc&time_period=all'
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        events = list(resp.context['events'])
        self.assertEqual(len(events), 3)
        self.assertGreaterEqual(events[0].base_amount, events[1].base_amount)
        self.assertGreaterEqual(events[1].base_amount, events[2].base_amount)

    def test_category_options_caching_and_invalidation(self):
        cache.clear()
        # 1. First call populates cache
        cats1 = get_user_categories(self.user)
        self.assertTrue(len(cats1) >= 2)

        # 2. Second call with or without query hits cache, no DB queries
        with self.assertNumQueries(0):
            cats2 = get_user_categories(self.user, q='Foo')
            self.assertEqual(len(cats2), 1)
            self.assertEqual(cats2[0]['value'], 'Food')

        with self.assertNumQueries(0):
            cats3 = get_user_categories(self.user)
            self.assertEqual(cats1, cats3)

        # 3. Cache invalidation on Expense save
        Expense.objects.create(
            user=self.user,
            date=date(2026, 9, 20),
            amount=Decimal('100.00'),
            category='Utilities',
            description='Electricity Bill',
        )
        self.assertIsNone(cache.get(f"filter_categories:{self.user.id}"))

        # 4. Re-populate and test invalidation on Category save
        get_user_categories(self.user)
        self.assertIsNotNone(cache.get(f"filter_categories:{self.user.id}"))

        Category.objects.create(user=self.user, name='Healthcare')
        self.assertIsNone(cache.get(f"filter_categories:{self.user.id}"))

    def test_merchant_options_caching_and_invalidation(self):
        cache.clear()
        # 1. First call populates cache
        merchants1 = get_user_merchants(self.user)
        self.assertTrue(len(merchants1) >= 2)

        # 2. Second call with various keystrokes hits cache, no DB queries
        with self.assertNumQueries(0):
            m_z = get_user_merchants(self.user, q='Z')
            m_zo = get_user_merchants(self.user, q='Zo')
            m_zom = get_user_merchants(self.user, q='Zom')
            self.assertTrue(any(opt['value'] == 'Amazon' for opt in m_z))
            self.assertTrue(any(opt['value'] == 'Amazon' for opt in m_zo))
            self.assertFalse(any(opt['value'] == 'Amazon' for opt in m_zom))
            self.assertEqual([opt['value'] for opt in m_zom], ['Zomato', 'Zomato Momos'])

        # 3. Cache invalidation on Expense save
        new_exp = Expense.objects.create(
            user=self.user,
            date=date(2026, 9, 20),
            amount=Decimal('500.00'),
            category='Shopping',
            description='Flipkart Laptop Stand',
        )
        self.assertIsNone(cache.get(f"filter_merchants:{self.user.id}"))

        # 4. Re-populate and test invalidation on Expense delete
        get_user_merchants(self.user)
        self.assertIsNotNone(cache.get(f"filter_merchants:{self.user.id}"))

        new_exp.delete()
        self.assertIsNone(cache.get(f"filter_merchants:{self.user.id}"))

    def test_expense_list_active_filters_count(self):
        self.client.login(username='filteruser', password='password123')
        # Default: time_period='this_month', sort='date_desc' -> 0
        resp = self.client.get(reverse('expense-list'))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context['active_filters_count'], 0)

        # 1 filter (category) + search (+1) + time_period=all (+1) + sort=amount_desc (+1) -> 4
        url = reverse('expense-list') + '?category=Food&search=Momos&time_period=all&sort=amount_desc'
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context['active_filters_count'], 4)

    def test_custom_date_range_filtering(self):
        self.client.login(username='filteruser', password='password123')
        url = reverse('expense-list') + '?time_period=custom&start_date=2026-09-12&end_date=2026-09-15'
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)

        expenses = list(resp.context['expenses'])
        self.assertEqual(len(expenses), 2)
        self.assertIn(self.e1, expenses)
        self.assertIn(self.e2, expenses)
        self.assertNotIn(self.e3, expenses)

        applied_state = resp.context['applied_state']
        self.assertEqual(applied_state['time_period'], 'custom')
        self.assertEqual(applied_state['start_date'], '2026-09-12')
        self.assertEqual(applied_state['end_date'], '2026-09-15')

        content = resp.content.decode('utf-8')
        self.assertIn('2026-09-12 &ndash; 2026-09-15', content)

    def test_account_options_active_first_inactive_last(self):
        # Create active and inactive accounts
        # Names chosen to test alphabetical sort vs active status sort
        # A_inactive should come AFTER Z_active
        acc_a_inactive = Account.objects.create(user=self.user, name='AAA Old Bank', is_active=False)
        acc_z_active = Account.objects.create(user=self.user, name='ZZZ New Bank', is_active=True)

        options = get_user_accounts(self.user)
        # Verify active accounts come before inactive accounts
        active_opts = [opt for opt in options if opt.get('is_active') is True]
        inactive_opts = [opt for opt in options if opt.get('is_active') is False]

        self.assertTrue(len(active_opts) >= 3)  # Cash Account, HDFC Credit Card, ZZZ New Bank
        self.assertEqual(len(inactive_opts), 1)  # AAA Old Bank

        # AAA Old Bank must be in inactive_opts and have (Inactive) in label
        self.assertEqual(inactive_opts[0]['value'], str(acc_a_inactive.id))
        self.assertEqual(inactive_opts[0]['label'], 'AAA Old Bank (Inactive)')
        self.assertFalse(inactive_opts[0]['is_active'])

        # In full list, all active options must appear before inactive options
        active_indices = [i for i, opt in enumerate(options) if opt.get('is_active') is True]
        inactive_indices = [i for i, opt in enumerate(options) if opt.get('is_active') is False]
        self.assertLess(max(active_indices), min(inactive_indices))

        # Test search query "inactive" matches inactive accounts
        search_inac = get_user_accounts(self.user, q='inactive')
        self.assertTrue(any(opt['value'] == str(acc_a_inactive.id) for opt in search_inac))

    def test_filter_options_api_account_inactive_handling(self):
        acc_inactive = Account.objects.create(user=self.user, name='Closed ICICI', is_active=False)
        self.client.login(username='filteruser', password='password123')

        url = reverse('filter-options-api') + '?page=expenses&filter=account'
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)

        data = resp.json()
        self.assertEqual(data['page'], 'expenses')
        self.assertEqual(data['filter'], 'account')
        options = data['options']

        # Verify last option is the inactive account
        last_opt = options[-1]
        self.assertEqual(last_opt['value'], str(acc_inactive.id))
        self.assertEqual(last_opt['label'], 'Closed ICICI (Inactive)')
        self.assertFalse(last_opt['is_active'])

    def test_dashboard_filter_config_definitions(self):
        filter_keys = [f.key for f in DASHBOARD_FILTERS.filters]
        self.assertIn('category', filter_keys)
        self.assertIn('payment_method', filter_keys)
        self.assertIn('account', filter_keys)

        pm_filter = DASHBOARD_FILTERS.get_filter('payment_method')
        self.assertEqual(pm_filter.type, 'multi_select')
        self.assertEqual(pm_filter.source, 'static')
        pm_values = [opt['value'] for opt in pm_filter.get_options_list()]
        self.assertIn('Cash', pm_values)
        self.assertIn('Credit Card', pm_values)
        self.assertIn('Debit Card', pm_values)
        self.assertIn('UPI', pm_values)
        self.assertIn('NetBanking', pm_values)

        acc_filter = DASHBOARD_FILTERS.get_filter('account')
        self.assertEqual(acc_filter.type, 'multi_select')
        self.assertEqual(acc_filter.source, 'dynamic')

    def test_filter_options_api_dashboard_endpoints(self):
        self.client.login(username='filteruser', password='password123')

        # Test Dashboard Payment Method options
        url_pm = reverse('filter-options-api') + '?page=dashboard&filter=payment_method'
        resp_pm = self.client.get(url_pm)
        self.assertEqual(resp_pm.status_code, 200)
        data_pm = resp_pm.json()
        self.assertEqual(data_pm['page'], 'dashboard')
        self.assertEqual(data_pm['filter'], 'payment_method')
        pm_opts = [opt['value'] for opt in data_pm['options']]
        self.assertIn('Cash', pm_opts)
        self.assertIn('Credit Card', pm_opts)

        # Test Dashboard Account options
        url_acc = reverse('filter-options-api') + '?page=dashboard&filter=account'
        resp_acc = self.client.get(url_acc)
        self.assertEqual(resp_acc.status_code, 200)
        data_acc = resp_acc.json()
        self.assertEqual(data_acc['page'], 'dashboard')
        self.assertEqual(data_acc['filter'], 'account')
        self.assertTrue(len(data_acc['options']) >= 2)

    def test_dashboard_view_payment_method_filtering(self):
        self.client.login(username='filteruser', password='password123')

        # 1. Filter by Cash
        resp_cash = self.client.get(reverse('home') + '?payment_method=Cash&time_period=all')
        self.assertEqual(resp_cash.status_code, 200)
        applied_state = resp_cash.context['applied_state']
        self.assertEqual(applied_state['filters']['payment_method'], ['Cash'])
        self.assertEqual(resp_cash.context['hero_metrics']['spent'], Decimal('250.00'))

        # 2. Filter by Credit Card
        resp_card = self.client.get(reverse('home') + '?payment_method=Credit%20Card&time_period=all')
        self.assertEqual(resp_card.status_code, 200)
        applied_state = resp_card.context['applied_state']
        self.assertEqual(applied_state['filters']['payment_method'], ['Credit Card'])
        # e2 (1500) + e3 (12000) = 13500
        self.assertEqual(resp_card.context['hero_metrics']['spent'], Decimal('13500.00'))

    def test_dashboard_view_account_filtering(self):
        self.client.login(username='filteruser', password='password123')

        # Filter by Cash Account
        resp = self.client.get(reverse('home') + f'?account={self.account_cash.id}&time_period=all')
        self.assertEqual(resp.status_code, 200)
        applied_state = resp.context['applied_state']
        self.assertEqual(applied_state['filters']['account'], [str(self.account_cash.id)])
        # Only e1 (250) on account_cash
        self.assertEqual(resp.context['hero_metrics']['spent'], Decimal('250.00'))
        # Only i1 (80000) on account_cash
        self.assertEqual(resp.context['hero_metrics']['income'], Decimal('80000.00'))

    def test_dashboard_view_combined_filtering(self):
        self.client.login(username='filteruser', password='password123')

        # Filter by Category=Shopping, Payment Method=Credit Card, Account=account_card
        url = reverse('home') + f'?category=Shopping&payment_method=Credit%20Card&account={self.account_card.id}&time_period=all'
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        applied_state = resp.context['applied_state']
        self.assertEqual(applied_state['filters']['category'], ['Shopping'])
        self.assertEqual(applied_state['filters']['payment_method'], ['Credit Card'])
        self.assertEqual(applied_state['filters']['account'], [str(self.account_card.id)])
        # Only e3 (12000) matches Shopping + Credit Card + account_card
        self.assertEqual(resp.context['hero_metrics']['spent'], Decimal('12000.00'))




