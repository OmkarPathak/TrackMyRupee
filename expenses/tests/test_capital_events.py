import json
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from expenses.forms import CapitalEventForm
from expenses.models import (
    Account,
    CapitalEvent,
    Expense,
    JournalEntry,
    Loan,
    LoanInterestRate,
    LoanRepayment,
)
from expenses.services import LoanService

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

def _make_user(username='testuser', password='testpassword', tier='PRO'):
    user = User.objects.create_user(username=username, password=password)
    user.profile.tier = tier
    user.profile.currency = '₹'
    user.profile.has_seen_tutorial = True
    user.profile.save()
    return user


def _make_account(user, name='Savings Account', balance=Decimal('10000.00'), currency='₹'):
    return Account.objects.create(
        user=user, name=name, account_type='BANK',
        balance=balance, currency=currency, is_active=True,
    )


def _make_loan(user, name='Home Loan', principal=Decimal('500000.00'),
               months=120, loan_type='HOME', currency='₹', rate=Decimal('8.50')):
    loan = Loan.objects.create(
        user=user, name=name, loan_type=loan_type,
        initial_principal=principal, duration_months=months,
        start_date=date.today(), currency=currency, is_active=True,
    )
    LoanInterestRate.objects.create(loan=loan, interest_rate=rate, effective_date=loan.start_date)
    return loan


# ===========================================================================
# 1. MODEL TESTS
# ===========================================================================

class CapitalEventModelTest(TestCase):

    def setUp(self):
        self.user = _make_user()
        self.account = _make_account(self.user)
        self.loan = _make_loan(self.user)

    # --- creation & __str__ ---

    def test_creation_and_str(self):
        event = CapitalEvent.objects.create(
            user=self.user, amount=Decimal('5000.00'), date=date.today(),
            subtype='loan_down_payment', note='Down payment',
            linked_loan=self.loan, account=self.account, currency='₹',
        )
        self.assertEqual(event.amount, Decimal('5000.00'))
        self.assertEqual(event.subtype, 'loan_down_payment')
        self.assertEqual(event.linked_loan, self.loan)
        self.assertEqual(str(event), f"{date.today()} – Loan Down Payment – ₹5000.00")

    def test_defaults(self):
        event = CapitalEvent.objects.create(
            user=self.user, amount=Decimal('1000.00'), date=date.today(), subtype='other',
        )
        self.assertTrue(event.exclude_from_averages)
        self.assertTrue(event.exclude_from_budget)
        self.assertTrue(event.include_in_net_worth)
        self.assertEqual(event.currency, '₹')
        self.assertEqual(event.note, '')
        self.assertIsNone(event.linked_loan)
        self.assertIsNone(event.account)

    def test_all_subtypes_are_valid(self):
        valid_subtypes = [s[0] for s in CapitalEvent.SUBTYPE_CHOICES]
        for subtype in valid_subtypes:
            event = CapitalEvent.objects.create(
                user=self.user, amount=Decimal('1000.00'),
                date=date.today(), subtype=subtype,
            )
            self.assertEqual(event.subtype, subtype)

    # --- account balance effects ---

    def test_include_in_net_worth_decreases_balance_on_create(self):
        CapitalEvent.objects.create(
            user=self.user, amount=Decimal('2000.00'), date=date.today(),
            subtype='large_purchase', account=self.account, include_in_net_worth=True,
        )
        self.account.refresh_from_db()
        self.assertEqual(self.account.balance, Decimal('8000.00'))

    def test_exclude_from_net_worth_does_not_touch_balance(self):
        CapitalEvent.objects.create(
            user=self.user, amount=Decimal('2000.00'), date=date.today(),
            subtype='large_purchase', account=self.account, include_in_net_worth=False,
        )
        self.account.refresh_from_db()
        self.assertEqual(self.account.balance, Decimal('10000.00'))

    def test_no_account_does_not_touch_any_balance(self):
        CapitalEvent.objects.create(
            user=self.user, amount=Decimal('5000.00'), date=date.today(), subtype='other',
        )
        self.account.refresh_from_db()
        self.assertEqual(self.account.balance, Decimal('10000.00'))

    def test_balance_reversal_on_amount_update(self):
        event = CapitalEvent.objects.create(
            user=self.user, amount=Decimal('2000.00'), date=date.today(),
            subtype='large_purchase', account=self.account, include_in_net_worth=True,
        )
        self.account.refresh_from_db()
        self.assertEqual(self.account.balance, Decimal('8000.00'))

        event.amount = Decimal('3000.00')
        event.save()
        self.account.refresh_from_db()
        self.assertEqual(self.account.balance, Decimal('7000.00'))

    def test_balance_reversal_when_switching_accounts(self):
        event = CapitalEvent.objects.create(
            user=self.user, amount=Decimal('3000.00'), date=date.today(),
            subtype='large_purchase', account=self.account, include_in_net_worth=True,
        )
        other_account = _make_account(self.user, name='Other Account', balance=Decimal('5000.00'))

        event.account = other_account
        event.save()

        self.account.refresh_from_db()
        other_account.refresh_from_db()
        self.assertEqual(self.account.balance, Decimal('10000.00'))
        self.assertEqual(other_account.balance, Decimal('2000.00'))

    def test_balance_restored_when_toggling_net_worth_off(self):
        event = CapitalEvent.objects.create(
            user=self.user, amount=Decimal('3000.00'), date=date.today(),
            subtype='large_purchase', account=self.account, include_in_net_worth=True,
        )
        self.account.refresh_from_db()
        self.assertEqual(self.account.balance, Decimal('7000.00'))

        event.include_in_net_worth = False
        event.save()
        self.account.refresh_from_db()
        self.assertEqual(self.account.balance, Decimal('10000.00'))

    def test_balance_decremented_when_toggling_net_worth_on(self):
        event = CapitalEvent.objects.create(
            user=self.user, amount=Decimal('3000.00'), date=date.today(),
            subtype='large_purchase', account=self.account, include_in_net_worth=False,
        )
        self.account.refresh_from_db()
        self.assertEqual(self.account.balance, Decimal('10000.00'))

        event.include_in_net_worth = True
        event.save()
        self.account.refresh_from_db()
        self.assertEqual(self.account.balance, Decimal('7000.00'))

    def test_balance_restored_on_delete(self):
        event = CapitalEvent.objects.create(
            user=self.user, amount=Decimal('1500.00'), date=date.today(),
            subtype='large_purchase', account=self.account, include_in_net_worth=True,
        )
        self.account.refresh_from_db()
        self.assertEqual(self.account.balance, Decimal('8500.00'))

        event.delete()
        self.account.refresh_from_db()
        self.assertEqual(self.account.balance, Decimal('10000.00'))

    def test_delete_without_account_does_not_raise(self):
        event = CapitalEvent.objects.create(
            user=self.user, amount=Decimal('1000.00'), date=date.today(), subtype='other',
        )
        try:
            event.delete()
        except Exception as exc:  # pragma: no cover
            self.fail(f"event.delete() raised unexpectedly: {exc}")
        self.assertEqual(CapitalEvent.objects.filter(user=self.user).count(), 0)

    # --- multi-currency ---

    @patch('expenses.fx.get_exchange_rate')
    @patch('expenses.models.get_exchange_rate')
    def test_multi_currency_normalization_on_create(self, mock_rate, mock_fx_rate):
        mock_rate.return_value = Decimal('80.00')
        mock_fx_rate.return_value = Decimal('80.00')
        event = CapitalEvent.objects.create(
            user=self.user, amount=Decimal('100.00'), date=date.today(),
            subtype='large_purchase', account=self.account, currency='$',
        )
        self.assertEqual(event.exchange_rate, Decimal('80.00'))
        self.assertEqual(event.base_amount, Decimal('8000.00'))
        mock_rate.assert_called_with('$', '₹')

    @patch('expenses.fx.get_exchange_rate')
    @patch('expenses.models.get_exchange_rate')
    def test_multi_currency_account_balance_converted_correctly(self, mock_rate, mock_fx_rate):
        mock_rate.return_value = Decimal('80.00')
        mock_fx_rate.return_value = Decimal('80.00')
        usd_account = _make_account(self.user, name='USD Account', balance=Decimal('1000.00'), currency='$')
        CapitalEvent.objects.create(
            user=self.user, amount=Decimal('100.00'), date=date.today(),
            subtype='large_purchase', account=usd_account,
            currency='$', include_in_net_worth=True,
        )
        usd_account.refresh_from_db()
        # Account currency matches event currency, no conversion needed for balance deduction
        self.assertEqual(usd_account.balance, Decimal('900.00'))

    def test_same_currency_as_user_sets_exchange_rate_to_one(self):
        event = CapitalEvent.objects.create(
            user=self.user, amount=Decimal('500.00'), date=date.today(),
            subtype='other', currency='₹',
        )
        self.assertEqual(event.exchange_rate, Decimal('1.0'))
        self.assertEqual(event.base_amount, Decimal('500.00'))

    # --- ordering ---

    def test_default_ordering_is_most_recent_first(self):
        older = CapitalEvent.objects.create(
            user=self.user, amount=Decimal('100.00'),
            date=date.today() - timedelta(days=5), subtype='other',
        )
        newer = CapitalEvent.objects.create(
            user=self.user, amount=Decimal('200.00'),
            date=date.today(), subtype='other',
        )
        events = list(CapitalEvent.objects.filter(user=self.user))
        self.assertEqual(events[0], newer)
        self.assertEqual(events[1], older)


