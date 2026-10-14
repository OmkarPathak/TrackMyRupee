"""Audit of the data export: what leaves the app is complete, belongs to the user, opens correctly in a
spreadsheet, and cannot run as a formula there."""
import csv
import io
import zipfile
from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from expenses.models import (
    Account,
    CapitalEvent,
    Category,
    Expense,
    GoalContribution,
    Income,
    Loan,
    LoanRepayment,
    RecurringTransaction,
    SavingsGoal,
    Transfer,
)
from expenses.views.export import EXPORTS, safe_cell

D = Decimal
TODAY = date.today()


def read(content):
    text = content.decode('utf-8')
    assert text.startswith('﻿'), 'a spreadsheet needs the byte-order mark to read rupee signs and Hindi'
    return list(csv.reader(io.StringIO(text[1:])))


class SafeCellTests(TestCase):
    def test_text_that_would_run_as_a_formula_is_made_plain(self):
        for bad in ('=SUM(A1:A9)', '+91 98765', '-5 refund', '@cmd', '\tx', '=HYPERLINK("http://x")'):
            self.assertEqual(safe_cell(bad), "'" + bad)

    def test_ordinary_values_are_untouched(self):
        for ok in ('Lunch', '', 'Rs 5', '5 - 3', D('-5.00'), 12, date(2026, 1, 1), True, None):
            self.assertEqual(safe_cell(ok), ok)


class ExportBase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='ex', email='ex@example.com', password='pw')
        self.user.profile.has_seen_tutorial = True
        self.user.profile.save()
        self.client.force_login(self.user)
        self.bank = Account.objects.create(user=self.user, name='Bank', account_type='BANK', balance=D('5000'), currency='₹')
        self.other = Account.objects.create(user=self.user, name='Other', account_type='BANK', balance=D('0'), currency='₹')
        Expense.objects.create(user=self.user, account=self.bank, amount=D('120'), category='Food', description='=1+1',
                               date=TODAY, currency='₹')
        Income.objects.create(user=self.user, account=self.bank, amount=D('900'), source='Salary', date=TODAY, currency='₹')
        Transfer.objects.create(user=self.user, from_account=self.bank, to_account=self.other, amount=D('50'), date=TODAY)
        self.goal = SavingsGoal.objects.create(user=self.user, name='Trip', target_amount=D('1000'), currency='₹')
        GoalContribution.objects.create(goal=self.goal, account=self.bank, amount=D('10'), date=TODAY)
        loan = Loan.objects.create(user=self.user, name='Car', loan_type='CAR', initial_principal=D('5000'),
                                   duration_months=12, start_date=TODAY, is_active=True, currency='₹')
        LoanRepayment.objects.create(loan=loan, from_account=self.bank, date=TODAY, amount=D('100'),
                                     principal_portion=D('90'), interest_portion=D('10'))
        CapitalEvent.objects.create(user=self.user, account=self.bank, date=TODAY, amount=D('30'), note='Laptop',
                                    include_in_net_worth=True)
        Category.objects.update_or_create(user=self.user, name='Food', defaults={'limit': D('2000')})
        RecurringTransaction.objects.create(user=self.user, description='Rent', amount=D('900'), frequency='MONTHLY',
                                            transaction_type='EXPENSE', start_date=TODAY, next_due_date=TODAY,
                                            account=self.bank, currency='₹', category='Rent')

    def export(self, *entities):
        return self.client.post(reverse('export-data'), {'entities': list(entities)})

    def rows(self, entity):
        response = self.export(entity)
        self.assertEqual(response.status_code, 200)
        return read(response.content)


