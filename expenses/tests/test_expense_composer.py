"""Tests for the New Expense composer: parser extensions, dry run, save, undo, learning, limits."""
import json
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import Client, SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import timezone

from expenses.composer import LARGE_AMOUNT_THRESHOLDS, UNDO_WINDOW
from expenses.models import Account, Category, Expense, ExpenseKeywordHint
from expenses.parser import keyword_candidates, parse_expense_nl
from finance_tracker.plans import PLAN_DETAILS

ACCOUNTS = [
    {'id': 1, 'name': 'SBI Savings', 'type': 'SAVINGS_ACCOUNT'},
    {'id': 2, 'name': 'HDFC Credit Card', 'type': 'CREDIT_CARD'},
    {'id': 3, 'name': 'Cash', 'type': 'CASH_WALLET'},
]
CATEGORIES = ['Food & Dining', 'Transport', 'Groceries', 'Shopping']


def parse(text, **kw):
    kw.setdefault('user_categories', CATEGORIES)
    kw.setdefault('account_info', ACCOUNTS)
    kw.setdefault('skip_genai', True)
    return parse_expense_nl(text, **kw)


class ParserAmountTests(SimpleTestCase):
    def test_amount_formats(self):
        cases = {
            'swiggy 450': '450.00',
            '₹450 swiggy': '450.00',
            'swiggy 450rs': '450.00',
            'groceries 2k': '2000.00',
            'groceries 1.5k': '1500.00',
            'rent 1,250': '1250.00',
            'flat 1,25,000': '125000.00',
            'car 2 lakh': '200000.00',
            'car 1.5 lakh': '150000.00',
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(parse(text)['amount'], expected)

    def test_biggest_bare_number_wins_but_is_flagged(self):
        result = parse('pizza 2 450')
        self.assertEqual(result['amount'], '450.00')
        self.assertIn('amount', result['check'])

    def test_missing_amount_is_flagged_not_failed(self):
        result = parse('chai')
        self.assertFalse(result['success'])
        self.assertIsNone(result['amount'])
        self.assertIn('amount', result['check'])

    def test_amount_is_not_confused_with_dates(self):
        result = parse('zomato 3 oct 320')
        self.assertEqual(result['amount'], '320.00')
        self.assertEqual(result['date'][5:], '10-03')


class ParserCurrencyTests(SimpleTestCase):
    def test_symbols_and_codes(self):
        cases = {'$10 coffee': '$', '10 usd coffee': '$', '€5 lunch': '€', '₹450 chai': '₹',
                 '5 gbp tea': '£', '1000 yen sushi': '¥', 'A$12 pie': 'A$'}
        for text, symbol in cases.items():
            with self.subTest(text=text):
                result = parse(text)
                self.assertEqual(result['currency'], symbol)
                self.assertTrue(result['currency_found'])

    def test_no_signal_keeps_default(self):
        result = parse('chai 20', default_currency='$')
        self.assertEqual(result['currency'], '$')
        self.assertFalse(result['currency_found'])


class ParserPaymentTests(SimpleTestCase):
    def test_keywords(self):
        cases = {
            'swiggy 450 upi': 'UPI', 'swiggy 450 gpay': 'UPI', 'swiggy 450 phonepe': 'UPI',
            'swiggy 450 paytm': 'UPI', 'swiggy 450 cc': 'Credit Card', 'swiggy 450 credit card': 'Credit Card',
            'swiggy 450 dc': 'Debit Card', 'swiggy 450 debit card': 'Debit Card',
            'swiggy 450 cash': 'Cash', 'swiggy 450 netbanking': 'NetBanking',
            'swiggy 450 net banking': 'NetBanking',
        }
        for text, method in cases.items():
            with self.subTest(text=text):
                result = parse(text)
                self.assertEqual(result['payment_method'], method)
                self.assertEqual(result['confidence']['payment_method'], 'high')

    def test_payment_words_leave_the_description(self):
        self.assertEqual(parse('paid 450 via gpay for swiggy dinner')['description'], 'Swiggy dinner')

    def test_bare_card_is_ambiguous_without_account_context(self):
        result = parse('petrol 500 card', account_info=[])
        self.assertIsNone(result['payment_method'])
        self.assertTrue(result['payment_ambiguous'])
        self.assertIn('payment_method', result['check'])

    def test_bare_card_uses_account_type(self):
        self.assertEqual(parse('petrol 500 card hdfc')['payment_method'], 'Credit Card')
        self.assertEqual(parse('petrol 500 card sbi')['payment_method'], 'Debit Card')
        self.assertEqual(parse('petrol 500 card', default_account=ACCOUNTS[1])['payment_method'], 'Credit Card')


class ParserAccountAndDateTests(SimpleTestCase):
    def test_account_hint(self):
        result = parse('amazon 999 hdfc')
        self.assertEqual(result['account'], 'HDFC Credit Card')
        self.assertEqual(result['account_id'], 2)
        self.assertEqual(parse('lunch 100 sbi')['account_id'], 1)

    def test_no_account_hint_when_unknown(self):
        self.assertIsNone(parse('lunch 100 icici')['account'])

    def test_relative_dates(self):
        today = timezone.localdate()
        self.assertEqual(parse('swiggy 320 yesterday')['date'], (today - timedelta(days=1)).isoformat())
        self.assertEqual(parse('chai 20 kal')['date'], (today - timedelta(days=1)).isoformat())
        self.assertEqual(parse('chai 20 parso')['date'], (today - timedelta(days=2)).isoformat())
        self.assertEqual(parse('chai 20 today')['date'], today.isoformat())
        self.assertTrue(parse('chai 20 kal')['date_found'])
        self.assertFalse(parse('chai 20')['date_found'])

    def test_explicit_dates(self):
        year = timezone.localdate().year
        self.assertEqual(parse('uber 150 3 oct')['date'][5:], '10-03')
        self.assertEqual(parse('uber 150 03/10')['date'][5:], '10-03')
        self.assertEqual(parse('uber 150 03/10/2024')['date'], '2024-10-03')
        self.assertEqual(parse('uber 150 oct 3')['date'][5:], '10-03')
        self.assertEqual(parse('uber 150 15 mar')['date'][5:], '03-15')
        self.assertIn(parse('uber 150 3 oct')['date'][:4], (str(year), str(year - 1)))

    def test_impossible_date_is_ignored(self):
        self.assertFalse(parse('uber 150 31/02')['date_found'])


class ParserLanguageTests(SimpleTestCase):
    def test_devanagari_numerals_and_dates(self):
        today = timezone.localdate()
        hi = parse('खाना ४५० कल')
        self.assertEqual(hi['amount'], '450.00')
        self.assertEqual(hi['date'], (today - timedelta(days=1)).isoformat())
        mr = parse('जेवण ३०० काल')
        self.assertEqual(mr['amount'], '300.00')
        self.assertEqual(mr['date'], (today - timedelta(days=1)).isoformat())
        self.assertEqual(parse('चाय २०')['amount'], '20.00')

    def test_hindi_words_for_amount_and_payment(self):
        self.assertEqual(parse('किराना 2 हजार')['amount'], '2000.00')
        self.assertEqual(parse('किराना 2 लाख')['amount'], '200000.00')
        self.assertEqual(parse('खाना 100 कैश')['payment_method'], 'Cash')

    def test_hinglish_category_rules(self):
        self.assertEqual(parse('chai 20')['category'], 'Food & Dining')
        self.assertEqual(parse('khana 120')['category'], 'Food & Dining')

    def test_unrecognised_words_stay_in_description(self):
        self.assertEqual(parse('birthday cake for mom 800')['description'], 'Birthday cake for mom')


class ParserConfidenceTests(SimpleTestCase):
    def test_confidence_shape(self):
        result = parse('swiggy dinner 450 upi')
        for key in ('amount', 'category', 'payment_method'):
            self.assertIn(key, result['confidence'])
        self.assertEqual(result['check'], [])

    def test_unknown_category_is_low_confidence(self):
        result = parse('xyzzy 450', user_categories=[])
        self.assertEqual(result['confidence']['category'], 'low')
        self.assertIn('category', result['check'])

    def test_hint_beats_rules(self):
        hints = {'swiggy': {'category': 'Shopping', 'account_id': 3, 'payment_method': 'Cash', 'count': 4}}
        result = parse('swiggy 320', hints=hints)
        self.assertEqual(result['category'], 'Shopping')
        self.assertEqual(result['account_id'], 3)
        self.assertEqual(result['payment_method'], 'Cash')

    def test_explicit_text_beats_hint(self):
        hints = {'swiggy': {'category': 'Shopping', 'account_id': 3, 'payment_method': 'Cash', 'count': 4}}
        result = parse('swiggy 320 upi sbi', hints=hints)
        self.assertEqual(result['payment_method'], 'UPI')
        self.assertEqual(result['account_id'], 1)

    def test_keyword_candidates(self):
        self.assertEqual(keyword_candidates('Swiggy dinner 450'), ['swiggy', 'dinner'])


class ComposerBase(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(username='composer', password='password')
        profile = self.user.profile
        profile.has_seen_tutorial = True
        profile.tier = 'PLUS'
        profile.save()
        self.client = Client()
        self.client.login(username='composer', password='password')
        for name in ('Food & Dining', 'Transport', 'Shopping'):
            Category.objects.get_or_create(user=self.user, name=name)
        self.sbi = Account.objects.create(user=self.user, name='SBI Savings', balance=Decimal('35000'))
        self.cash = Account.objects.create(user=self.user, name='Cash', balance=Decimal('1000'))

    def post_json(self, name, payload, **kwargs):
        return self.client.post(reverse(name, **kwargs), json.dumps(payload), content_type='application/json')

    def payload(self, **overrides):
        data = {
            'key': 'key-1', 'amount': '450', 'currency': '₹', 'date': timezone.localdate().isoformat(),
            'description': 'Swiggy dinner', 'category': 'Food & Dining',
            'account_id': self.sbi.id, 'payment_method': 'UPI',
        }
        data.update(overrides)
        return data

    def balance(self, account):
        return Account.objects.get(pk=account.pk).balance


class ParseEndpointTests(ComposerBase):
    def test_dry_run_saves_nothing(self):
        before = (Expense.objects.count(), ExpenseKeywordHint.objects.count())
        response = self.post_json('parse-expense', {'text': 'swiggy dinner 450 upi sbi'})
        self.assertEqual(response.status_code, 200)
        data = response.json()['data']
        self.assertEqual(data['amount'], '450.00')
        self.assertEqual(data['payment_method'], 'UPI')
        self.assertEqual(data['account_id'], self.sbi.id)
        self.assertEqual(data['category'], 'Food & Dining')
        self.assertEqual((Expense.objects.count(), ExpenseKeywordHint.objects.count()), before)
        self.assertEqual(self.balance(self.sbi), Decimal('35000.00'))

    def test_only_offers_the_users_own_categories(self):
        data = self.post_json('parse-expense', {'text': 'xyzzy 100'}).json()['data']
        self.assertIsNone(data['category'])
        self.assertIn('category', data['check'])

    def test_empty_text_and_bad_json(self):
        self.assertFalse(self.post_json('parse-expense', {'text': '  '}).json()['success'])
        bad = self.client.post(reverse('parse-expense'), 'not json', content_type='application/json')
        self.assertEqual(bad.status_code, 400)

    def test_query_budget(self):
        self.post_json('parse-expense', {'text': 'swiggy 100'})  # warm profile/tier caches
        with self.assertNumQueries(6):
            self.post_json('parse-expense', {'text': 'swiggy dinner 450 upi'})

    def test_requires_login(self):
        self.client.logout()
        self.assertEqual(self.post_json('parse-expense', {'text': 'chai 20'}).status_code, 302)


class DataEndpointTests(ComposerBase):
    def test_payload_has_everything_the_card_needs(self):
        data = self.client.get(reverse('expense-composer-data')).json()['data']
        self.assertTrue({'Food & Dining', 'Shopping', 'Transport'} <= set(data['categories']))
        self.assertEqual({a['name'] for a in data['accounts']}, {'SBI Savings', 'Cash'})
        self.assertTrue(any('35,000' in a['label'] for a in data['accounts']))
        self.assertEqual(data['default_currency'], '₹')
        self.assertEqual(len(data['currencies']), 10)
        self.assertEqual(data['default_account_source'], 'first')
        self.assertEqual(data['today'], timezone.localdate().isoformat())

    def test_inactive_accounts_are_hidden(self):
        Account.objects.create(user=self.user, name='Old Bank', balance=0, is_active=False)
        data = self.client.get(reverse('expense-composer-data')).json()['data']
        self.assertNotIn('Old Bank', [a['name'] for a in data['accounts']])

    def test_default_account_is_last_used(self):
        Expense.objects.create(user=self.user, date=date.today(), amount=10, description='x',
                               category='Transport', account=self.cash, payment_method='Cash')
        data = self.client.get(reverse('expense-composer-data')).json()['data']
        self.assertEqual(data['default_account_id'], self.cash.id)
        self.assertEqual(data['default_account_source'], 'last_used')
        self.assertEqual(data['default_payment_method'], 'Cash')

    def test_other_users_data_is_not_exposed(self):
        other = User.objects.create_user(username='other', password='password')
        Category.objects.create(user=other, name='Secret')
        Account.objects.create(user=other, name='Hidden', balance=5)
        data = self.client.get(reverse('expense-composer-data')).json()['data']
        self.assertNotIn('Secret', data['categories'])
        self.assertNotIn('Hidden', [a['name'] for a in data['accounts']])


class SaveEndpointTests(ComposerBase):
    def test_add_saves_exactly_one_expense_with_all_six_fields(self):
        response = self.post_json('expense-composer-save', self.payload(date='2026-10-03'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Expense.objects.count(), 1)
        e = Expense.objects.get()
        self.assertEqual(e.amount, Decimal('450.00'))
        self.assertEqual(e.date, date(2026, 10, 3))
        self.assertEqual(e.description, 'Swiggy dinner')
        self.assertEqual(e.category, 'Food & Dining')
        self.assertEqual(e.account, self.sbi)
        self.assertEqual(e.payment_method, 'UPI')
        self.assertEqual(e.currency, '₹')
        body = response.json()
        self.assertEqual(body['expense']['amount_display'], '₹450')
        self.assertEqual(body['account_id'], self.sbi.id)
        self.assertIn('34,550', body['account_label'])

    def test_balance_is_applied(self):
        self.post_json('expense-composer-save', self.payload())
        self.assertEqual(self.balance(self.sbi), Decimal('34550.00'))

    def test_double_submit_is_idempotent(self):
        first = self.post_json('expense-composer-save', self.payload())
        second = self.post_json('expense-composer-save', self.payload())
        self.assertFalse(first.json()['duplicate'])
        self.assertTrue(second.json()['duplicate'])
        self.assertEqual(second.json()['expense']['id'], first.json()['expense']['id'])
        self.assertEqual(Expense.objects.count(), 1)
        self.assertEqual(self.balance(self.sbi), Decimal('34550.00'))

    def test_different_keys_make_different_expenses(self):
        self.post_json('expense-composer-save', self.payload(key='a'))
        self.post_json('expense-composer-save', self.payload(key='b'))
        self.assertEqual(Expense.objects.count(), 2)

    def test_validation(self):
        for bad in ({'amount': '0'}, {'amount': '-5'}, {'amount': 'abc'}, {'category': ''},
                    {'category': 'Nope'}, {'date': 'not-a-date'}):
            with self.subTest(bad=bad):
                response = self.post_json('expense-composer-save', self.payload(key='v-' + str(sorted(bad)), **bad))
                self.assertEqual(response.status_code, 400)
                self.assertFalse(response.json()['success'])
        self.assertEqual(Expense.objects.count(), 0)

    def test_blank_description_falls_back_to_category(self):
        self.post_json('expense-composer-save', self.payload(description='  '))
        self.assertEqual(Expense.objects.get().description, 'Food & Dining')

    def test_foreign_account_is_rejected(self):
        other = User.objects.create_user(username='intruder', password='password')
        theirs = Account.objects.create(user=other, name='Theirs', balance=10)
        response = self.post_json('expense-composer-save', self.payload(account_id=theirs.id))
        self.assertEqual(response.status_code, 400)
        self.assertEqual(Expense.objects.count(), 0)

    def test_foreign_currency_converts_to_base(self):
        with patch('expenses.models.get_exchange_rate', return_value=Decimal('80')):
            self.post_json('expense-composer-save', self.payload(currency='$', amount='10', account_id=''))
        e = Expense.objects.get()
        self.assertEqual(e.currency, '$')
        self.assertEqual(e.base_amount, Decimal('800.00'))

    def test_amount_with_indian_grouping_is_accepted(self):
        self.post_json('expense-composer-save', self.payload(amount='1,25,000'))
        self.assertEqual(Expense.objects.get().amount, Decimal('125000.00'))

    def test_anonymous_cannot_save(self):
        self.client.logout()
        response = self.post_json('expense-composer-save', self.payload())
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Expense.objects.count(), 0)

    def test_analytics_never_carry_amounts_text_or_names(self):
        with patch('expenses.views.composer.ph_capture') as capture:
            body = self.payload(meta={'mode': 'quick', 'edited': ['category', 'bogus'], 'add_another': True,
                                      'duration_ms': 4200})
            self.post_json('expense-composer-save', body)
        (user, event, props), _ = capture.call_args
        self.assertEqual(event, 'expense_added')
        self.assertEqual(props['mode'], 'quick')
        self.assertTrue(props['edited_before_add'])
        self.assertEqual(props['edited_count'], 1)
        self.assertTrue(props['add_another'])
        self.assertEqual(props['duration_ms'], 4200)
        flat = json.dumps(props)
        for secret in ('450', 'Swiggy', 'SBI', 'Food'):
            self.assertNotIn(secret, flat)

    def test_replay_does_not_fire_analytics_twice(self):
        with patch('expenses.views.composer.ph_capture') as capture:
            self.post_json('expense-composer-save', self.payload())
            self.post_json('expense-composer-save', self.payload())
        self.assertEqual(capture.call_count, 1)


class UndoTests(ComposerBase):
    def test_undo_removes_expense_and_restores_balance(self):
        saved = self.post_json('expense-composer-save', self.payload()).json()
        self.assertEqual(self.balance(self.sbi), Decimal('34550.00'))
        response = self.client.post(saved['undo_url'])
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['success'])
        self.assertEqual(Expense.objects.count(), 0)
        self.assertEqual(self.balance(self.sbi), Decimal('35000.00'))
        self.assertIn('35,000', response.json()['account_label'])

    def test_undo_is_limited_to_recent_expenses(self):
        saved = self.post_json('expense-composer-save', self.payload()).json()
        Expense.objects.filter(uuid=saved['expense']['id']).update(
            created_at=timezone.now() - UNDO_WINDOW - timedelta(minutes=1))
        response = self.client.post(saved['undo_url'])
        self.assertEqual(response.status_code, 409)
        self.assertEqual(Expense.objects.count(), 1)

    def test_cannot_undo_someone_elses_expense(self):
        saved = self.post_json('expense-composer-save', self.payload()).json()
        stranger = User.objects.create_user(username='stranger', password='password')
        client = Client()
        client.login(username='stranger', password='password')
        self.assertEqual(client.post(saved['undo_url']).status_code, 404)
        self.assertEqual(Expense.objects.count(), 1)

    def test_undo_requires_post(self):
        saved = self.post_json('expense-composer-save', self.payload()).json()
        self.assertEqual(self.client.get(saved['undo_url']).status_code, 405)


class LearningTests(ComposerBase):
    def parse_category(self, text):
        return self.post_json('parse-expense', {'text': text}).json()['data']

    def test_save_remembers_keyword_choices(self):
        self.post_json('expense-composer-save', self.payload(key='l1', description='Swiggy dinner',
                                                              category='Shopping', account_id=self.cash.id,
                                                              payment_method='Cash'))
        hint = ExpenseKeywordHint.objects.get(user=self.user, keyword='swiggy')
        self.assertEqual((hint.category, hint.account, hint.payment_method), ('Shopping', self.cash, 'Cash'))
        data = self.parse_category('swiggy 200')
        self.assertEqual(data['category'], 'Shopping')
        self.assertEqual(data['account_id'], self.cash.id)
        self.assertEqual(data['payment_method'], 'Cash')

    def test_correction_changes_next_suggestion(self):
        self.post_json('expense-composer-save', self.payload(key='l1', category='Shopping'))
        self.assertEqual(self.parse_category('swiggy 100')['category'], 'Shopping')
        self.post_json('expense-composer-save', self.payload(key='l2', category='Transport',
                                                              account_id=self.cash.id, payment_method='Cash'))
        data = self.parse_category('swiggy 100')
        self.assertEqual(data['category'], 'Transport')
        self.assertEqual(data['account_id'], self.cash.id)
        hint = ExpenseKeywordHint.objects.get(user=self.user, keyword='swiggy')
        self.assertEqual(hint.use_count, 2)

    def test_explicit_text_still_beats_learned_hint(self):
        self.post_json('expense-composer-save', self.payload(key='l1', payment_method='Cash'))
        data = self.parse_category('swiggy 100 upi')
        self.assertEqual(data['payment_method'], 'UPI')

    def test_hints_are_per_user_and_capped(self):
        other = User.objects.create_user(username='other2', password='password')
        ExpenseKeywordHint.objects.create(user=other, keyword='swiggy', category='Secret')
        self.assertNotEqual(self.parse_category('swiggy 100')['category'], 'Secret')

        cap = ExpenseKeywordHint.MAX_PER_USER
        ExpenseKeywordHint.objects.bulk_create(
            [ExpenseKeywordHint(user=self.user, keyword='k%04d' % i, category='Transport') for i in range(cap)])
        self.post_json('expense-composer-save', self.payload(key='cap', description='Brandnew thing'))
        self.assertLessEqual(ExpenseKeywordHint.objects.filter(user=self.user).count(), cap)
        self.assertTrue(ExpenseKeywordHint.objects.filter(user=self.user, keyword='brandnew').exists())


class UsualTests(ComposerBase):
    def test_top_recurring_pairs_with_one_cheap_path(self):
        today = timezone.localdate()
        for i in range(3):
            Expense.objects.create(user=self.user, date=today - timedelta(days=i), amount=20, description='Chai',
                                   category='Food & Dining', account=self.cash, payment_method='Cash')
        Expense.objects.create(user=self.user, date=today, amount=999, description='One-off',
                               category='Shopping', account=self.cash, payment_method='UPI')
        usuals = self.client.get(reverse('expense-composer-data')).json()['data']['usuals']
        self.assertEqual(len(usuals), 1)
        self.assertEqual(usuals[0]['label'], 'chai 20')
        self.assertEqual(usuals[0]['category'], 'Food & Dining')
        self.assertEqual(usuals[0]['account_id'], self.cash.id)
        self.assertEqual(usuals[0]['payment_method'], 'Cash')

    def test_old_expenses_do_not_count(self):
        old = timezone.localdate() - timedelta(days=120)
        for _ in range(3):
            Expense.objects.create(user=self.user, date=old, amount=20, description='Chai',
                                   category='Food & Dining', account=self.cash, payment_method='Cash')
        self.assertEqual(self.client.get(reverse('expense-composer-data')).json()['data']['usuals'], [])

    def test_cache_is_dropped_after_save(self):
        self.client.get(reverse('expense-composer-data'))
        for i in range(2):
            self.post_json('expense-composer-save', self.payload(key='u%d' % i, description='Tea', amount='15'))
        self.assertEqual(len(self.client.get(reverse('expense-composer-data')).json()['data']['usuals']), 1)


class LimitTests(ComposerBase):
    def test_free_tier_blocks_the_91st_expense_at_add_time(self):
        profile = self.user.profile
        profile.tier = 'FREE'
        profile.save()
        limit = PLAN_DETAILS['FREE']['limits']['expenses_per_month']
        today = timezone.localdate()
        Expense.objects.bulk_create([
            Expense(user=self.user, date=today, amount=10, description='e%d' % i, category='Food & Dining',
                    base_amount=10) for i in range(limit)])
        # Opening the card is never blocked...
        self.assertEqual(self.client.get(reverse('expense-composer-data')).status_code, 200)
        # ...the limit is enforced when Add is pressed.
        response = self.post_json('expense-composer-save', self.payload(key='over'))
        self.assertEqual(response.status_code, 403)
        body = response.json()
        self.assertEqual(body['code'], 'limit')
        self.assertIn(str(limit), body['error'])
        self.assertEqual(body['upgrade_url'], reverse('pricing'))
        self.assertEqual(Expense.objects.filter(user=self.user).count(), limit)

    def test_last_free_slot_is_still_usable(self):
        profile = self.user.profile
        profile.tier = 'FREE'
        profile.save()
        limit = PLAN_DETAILS['FREE']['limits']['expenses_per_month']
        Expense.objects.bulk_create([
            Expense(user=self.user, date=timezone.localdate(), amount=10, description='e%d' % i,
                    category='Food & Dining', base_amount=10) for i in range(limit - 1)])
        self.assertEqual(self.post_json('expense-composer-save', self.payload(key='last')).status_code, 200)

    def test_back_dated_expense_outside_this_month_is_not_counted(self):
        profile = self.user.profile
        profile.tier = 'FREE'
        profile.save()
        limit = PLAN_DETAILS['FREE']['limits']['expenses_per_month']
        Expense.objects.bulk_create([
            Expense(user=self.user, date=timezone.localdate(), amount=10, description='e%d' % i,
                    category='Food & Dining', base_amount=10) for i in range(limit)])
        long_ago = (timezone.localdate() - timedelta(days=90)).isoformat()
        self.assertEqual(self.post_json('expense-composer-save', self.payload(key='old', date=long_ago)).status_code, 200)


class EventEndpointTests(ComposerBase):
    def send(self, event, props=None):
        return self.post_json('expense-composer-event', {'event': event, 'props': props or {}})

    def test_allowed_events_are_sanitised(self):
        with patch('expenses.views.composer.ph_capture') as capture:
            self.send('expense_form_opened', {'entry': 'fab', 'amount': '450', 'description': 'secret'})
            self.send('quick_add_parsed', {'success': True, 'fields_filled': 6, 'check_count': 99, 'text': 'x'})
            self.send('expense_field_edited', {'field': 'category'})
            self.send('your_usual_chip_used')
            self.send('voice_used', {'lang': 'hi'})
        sent = [(c.args[1], c.args[2]) for c in capture.call_args_list]
        self.assertEqual(sent[0], ('expense_form_opened', {'entry': 'fab'}))
        self.assertEqual(sent[1], ('quick_add_parsed', {'success': True, 'fields_filled': 6, 'check_count': 7}))
        self.assertEqual(sent[2], ('expense_field_edited', {'field': 'category'}))
        self.assertEqual(sent[3], ('your_usual_chip_used', {}))
        self.assertEqual(sent[4], ('voice_used', {'lang': 'hi'}))

    def test_unknown_events_and_fields_are_rejected(self):
        with patch('expenses.views.composer.ph_capture') as capture:
            self.assertEqual(self.send('something_else').status_code, 400)
            self.assertEqual(self.send('expense_field_edited', {'field': 'password'}).status_code, 400)
            self.assertEqual(self.send('expense_added', {'amount': 1}).status_code, 400)
            self.assertEqual(self.send('quick_add_parsed', {'fields_filled': 'abc'}).status_code, 200)
        self.assertEqual(capture.call_count, 1)

    def test_unknown_entry_is_bucketed(self):
        with patch('expenses.views.composer.ph_capture') as capture:
            self.send('expense_form_opened', {'entry': 'https://evil.example'})
        self.assertEqual(capture.call_args.args[2], {'entry': 'other'})


class DeepLinkAndEntryPointTests(ComposerBase):
    def test_add_url_still_works_and_opens_the_composer(self):
        response = self.client.get(reverse('expense-create'))
        self.assertContains(response, 'data-composer-autoopen')
        self.assertContains(response, 'id="tmr-composer"')

    def test_next_must_be_local(self):
        response = self.client.get(reverse('expense-create'), {'next': 'https://evil.example/x'})
        self.assertContains(response, 'data-next="%s"' % reverse('expense-list'))
        response = self.client.get(reverse('expense-create'), {'next': '/accounts/'})
        self.assertContains(response, 'data-next="/accounts/"')

    def test_old_formset_post_is_gone(self):
        response = self.client.post(reverse('expense-create'), {'form-TOTAL_FORMS': '1'})
        self.assertEqual(response.status_code, 405)
        self.assertEqual(Expense.objects.count(), 0)

    def test_composer_is_mounted_for_signed_in_users_only(self):
        self.assertContains(self.client.get(reverse('expense-list')), 'id="tmr-composer"')
        self.client.logout()
        self.assertNotContains(self.client.get(reverse('about')), 'id="tmr-composer"')

    def test_manifest_has_add_expense_shortcut(self):
        manifest = json.loads(self.client.get(reverse('manifest')).content)
        shortcut = manifest['shortcuts'][0]
        self.assertEqual(shortcut['url'], reverse('expense-create') + '?source=pwa')

    def test_dashboard_has_quick_add_bar(self):
        self.assertContains(self.client.get(reverse('home')), 'data-composer-bar-input')

    def test_edit_page_is_edit_only(self):
        e = Expense.objects.create(user=self.user, date=date.today(), amount=10, description='x',
                                   category='Food & Dining', account=self.cash)
        response = self.client.get(reverse('expense-edit', args=[e.uuid]))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'nl-input')
        self.assertNotContains(response, 'More options')
        self.assertNotContains(response, 'New Category')


class ThresholdTests(SimpleTestCase):
    def test_rupee_threshold_is_one_lakh(self):
        self.assertEqual(LARGE_AMOUNT_THRESHOLDS['₹'], 100000)