# ===========================================================================
# 2. LEDGER SHADOW WRITE TESTS
# ===========================================================================

class CapitalEventLedgerTest(TestCase):

    def setUp(self):
        self.user = _make_user()
        self.account = _make_account(self.user)

    @override_settings(LEDGER_WRITE_ENABLED=True, LEDGER_ENFORCE_BALANCED_WRITE=False)
    def test_create_writes_one_posted_journal_entry(self):
        event = CapitalEvent.objects.create(
            user=self.user, amount=Decimal('1000.00'), date=date.today(),
            subtype='large_purchase', account=self.account, currency='₹',
        )
        self.assertEqual(
            JournalEntry.objects.filter(
                source_type='CAPITAL_EVENT', source_id=event.id, status='POSTED',
            ).count(),
            1,
        )

    @override_settings(LEDGER_WRITE_ENABLED=True, LEDGER_ENFORCE_BALANCED_WRITE=False)
    def test_update_writes_reversal_and_new_entry(self):
        event = CapitalEvent.objects.create(
            user=self.user, amount=Decimal('1000.00'), date=date.today(),
            subtype='large_purchase', account=self.account, currency='₹',
        )
        event.amount = Decimal('1200.00')
        event.save()
        # 1 original POSTED + 1 REVERSED (old) + 1 new POSTED = 3
        self.assertEqual(
            JournalEntry.objects.filter(source_type='CAPITAL_EVENT', source_id=event.id).count(),
            3,
        )

    @override_settings(LEDGER_WRITE_ENABLED=True, LEDGER_ENFORCE_BALANCED_WRITE=False)
    def test_delete_writes_reversal_entry(self):
        event = CapitalEvent.objects.create(
            user=self.user, amount=Decimal('1000.00'), date=date.today(),
            subtype='large_purchase', account=self.account, currency='₹',
        )
        event_id = event.id
        event.delete()
        # 1 original POSTED + 1 REVERSED = 2
        self.assertEqual(
            JournalEntry.objects.filter(source_type='CAPITAL_EVENT', source_id=event_id).count(),
            2,
        )

    @override_settings(LEDGER_WRITE_ENABLED=False)
    def test_no_ledger_writes_when_disabled(self):
        event = CapitalEvent.objects.create(
            user=self.user, amount=Decimal('1000.00'), date=date.today(),
            subtype='large_purchase', account=self.account, currency='₹',
        )
        self.assertEqual(
            JournalEntry.objects.filter(source_type='CAPITAL_EVENT', source_id=event.id).count(),
            0,
        )


