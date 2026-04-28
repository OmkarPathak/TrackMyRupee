from django.test import TestCase
from django.utils import timezone
from expenses.parser import parse_expense_nl
from datetime import timedelta
from decimal import Decimal

class ExpenseParserTest(TestCase):
    def test_simple_expense(self):
        text = "lunch 450"
        result = parse_expense_nl(text)
        self.assertTrue(result['success'])
        self.assertEqual(result['amount'], "450.00")
        self.assertEqual(result['category'], "Dining Out")
        self.assertTrue("lunch" in result['description'].lower() or "expense" in result['description'].lower())

    def test_currency_symbols(self):
        text = "₹850 uber"
        result = parse_expense_nl(text)
        self.assertTrue(result['success'])
        self.assertEqual(result['amount'], "850.00")
        self.assertEqual(result['category'], "Transport")
        self.assertTrue("uber" in result['description'].lower())

    def test_thousands_suffix(self):
        text = "2k groceries"
        result = parse_expense_nl(text)
        self.assertTrue(result['success'])
        self.assertEqual(result['amount'], "2000.00")
        self.assertEqual(result['category'], "Groceries")
        self.assertTrue("groceries" in result['description'].lower())

    def test_date_keywords(self):
        now = timezone.localdate()
        # Today
        result_today = parse_expense_nl("swiggy 320 today")
        self.assertEqual(result_today['date'], now.isoformat())
        
        # Yesterday
        result_yesterday = parse_expense_nl("swiggy 320 yesterday")
        expected_date = (now - timedelta(days=1)).isoformat()
        self.assertEqual(result_yesterday['date'], expected_date)

    def test_user_categories_matching(self):
        user_cats = ["Personal", "Work", "Education"]
        text = "Work expenses 500"
        result = parse_expense_nl(text, user_categories=user_cats)
        self.assertEqual(result['category'], "Work")
        self.assertTrue("work" in result['description'].lower())

    def test_failure_no_amount(self):
        text = "just some text without numbers"
        result = parse_expense_nl(text)
        self.assertFalse(result['success'])
        self.assertIsNone(result['amount'])
        self.assertEqual(result['category'], "Other")