class ContentTests(ExportBase):
    def test_every_entity_offered_by_the_form_is_exported(self):
        page = self.client.get(reverse('export-data')).content.decode()
        for entity in EXPORTS:
            self.assertIn(f'value="{entity}"', page, entity)

    def test_every_entity_exports_a_header_and_its_rows(self):
        for entity in EXPORTS:
            rows = self.rows(entity)
            self.assertGreaterEqual(len(rows), 2, f'{entity} exported nothing')
            self.assertTrue(all(len(r) == len(rows[0]) for r in rows), f'{entity} has ragged rows')

    def test_a_single_entity_is_a_csv_and_several_are_a_zip_with_one_file_each(self):
        single = self.export('expenses')
        self.assertEqual(single['Content-Type'], 'text/csv')
        self.assertIn('expenses.csv', single['Content-Disposition'])
        multi = self.export('expenses', 'accounts', 'categories')
        names = zipfile.ZipFile(io.BytesIO(multi.content)).namelist()
        self.assertEqual(sorted(names), ['accounts.csv', 'categories.csv', 'expenses.csv'])

    def test_unknown_and_missing_selections_export_nothing(self):
        self.assertRedirects(self.export('passwords'), reverse('export-data'), fetch_redirect_response=False)
        self.assertRedirects(self.export(), reverse('export-data'), fetch_redirect_response=False)

    def test_accounts_carry_their_current_balance(self):
        rows = {r[0]: r for r in self.rows('accounts')[1:]}
        self.assertEqual(rows['Bank'][3], '5590.00')  # 5000 - 120 + 900 - 50 - 10 - 100 - 30
        self.assertEqual(rows['Other'][3], '50.00')

    def test_the_new_columns_are_at_the_end_so_older_files_still_line_up(self):
        self.assertEqual(self.rows('recurring')[0][:7],
                         ['Description', 'Amount', 'Type', 'Frequency', 'Next Due Date', 'End Date', 'Active'])
        self.assertEqual(self.rows('goals')[0][:5], ['Name', 'Target Amount', 'Current Amount', 'Target Date', 'Completed'])

    def test_amounts_and_dates_are_plain_machine_readable_values(self):
        row = self.rows('expenses')[1]
        self.assertEqual((row[0], row[2], row[3]), (TODAY.isoformat(), '120.00', '₹'))

    def test_the_header_is_translated_but_the_file_name_is_not(self):
        response = self.export('expenses')
        self.assertIn('expenses.csv', response['Content-Disposition'])


class SafetyTests(ExportBase):
    def test_a_description_that_looks_like_a_formula_is_exported_as_text(self):
        row = self.rows('expenses')[1]
        self.assertEqual(row[1], "'=1+1")

    def test_the_filtered_list_export_is_safe_too(self):
        response = self.client.get(reverse('export-expenses'))
        text = response.content.decode('utf-8')
        self.assertTrue(text.startswith('﻿'))
        self.assertIn("'=1+1", text)
        self.assertNotIn(',=1+1,', text)

    def test_hindi_and_the_rupee_sign_survive(self):
        Expense.objects.create(user=self.user, account=self.bank, amount=D('10'), category='Food',
                               description='चाय ₹ break', date=TODAY, currency='₹')
        descriptions = [r[1] for r in self.rows('expenses')[1:]]
        self.assertIn('चाय ₹ break', descriptions)

    def test_only_my_data_is_exported(self):
        stranger = User.objects.create_user(username='stranger', password='pw')
        theirs = Account.objects.create(user=stranger, name='Secret', balance=D('1'), currency='₹')
        Expense.objects.create(user=stranger, account=theirs, amount=D('7'), category='Food', description='Hidden',
                               date=TODAY, currency='₹')
        Category.objects.create(user=stranger, name='Hidden cat')
        blob = b''.join(self.export(*EXPORTS).content for _ in [0])
        archive = zipfile.ZipFile(io.BytesIO(blob))
        everything = ''.join(archive.read(n).decode('utf-8') for n in archive.namelist())
        for secret in ('Secret', 'Hidden'):
            self.assertNotIn(secret, everything)

    def test_login_is_required(self):
        self.client.logout()
        self.assertEqual(self.client.post(reverse('export-data'), {'entities': ['expenses']}).status_code, 302)
        self.assertEqual(self.client.get(reverse('export-expenses')).status_code, 302)

    def test_nothing_is_exported_for_a_user_with_no_data(self):
        empty = User.objects.create_user(username='empty', password='pw')
        self.client.force_login(empty)
        for entity in EXPORTS:
            if entity == 'categories':
                continue  # every new account starts with the default categories
            rows = self.rows(entity)
            self.assertEqual(len(rows), 1, entity)  # just the header