# ===========================================================================
# 3. FORM TESTS
# ===========================================================================

class CapitalEventFormTest(TestCase):

    def setUp(self):
        self.user = _make_user()
        self.account = _make_account(self.user)
        self.loan = _make_loan(self.user)

    def _valid_data(self, **overrides):
        data = {
            'date': date.today().isoformat(),
            'amount': '1500.00',
            'currency': '₹',
            'account': self.account.id,
            'subtype': 'large_purchase',
            'note': 'Test note',
            'exclude_from_averages': True,
            'exclude_from_budget': True,
            'include_in_net_worth': True,
        }
        data.update(overrides)
        return data

    def test_valid_form(self):
        form = CapitalEventForm(data=self._valid_data(), user=self.user)
        self.assertTrue(form.is_valid(), form.errors)

    def test_invalid_negative_amount(self):
        form = CapitalEventForm(data=self._valid_data(amount='-50.00'), user=self.user)
        self.assertFalse(form.is_valid())
        self.assertIn('amount', form.errors)

    def test_invalid_zero_amount(self):
        form = CapitalEventForm(data=self._valid_data(amount='0.00'), user=self.user)
        self.assertFalse(form.is_valid())
        self.assertIn('amount', form.errors)

    def test_missing_required_date(self):
        data = self._valid_data()
        data.pop('date')
        form = CapitalEventForm(data=data, user=self.user)
        self.assertFalse(form.is_valid())
        self.assertIn('date', form.errors)

    def test_missing_required_subtype(self):
        data = self._valid_data()
        data.pop('subtype')
        form = CapitalEventForm(data=data, user=self.user)
        self.assertFalse(form.is_valid())
        self.assertIn('subtype', form.errors)

    def test_linked_loan_is_optional(self):
        data = self._valid_data()
        data.pop('linked_loan', None)  # not present
        form = CapitalEventForm(data=data, user=self.user)
        self.assertTrue(form.is_valid(), form.errors)

    def test_linked_loan_accepted_when_provided(self):
        data = self._valid_data(linked_loan=self.loan.id)
        form = CapitalEventForm(data=data, user=self.user)
        self.assertTrue(form.is_valid(), form.errors)
        event = form.save(commit=False)
        event.user = self.user
        event.save()
        self.assertEqual(event.linked_loan, self.loan)

    def test_account_queryset_scoped_to_user(self):
        other_user = _make_user(username='other')
        other_account = _make_account(other_user, name='Other Bank')
        form = CapitalEventForm(user=self.user)
        self.assertIn(self.account, form.fields['account'].queryset)
        self.assertNotIn(other_account, form.fields['account'].queryset)

    def test_loan_queryset_scoped_to_user(self):
        other_user = _make_user(username='other2')
        other_loan = _make_loan(other_user, name='Other Loan')
        form = CapitalEventForm(user=self.user)
        self.assertIn(self.loan, form.fields['linked_loan'].queryset)
        self.assertNotIn(other_loan, form.fields['linked_loan'].queryset)

    def test_inactive_account_excluded_from_queryset(self):
        inactive = Account.objects.create(
            user=self.user, name='Old Account', account_type='BANK',
            balance=Decimal('0.00'), is_active=False,
        )
        form = CapitalEventForm(user=self.user)
        self.assertNotIn(inactive, form.fields['account'].queryset)

    def test_inactive_loan_excluded_from_queryset(self):
        inactive_loan = Loan.objects.create(
            user=self.user, name='Paid Off', loan_type='PERSONAL',
            initial_principal=Decimal('1000.00'), duration_months=12,
            start_date=date.today(), is_active=False,
        )
        form = CapitalEventForm(user=self.user)
        self.assertNotIn(inactive_loan, form.fields['linked_loan'].queryset)

    def test_currency_default_matches_user_profile(self):
        form = CapitalEventForm(user=self.user)
        self.assertEqual(form.fields['currency'].initial, '₹')

    def test_instance_pre_populates_form(self):
        event = CapitalEvent.objects.create(
            user=self.user, amount=Decimal('5000.00'), date=date.today(),
            subtype='gift_given', note='Birthday gift', linked_loan=self.loan,
            account=self.account,
        )
        form = CapitalEventForm(instance=event, user=self.user)
        self.assertEqual(form.initial.get('amount') or form['amount'].value(), Decimal('5000.00'))


# ===========================================================================
# 4. VIEW TESTS — authentication & ownership
# ===========================================================================

