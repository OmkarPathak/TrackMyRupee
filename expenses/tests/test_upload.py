from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth.models import User
from expenses.models import Expense, Category
from datetime import date
import io
import openpyxl
import csv

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

    def create_csv_file(self, data):
        file_io = io.BytesIO()
        # Use csv writer to handle commas in data if any
        content = io.StringIO()
        writer = csv.writer(content)
        for row in data:
            writer.writerow(row)
        file_io.write(content.getvalue().encode('utf-8'))
        file_io.seek(0)
        return file_io

    def test_excel_upload_success(self):
        data = [
            ['Date', 'Amount', 'Description', 'Category'],
            ['2025-01-01', 100, 'Lunch', 'Food'],
            ['2025-01-02', 200, 'Bus', 'Travel'],
        ]
        excel_file = self.create_excel_file(data)
        excel_file.name = 'test.xlsx'
        
        response = self.client.post(reverse('upload'), {
            'year': 2025,
            'file': excel_file
        })
        
        self.assertEqual(response.status_code, 302)
        # Current implementation redirects to expense-list
        self.assertIn(reverse('expense-list'), response.url)
        self.assertEqual(Expense.objects.count(), 2)
        self.assertTrue(Category.objects.filter(name='Food').exists())

    def test_csv_upload_success(self):
        data = [
            ['Date', 'Amount', 'Description', 'Category'],
            ['01 Jan 2026', 150, 'Dinner', 'Food'],
            ['2026-02-01', 300, 'Train', 'Travel'],
        ]
        csv_file = self.create_csv_file(data)
        csv_file.name = 'test.csv'
        
        response = self.client.post(reverse('upload'), {
            'year': 2026,
            'file': csv_file
        })
        
        # This will fail until CSV support is added
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Expense.objects.count(), 2)
        self.assertEqual(Expense.objects.filter(date__year=2026).count(), 2)

    def test_upload_missing_columns(self):
        data = [
            ['Date', 'Description'], # Missing Amount and Category
            ['2025-01-01', 'Lunch'],
        ]
        csv_file = self.create_csv_file(data)
        csv_file.name = 'test.csv'
        
        response = self.client.post(reverse('upload'), {
            'year': 2025,
            'file': csv_file
        })
        
        # Currently the view redirects to expense-list with an info message if no data found
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Expense.objects.count(), 0)

    def test_upload_invalid_date_format(self):
        data = [
            ['Date', 'Amount', 'Description', 'Category'],
            ['invalid-date', 100, 'Lunch', 'Food'],
        ]
        csv_file = self.create_csv_file(data)
        csv_file.name = 'test.csv'
        
        response = self.client.post(reverse('upload'), {
            'year': 2025,
            'file': csv_file
        })
        
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Expense.objects.count(), 0)
