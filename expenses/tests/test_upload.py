import csv
import io
import re
from datetime import date, datetime
from unittest.mock import patch

import openpyxl
from django.contrib.auth.models import User
from django.contrib import messages
from django.test import Client, TestCase
from django.urls import reverse

from expenses.models import Category, Expense


class UploadViewTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='testuser', password='password')
        self.client = Client()
        self.client.login(username='testuser', password='password')
        self.user.profile.has_seen_tutorial = True
        self.user.profile.save()

    def create_excel_file(self, data):
        wb = openpyxl.Workbook()
        ws = wb.active
        for row in data:
            ws.append(row)
        file_io = io.BytesIO()
        wb.save(file_io)
        file_io.seek(0)
        return file_io

    def create_csv_file(self, data, encoding='utf-8'):
        file_io = io.BytesIO()
        content = io.StringIO()
        writer = csv.writer(content)
        for row in data:
            writer.writerow(row)
        file_io.write(content.getvalue().encode(encoding))
        file_io.seek(0)
        return file_io

    @patch('expenses.views.predict_category_ai')
    def test_excel_upload_robust_headers_success(self, mock_ai):
        mock_ai.return_value = 'Food'
        # Non-standard headers: 'Txn Date', 'Details', 'Spent'
        data = [
            ['Txn Date', 'Details', 'Spent', 'Notes'],
            ['2025-01-01', 'Lunch', 100, 'Test'],
            ['2025-01-02', 'Bus', 200, 'Test'],
        ]
        excel_file = self.create_excel_file(data)
        excel_file.name = 'test.xlsx'
        
        response = self.client.post(reverse('upload'), {
            'currency': '₹',
            'file': excel_file
        })
        
        self.assertEqual(response.status_code, 200)
        self.assertIn('results', response.context)
        results = response.context['results']
        self.assertEqual(results['created_count'], 2, f"Results: {results}")
        self.assertEqual(Expense.objects.count(), 2)
        # Category prioritization check (none in file, should use AI)
        self.assertEqual(Expense.objects.first().category, 'Food')

    @patch('expenses.views.predict_category_ai')
    def test_csv_upload_robust_headers_and_auto_categorize(self, mock_ai):
        mock_ai.return_value = 'Transport'
        # Headers: 'Dated', 'Narration', 'Value' (No category column)
        data = [
            ['Dated', 'Narration', 'Value'],
            ['2026-12-15', 'Uber Ride', 350.50],
            ['2026-02-10', 'Train Ticket', 1200],
        ]
        csv_file = self.create_csv_file(data)
        csv_file.name = 'test.csv'
        
        response = self.client.post(reverse('upload'), {
            'currency': '$',
            'file': csv_file
        })
        
        self.assertEqual(response.status_code, 200)
        self.assertIn('results', response.context)
        results = response.context['results']
        self.assertEqual(results['created_count'], 2, f"Results: {results}")
        self.assertEqual(Expense.objects.filter(currency='$').count(), 2)
        self.assertEqual(Expense.objects.get(description='Uber Ride').category, 'Transport')
        self.assertEqual(Expense.objects.get(description='Uber Ride').date, date(2026, 12, 15))

    def test_upload_deduplication(self):
        data = [
            ['Date', 'Amount', 'Description'],
            ['2025-01-01', 500, 'Rent'],
        ]
        csv_file = self.create_csv_file(data)
        csv_file.name = 'test.csv'
        
        # First upload
        self.client.post(reverse('upload'), {'currency': '₹', 'file': csv_file})
        self.assertEqual(Expense.objects.count(), 1)
        
        # Second upload (same file)
        csv_file.seek(0)
        response = self.client.post(reverse('upload'), {'currency': '₹', 'file': csv_file})
        
        self.assertIn('results', response.context)
        results = response.context['results']
        self.assertEqual(results['created_count'], 0)
        self.assertEqual(results['duplicate_count'], 1)
        self.assertEqual(Expense.objects.count(), 1)

    def test_upload_invalid_rows_and_errors(self):
        # One valid, one invalid date, one invalid amount
        data = [
            ['Date', 'Amount', 'Description'],
            ['2025-01-01', 100, 'Valid'],
            ['invalid-date', 200, 'Invalid Date'],
            ['2025-01-02', 'invalid-amount', 'Invalid Amount'],
        ]
        csv_file = self.create_csv_file(data)
        csv_file.name = 'test.csv'
        
        response = self.client.post(reverse('upload'), {
            'currency': '₹',
            'file': csv_file
        })
        
        results = response.context['results']
        self.assertEqual(results['created_count'], 1)
        self.assertEqual(results['error_count'], 2)
        self.assertEqual(results['total_rows'], 3)

    def test_csv_encoding_handling(self):
        # Testing Latin-1 encoding
        data = [
            ['Date', 'Amount', 'Description'],
            ['2025-01-01', 100, 'Café'],
        ]
        csv_file = self.create_csv_file(data, encoding='latin-1')
        csv_file.name = 'test.csv'
        
        response = self.client.post(reverse('upload'), {
            'currency': '₹',
            'file': csv_file
        })
        
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Expense.objects.count(), 1)
        self.assertEqual(Expense.objects.first().description, 'Café')

    def test_robust_header_discovery_deep_in_file(self):
        # Header starts at row 5
        data = [
            [],
            ['Some', 'Random', 'Text'],
            ['Metadata:', 'v1.0'],
            [],
            ['Date', 'Description', 'Amount'],
            ['2025-01-01', 'Found It', 50],
        ]
        excel_file = self.create_excel_file(data)
        excel_file.name = 'test.xlsx'
        
        response = self.client.post(reverse('upload'), {
            'currency': '₹',
            'file': excel_file
        })
        
        self.assertEqual(Expense.objects.count(), 1)
        self.assertEqual(Expense.objects.first().description, 'Found It')

    def test_partial_date_defaults_to_current_year(self):
        current_year = datetime.now().year
        data = [
            ['Date', 'Description', 'Amount'],
            ['15 Dec', 'Partial Date', 50],
        ]
        csv_file = self.create_csv_file(data)
        csv_file.name = 'test.csv'
        
        self.client.post(reverse('upload'), {
            'currency': '₹',
            'file': csv_file
        })
        
        self.assertEqual(Expense.objects.count(), 1)
        self.assertEqual(Expense.objects.first().date.year, current_year)

    def test_csv_upload_headerless(self):
        # Data like the user's screenshot: No headers
        data = [
            ['17/04/2026', '103', 'uber ride'],
            ['11/04/2026', '502', 'lunch at mcdonalds'],
        ]
        csv_file = self.create_csv_file(data)
        csv_file.name = 'expenses_today.csv'
        
        response = self.client.post(reverse('upload'), {
            'currency': '₹',
            'file': csv_file
        })
        
        self.assertEqual(response.status_code, 200)
        results = response.context['results']
        self.assertEqual(results['created_count'], 2)
        self.assertEqual(Expense.objects.count(), 2)
        
        # Verify specific values
        uber = Expense.objects.get(description='uber ride')
        self.assertEqual(uber.amount, 103)
        self.assertEqual(uber.date, date(2026, 4, 17))