class CapitalEventAuthTest(TestCase):

    def setUp(self):
        self.client = Client()
        self.user = _make_user()
        self.other_user = _make_user(username='hacker', password='hackerpass')
        self.account = _make_account(self.user)
        self.event = CapitalEvent.objects.create(
            user=self.user, amount=Decimal('5000.00'), date=date.today(), subtype='other',
        )

    def test_list_redirects_anonymous(self):
        response = self.client.get(reverse('capital-event-list'))
        self.assertEqual(response.status_code, 302)

    def test_create_redirects_anonymous(self):
        response = self.client.get(reverse('capital-event-create'))
        self.assertEqual(response.status_code, 302)

    def test_edit_redirects_anonymous(self):
        response = self.client.get(reverse('capital-event-edit', kwargs={'pk': self.event.pk}))
        self.assertEqual(response.status_code, 302)

    def test_delete_redirects_anonymous(self):
        response = self.client.get(reverse('capital-event-delete', kwargs={'pk': self.event.pk}))
        self.assertEqual(response.status_code, 302)

    def test_edit_returns_404_for_other_user(self):
        self.client.login(username='hacker', password='hackerpass')
        response = self.client.get(reverse('capital-event-edit', kwargs={'pk': self.event.pk}))
        self.assertEqual(response.status_code, 404)

    def test_delete_returns_404_for_other_user(self):
        self.client.login(username='hacker', password='hackerpass')
        response = self.client.get(reverse('capital-event-delete', kwargs={'pk': self.event.pk}))
        self.assertEqual(response.status_code, 404)

    def test_convert_returns_404_for_other_user(self):
        self.client.login(username='hacker', password='hackerpass')
        response = self.client.post(reverse('capital-event-convert', kwargs={'pk': self.event.pk}))
        self.assertEqual(response.status_code, 404)


# ===========================================================================
# 5. VIEW TESTS — list
# ===========================================================================

class CapitalEventListViewTest(TestCase):

    def setUp(self):
        self.client = Client()
        self.user = _make_user()
        self.client.login(username='testuser', password='testpassword')

    def test_empty_list(self):
        response = self.client.get(reverse('capital-event-list'))
        self.assertEqual(response.status_code, 200)
        self.assertQuerySetEqual(response.context['events'], [])

    def test_only_shows_own_events(self):
        other_user = _make_user(username='stranger')
        own = CapitalEvent.objects.create(
            user=self.user, amount=Decimal('1000.00'), date=date.today(), subtype='other',
        )
        CapitalEvent.objects.create(
            user=other_user, amount=Decimal('9999.00'), date=date.today(), subtype='other',
        )
        response = self.client.get(reverse('capital-event-list'))
        self.assertIn(own, response.context['events'])
        self.assertEqual(response.context['events'].count(), 1)

    def test_filter_by_subtype(self):
        medical = CapitalEvent.objects.create(
            user=self.user, amount=Decimal('500.00'), date=date.today(), subtype='medical_lump_sum',
        )
        gift = CapitalEvent.objects.create(
            user=self.user, amount=Decimal('1000.00'), date=date.today(), subtype='gift_given',
        )
        response = self.client.get(reverse('capital-event-list'), {'subtype': 'medical_lump_sum'})
        events = list(response.context['events'])
        self.assertIn(medical, events)
        self.assertNotIn(gift, events)

    def test_invalid_subtype_filter_returns_empty(self):
        CapitalEvent.objects.create(
            user=self.user, amount=Decimal('500.00'), date=date.today(), subtype='other',
        )
        response = self.client.get(reverse('capital-event-list'), {'subtype': 'nonexistent'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['events'].count(), 0)

    def test_context_includes_subtype_choices(self):
        response = self.client.get(reverse('capital-event-list'))
        self.assertIn('subtype_choices', response.context)
        self.assertEqual(response.context['subtype_choices'], CapitalEvent.SUBTYPE_CHOICES)

    def test_context_includes_selected_subtype(self):
        response = self.client.get(reverse('capital-event-list'), {'subtype': 'gift_given'})
        self.assertEqual(response.context['selected_subtypes'], ['gift_given'])

    def test_pagination_present_with_many_events(self):
        for i in range(25):
            CapitalEvent.objects.create(
                user=self.user, amount=Decimal('100.00'),
                date=date.today(), subtype='other',
            )
        response = self.client.get(reverse('capital-event-list'))
        self.assertEqual(response.status_code, 200)
        print("EVENTS COUNT:", len(response.context['events']))
        print("PAGINATOR COUNT:", getattr(response.context.get('paginator'), 'count', None))
        self.assertTrue(response.context['is_paginated'])


# ===========================================================================
# 6. VIEW TESTS — create
# ===========================================================================

class CapitalEventCreateViewTest(TestCase):

    def setUp(self):
        self.client = Client()
        self.user = _make_user()
        self.account = _make_account(self.user)
        self.loan = _make_loan(self.user)
        self.client.login(username='testuser', password='testpassword')

    def test_get_returns_200_with_form(self):
        response = self.client.get(reverse('capital-event-create'))
        self.assertEqual(response.status_code, 200)
        self.assertIn('form', response.context)

    def test_get_includes_user_loans_in_context(self):
        response = self.client.get(reverse('capital-event-create'))
        self.assertIn(self.loan, response.context['user_loans'])

    def test_prefill_from_expense_id(self):
        expense = Expense.objects.create(
            user=self.user, date=date.today(), amount=Decimal('1500.00'),
            description='Dentist', category='Medical',
            account=self.account, currency='₹',
        )
        response = self.client.get(reverse('capital-event-create'), {'from_expense': expense.id})
        self.assertEqual(response.status_code, 200)
        initial = response.context['form'].initial
        self.assertEqual(initial['amount'], Decimal('1500.00'))
        self.assertEqual(initial['note'], 'Dentist')
        self.assertEqual(initial['account'], self.account.id)

    def test_prefill_with_nonexistent_expense_id_is_silently_ignored(self):
        response = self.client.get(reverse('capital-event-create'), {'from_expense': 99999})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['form'].initial, {})

    def test_prefill_with_other_users_expense_is_silently_ignored(self):
        other_user = _make_user(username='other3')
        other_account = _make_account(other_user, name='Other3 Bank')
        expense = Expense.objects.create(
            user=other_user, date=date.today(), amount=Decimal('9999.00'),
            description='Private', category='Other', account=other_account, currency='₹',
        )
        response = self.client.get(reverse('capital-event-create'), {'from_expense': expense.id})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['form'].initial, {})

    def test_post_creates_event_and_redirects(self):
        count_before = CapitalEvent.objects.filter(user=self.user).count()
        data = {
            'date': date.today().isoformat(),
            'amount': '3000.00',
            'currency': '₹',
            'account': self.account.id,
            'subtype': 'medical_lump_sum',
            'note': 'Hospital bill',
            'exclude_from_averages': '1',
            'exclude_from_budget': '1',
            'include_in_net_worth': '1',
        }
        response = self.client.post(reverse('capital-event-create'), data)
        self.assertRedirects(response, reverse('capital-event-list'))
        self.assertEqual(CapitalEvent.objects.filter(user=self.user).count(), count_before + 1)
        event = CapitalEvent.objects.filter(user=self.user).latest('created_at')
        self.assertEqual(event.amount, Decimal('3000.00'))
        self.assertEqual(event.subtype, 'medical_lump_sum')

    def test_post_invalid_data_re_renders_form(self):
        data = {
            'date': date.today().isoformat(),
            'amount': '-100.00',
            'currency': '₹',
            'subtype': 'other',
        }
        response = self.client.post(reverse('capital-event-create'), data)
        self.assertEqual(response.status_code, 200)
        self.assertIn('form', response.context)
        self.assertFalse(response.context['form'].is_valid())

    def test_post_with_expense_deletion(self):
        expense = Expense.objects.create(
            user=self.user, date=date.today(), amount=Decimal('1500.00'),
            description='Dentist', category='Medical', account=self.account, currency='₹',
        )
        data = {
            'date': date.today().isoformat(),
            'amount': '1500.00',
            'currency': '₹',
            'account': self.account.id,
            'subtype': 'medical_lump_sum',
            'note': 'Dentist',
            'from_expense_id': expense.id,
            'delete_source_expense': '1',
        }
        response = self.client.post(reverse('capital-event-create'), data)
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Expense.objects.filter(id=expense.id).exists())

    def test_post_without_expense_deletion_flag_keeps_expense(self):
        expense = Expense.objects.create(
            user=self.user, date=date.today(), amount=Decimal('1500.00'),
            description='Dentist', category='Medical', account=self.account, currency='₹',
        )
        data = {
            'date': date.today().isoformat(),
            'amount': '1500.00',
            'currency': '₹',
            'account': self.account.id,
            'subtype': 'medical_lump_sum',
            'note': 'Dentist',
            'from_expense_id': expense.id,
            # delete_source_expense deliberately omitted
        }
        response = self.client.post(reverse('capital-event-create'), data)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Expense.objects.filter(id=expense.id).exists())

    def test_post_with_nonexistent_expense_id_does_not_raise(self):
        data = {
            'date': date.today().isoformat(),
            'amount': '500.00',
            'currency': '₹',
            'account': self.account.id,
            'subtype': 'other',
            'note': 'Test',
            'from_expense_id': 99999,
            'delete_source_expense': '1',
        }
        response = self.client.post(reverse('capital-event-create'), data)
        self.assertEqual(response.status_code, 302)


# ===========================================================================
# 7. VIEW TESTS — update
# ===========================================================================

class CapitalEventUpdateViewTest(TestCase):

    def setUp(self):
        self.client = Client()
        self.user = _make_user()
        self.account = _make_account(self.user)
        self.loan = _make_loan(self.user)
        self.event = CapitalEvent.objects.create(
            user=self.user, amount=Decimal('5000.00'), date=date.today(),
            subtype='other', note='Old note',
        )
        self.url = reverse('capital-event-edit', kwargs={'pk': self.event.pk})
        self.client.login(username='testuser', password='testpassword')

    def test_get_returns_200_with_populated_form(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertIn('form', response.context)
        self.assertIn('event', response.context)

    def test_get_includes_user_loans_in_context(self):
        response = self.client.get(self.url)
        self.assertIn(self.loan, response.context['user_loans'])

    def test_post_updates_amount_and_note(self):
        data = {
            'date': date.today().isoformat(),
            'amount': '6000.00',
            'currency': '₹',
            'account': self.account.id,
            'subtype': 'other',
            'note': 'New note',
        }
        response = self.client.post(self.url, data)
        self.assertRedirects(response, reverse('capital-event-list'))
        self.event.refresh_from_db()
        self.assertEqual(self.event.amount, Decimal('6000.00'))
        self.assertEqual(self.event.note, 'New note')

    def test_post_can_uncheck_averages_and_net_worth_flags(self):
        self.event.account = self.account
        self.event.include_in_net_worth = True
        self.event.exclude_from_averages = True
        self.event.save()

        data = {
            'date': date.today().isoformat(),
            'amount': '5000.00',
            'currency': '₹',
            'account': self.account.id,
            'subtype': 'other',
            'note': 'Updated flags',
            'exclude_from_budget': '1',
            # Intentionally omit exclude_from_averages and include_in_net_worth to uncheck them.
        }
        response = self.client.post(self.url, data)
        self.assertRedirects(response, reverse('capital-event-list'))

        self.event.refresh_from_db()
        self.account.refresh_from_db()
        self.assertFalse(self.event.exclude_from_averages)
        self.assertTrue(self.event.exclude_from_budget)
        self.assertFalse(self.event.include_in_net_worth)
        self.assertEqual(self.account.balance, Decimal('10000.00'))

    def test_post_invalid_data_re_renders_form(self):
        data = {
            'date': date.today().isoformat(),
            'amount': '0.00',  # invalid
            'currency': '₹',
            'subtype': 'other',
        }
        response = self.client.post(self.url, data)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context['form'].is_valid())

    def test_post_updates_account_and_adjusts_balances(self):
        # Attach to account1 first
        self.event.account = self.account
        self.event.include_in_net_worth = True
        self.event.save()
        self.account.refresh_from_db()
        balance_after_create = self.account.balance  # 5000

        other_account = _make_account(self.user, name='Cash', balance=Decimal('8000.00'))
        data = {
            'date': date.today().isoformat(),
            'amount': '5000.00',
            'currency': '₹',
            'account': other_account.id,
            'subtype': 'other',
            'note': '',
            'include_in_net_worth': '1',
        }
        self.client.post(self.url, data)
        self.account.refresh_from_db()
        other_account.refresh_from_db()
        # Original account restored
        self.assertEqual(self.account.balance, balance_after_create + Decimal('5000.00'))
        # New account decremented
        self.assertEqual(other_account.balance, Decimal('3000.00'))


# ===========================================================================
# 8. VIEW TESTS — delete
# ===========================================================================

class CapitalEventDeleteViewTest(TestCase):

    def setUp(self):
        self.client = Client()
        self.user = _make_user()
        self.event = CapitalEvent.objects.create(
            user=self.user, amount=Decimal('5000.00'), date=date.today(), subtype='other',
        )
        self.url = reverse('capital-event-delete', kwargs={'pk': self.event.pk})
        self.client.login(username='testuser', password='testpassword')

    def test_get_shows_confirmation_page(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)

    def test_post_deletes_and_redirects(self):
        response = self.client.post(self.url)
        self.assertRedirects(response, reverse('capital-event-list'))
        self.assertFalse(CapitalEvent.objects.filter(id=self.event.id).exists())

    def test_delete_restores_account_balance(self):
        account = _make_account(self.user, balance=Decimal('10000.00'))
        event = CapitalEvent.objects.create(
            user=self.user, amount=Decimal('2000.00'), date=date.today(),
            subtype='large_purchase', account=account, include_in_net_worth=True,
        )
        account.refresh_from_db()
        self.assertEqual(account.balance, Decimal('8000.00'))

        self.client.post(reverse('capital-event-delete', kwargs={'pk': event.pk}))
        account.refresh_from_db()
        self.assertEqual(account.balance, Decimal('10000.00'))


# ===========================================================================
# 9. VIEW TESTS — convert to expense
# ===========================================================================

class CapitalEventConvertViewTest(TestCase):

    def setUp(self):
        self.client = Client()
        self.user = _make_user()
        self.account = _make_account(self.user)
        self.client.login(username='testuser', password='testpassword')

    def test_post_creates_expense_and_deletes_event(self):
        event = CapitalEvent.objects.create(
            user=self.user, amount=Decimal('4500.00'), date=date.today(),
            subtype='large_purchase', note='Laptop', account=self.account, currency='₹',
        )
        url = reverse('capital-event-convert', kwargs={'pk': event.pk})
        response = self.client.post(url)
        self.assertRedirects(response, reverse('expense-list'))
        self.assertFalse(CapitalEvent.objects.filter(id=event.id).exists())
        expense = Expense.objects.get(user=self.user, amount=Decimal('4500.00'))
        self.assertEqual(expense.description, 'Laptop')
        self.assertEqual(expense.account, self.account)
        self.assertEqual(expense.currency, '₹')

    def test_post_uses_subtype_display_when_note_is_empty(self):
        event = CapitalEvent.objects.create(
            user=self.user, amount=Decimal('2000.00'), date=date.today(),
            subtype='gift_given', note='', account=self.account,
        )
        self.client.post(reverse('capital-event-convert', kwargs={'pk': event.pk}))
        expense = Expense.objects.get(user=self.user, amount=Decimal('2000.00'))
        self.assertEqual(expense.description, 'Gift Given')

    def test_get_method_not_allowed(self):
        event = CapitalEvent.objects.create(
            user=self.user, amount=Decimal('1000.00'), date=date.today(), subtype='other',
        )
        url = reverse('capital-event-convert', kwargs={'pk': event.pk})
        response = self.client.get(url)
        # GET should not delete the event
        self.assertTrue(CapitalEvent.objects.filter(id=event.id).exists())

    def test_convert_with_account_none_still_works(self):
        event = CapitalEvent.objects.create(
            user=self.user, amount=Decimal('500.00'), date=date.today(),
            subtype='other', note='No account', account=None,
        )
        url = reverse('capital-event-convert', kwargs={'pk': event.pk})
        response = self.client.post(url)
        self.assertEqual(response.status_code, 302)
        expense = Expense.objects.get(user=self.user, amount=Decimal('500.00'))
        self.assertIsNone(expense.account)


# ===========================================================================
# 10. VIEW TESTS — AJAX loans endpoint
# ===========================================================================

class CapitalEventLoansAjaxTest(TestCase):

    def setUp(self):
        self.client = Client()
        self.user = _make_user()
        self.loan = _make_loan(self.user)
        self.url = reverse('capital-event-loans-ajax')

    def test_returns_empty_list_for_anonymous_user(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(response.content), {'loans': []})

    def test_returns_active_loans_for_authenticated_user(self):
        self.client.login(username='testuser', password='testpassword')
        response = self.client.get(self.url)
        data = json.loads(response.content)
        self.assertEqual(len(data['loans']), 1)
        self.assertEqual(data['loans'][0]['name'], 'Home Loan')
        self.assertIn('id', data['loans'][0])
        self.assertIn('loan_type', data['loans'][0])

    def test_excludes_inactive_loans(self):
        self.client.login(username='testuser', password='testpassword')
        inactive = Loan.objects.create(
            user=self.user, name='Paid Off', loan_type='PERSONAL',
            initial_principal=Decimal('1000.00'), duration_months=12,
            start_date=date.today(), is_active=False,
        )
        response = self.client.get(self.url)
        data = json.loads(response.content)
        ids = [l['id'] for l in data['loans']]
        self.assertNotIn(inactive.id, ids)
        self.assertIn(self.loan.id, ids)

    def test_excludes_other_users_loans(self):
        self.client.login(username='testuser', password='testpassword')
        other_user = _make_user(username='stranger2')
        other_loan = _make_loan(other_user, name='Stranger Loan')
        response = self.client.get(self.url)
        data = json.loads(response.content)
        ids = [l['id'] for l in data['loans']]
        self.assertNotIn(other_loan.id, ids)


# ===========================================================================
# 11. LOAN SERVICE INTEGRATION — prepayment reduces remaining principal
# ===========================================================================

class CapitalEventLoanServiceIntegrationTest(TestCase):

    def setUp(self):
        self.user = _make_user()
        self.account = _make_account(self.user, balance=Decimal('1000000.00'))
        self.loan = _make_loan(
            self.user, principal=Decimal('500000.00'),
            months=120, rate=Decimal('8.50'),
        )

    def test_loan_prepayment_reduces_remaining_principal(self):
        prepayment = CapitalEvent.objects.create(
            user=self.user, amount=Decimal('100000.00'), date=date.today(),
            subtype='loan_prepayment', linked_loan=self.loan, account=self.account,
        )
        summary = LoanService.get_loan_summary(self.loan)
        self.assertEqual(summary['remaining_principal'], 400000.0)

    def test_multiple_prepayments_are_summed(self):
        CapitalEvent.objects.create(
            user=self.user, amount=Decimal('50000.00'), date=date.today(),
            subtype='loan_prepayment', linked_loan=self.loan,
        )
        CapitalEvent.objects.create(
            user=self.user, amount=Decimal('30000.00'), date=date.today(),
            subtype='loan_prepayment', linked_loan=self.loan,
        )
        summary = LoanService.get_loan_summary(self.loan)
        self.assertEqual(summary['remaining_principal'], 420000.0)

    def test_down_payment_subtype_reduces_principal(self):
        # Both 'loan_down_payment' and 'loan_prepayment' reduce the tracked principal
        CapitalEvent.objects.create(
            user=self.user, amount=Decimal('100000.00'), date=date.today(),
            subtype='loan_down_payment', linked_loan=self.loan,
        )
        summary = LoanService.get_loan_summary(self.loan)
        self.assertEqual(summary['remaining_principal'], 400000.0)

    def test_prepayment_and_regular_repayments_combined(self):
        # Regular repayment covering 10k principal + 3k interest
        LoanRepayment.objects.create(
            loan=self.loan, amount=Decimal('13000.00'),
            principal_portion=Decimal('10000.00'),
            interest_portion=Decimal('3000.00'),
            date=date.today() - timedelta(days=30),
        )
        CapitalEvent.objects.create(
            user=self.user, amount=Decimal('100000.00'), date=date.today(),
            subtype='loan_prepayment', linked_loan=self.loan,
        )
        summary = LoanService.get_loan_summary(self.loan)
        # 500000 - 10000 (repayment) - 100000 (capital event) = 390000
        self.assertEqual(summary['remaining_principal'], 390000.0)

    def test_remaining_principal_never_goes_negative(self):
        # Prepay more than the principal
        CapitalEvent.objects.create(
            user=self.user, amount=Decimal('600000.00'), date=date.today(),
            subtype='loan_prepayment', linked_loan=self.loan,
        )
        summary = LoanService.get_loan_summary(self.loan)
        self.assertEqual(summary['remaining_principal'], 0.0)

    def test_amortization_schedule_reflects_prepayment(self):
        CapitalEvent.objects.create(
            user=self.user, amount=Decimal('400000.00'), date=date.today(),
            subtype='loan_prepayment', linked_loan=self.loan,
        )
        schedule = LoanService.generate_amortization_schedule(self.loan)
        # With only 100k left the schedule should be much shorter
        self.assertTrue(len(schedule) > 0)
        # All balances should be based on 100k, not 500k
        first_balance = schedule[0]['balance']
        self.assertLess(first_balance, 100000.0)

    def test_loan_with_no_events_unchanged(self):
        summary = LoanService.get_loan_summary(self.loan)
        self.assertEqual(summary['remaining_principal'], float(self.loan.initial_principal))

    def test_prepayment_from_unlinked_loan_does_not_affect_this_loan(self):
        other_loan = _make_loan(self.user, name='Car Loan', principal=Decimal('200000.00'))
        CapitalEvent.objects.create(
            user=self.user, amount=Decimal('50000.00'), date=date.today(),
            subtype='loan_prepayment', linked_loan=other_loan,
        )
        summary = LoanService.get_loan_summary(self.loan)
        self.assertEqual(summary['remaining_principal'], float(self.loan.initial_principal))


# ===========================================================================
# 12. LOAN REPAYMENT CLEAN VALIDATION WITH PREPAYMENTS
# ===========================================================================

class LoanRepaymentCleanWithCapitalEventTest(TestCase):

    def setUp(self):
        self.user = _make_user()
        self.account = _make_account(self.user, balance=Decimal('1000000.00'))
        self.loan = _make_loan(self.user, principal=Decimal('100000.00'), months=12)

    def test_repayment_validates_against_reduced_principal(self):
        # Prepay 90k → only 10k left
        CapitalEvent.objects.create(
            user=self.user, amount=Decimal('90000.00'), date=date.today(),
            subtype='loan_prepayment', linked_loan=self.loan,
        )
        # A repayment of 10001 should fail validation
        repayment = LoanRepayment(
            loan=self.loan,
            from_account=self.account,
            amount=Decimal('10001.00'),
            principal_portion=Decimal('10001.00'),
            interest_portion=Decimal('0.00'),
            date=date.today(),
        )
        from django.core.exceptions import ValidationError
        with self.assertRaises(ValidationError):
            repayment.clean()

    def test_repayment_within_reduced_principal_passes_validation(self):
        CapitalEvent.objects.create(
            user=self.user, amount=Decimal('90000.00'), date=date.today(),
            subtype='loan_prepayment', linked_loan=self.loan,
        )
        repayment = LoanRepayment(
            loan=self.loan,
            from_account=self.account,
            amount=Decimal('10000.00'),
            principal_portion=Decimal('9500.00'),
            interest_portion=Decimal('500.00'),
            date=date.today(),
        )
        # Should not raise
        repayment.clean()


# ===========================================================================
# 13. ANALYTICS FLAGS TESTS
# ===========================================================================

class CapitalEventAnalyticsFlagsTest(TestCase):

    def setUp(self):
        self.client = Client()
        self.user = _make_user()
        self.client.login(username='testuser', password='testpassword')
        self.account = _make_account(self.user)

    def test_defaults_exclude_from_averages_and_budget(self):
        event = CapitalEvent.objects.create(
            user=self.user, amount=Decimal('50000.00'),
            date=date.today(), subtype='large_purchase',
        )
        self.assertTrue(event.exclude_from_averages)
        self.assertTrue(event.exclude_from_budget)
        self.assertTrue(event.include_in_net_worth)

    def test_flags_can_be_overridden(self):
        event = CapitalEvent.objects.create(
            user=self.user, amount=Decimal('50000.00'),
            date=date.today(), subtype='large_purchase',
            exclude_from_averages=False,
            exclude_from_budget=False,
            include_in_net_worth=False,
        )
        self.assertFalse(event.exclude_from_averages)
        self.assertFalse(event.exclude_from_budget)
        self.assertFalse(event.include_in_net_worth)

    def test_exclude_from_averages_false_included_in_dashboard_total(self):
        CapitalEvent.objects.create(
            user=self.user, amount=Decimal('2500.00'),
            date=date.today(), subtype='large_purchase',
            exclude_from_averages=False,
        )
        response = self.client.get(reverse('home'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['total_expenses'], Decimal('2500.00'))

    def test_exclude_from_budget_false_included_in_category_breakdown(self):
        CapitalEvent.objects.create(
            user=self.user, amount=Decimal('1500.00'),
            date=date.today(), subtype='medical_lump_sum',
            exclude_from_budget=False,
        )
        response = self.client.get(reverse('home'))
        self.assertEqual(response.status_code, 200)
        
        found = False
        for cat in response.context['category_limits']:
            if cat['name'] == 'Medical Lump Sum':
                self.assertEqual(cat['total'], 1500.00)
                found = True
                break
        self.assertTrue(found, "Medical Lump Sum category not found in budget breakdown")

    def test_include_in_net_worth_false_excluded_from_ledger_total(self):
        CapitalEvent.objects.create(
            user=self.user, amount=Decimal('5000.00'),
            date=date.today(), subtype='other',
            account=self.account,
            include_in_net_worth=False,
        )
        response = self.client.get(reverse('account-detail', kwargs={'pk': self.account.pk}))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['filtered_net_total'], Decimal('0.00'))

    def test_mobile_spent_and_remaining_includes_capital_events(self):
        # Create a regular expense
        Expense.objects.create(
            user=self.user, amount=Decimal('500.00'),
            date=date.today(), category='Food',
            account=self.account, currency='₹',
        )
        # Create an excluded capital event
        CapitalEvent.objects.create(
            user=self.user, amount=Decimal('1000.00'),
            date=date.today(), subtype='large_purchase',
            exclude_from_averages=True,
        )
        # Create an included capital event
        CapitalEvent.objects.create(
            user=self.user, amount=Decimal('300.00'),
            date=date.today(), subtype='medical_lump_sum',
            exclude_from_averages=False,
        )

        response = self.client.get(reverse('home'))
        self.assertEqual(response.status_code, 200)
        
        # total_expenses should only include regular expense + included capital event = 500 + 300 = 800
        self.assertEqual(response.context['total_expenses'], Decimal('800.00'))
        
        # mobile_spent should include all capital events = 500 + 300 + 1000 = 1800
        self.assertEqual(response.context['mobile_spent'], Decimal('1800.00'))
        
        # mobile_remaining should be total_income - mobile_spent - total_investments
        expected_remaining = Decimal('0.00') - Decimal('1800.00') - Decimal('0.00')
        self.assertEqual(response.context['mobile_remaining'], expected_remaining)
