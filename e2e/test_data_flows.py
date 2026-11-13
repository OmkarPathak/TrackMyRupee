"""Getting data in and out and keeping it tidy: import a statement, a big purchase, categories, accounts, export,
settings, search."""
import io
import zipfile
from datetime import timedelta
from decimal import Decimal

from django.urls import reverse
from django.utils import timezone

from e2e.base import E2ETestCase, make_account, make_user


class Base(E2ETestCase):
    def setUp(self):
        super().setUp()
        self.user = make_user('tidy')
        self.bank = make_account(self.user, 'HDFC Savings', 'SAVINGS_ACCOUNT', 100000)

    def balance(self, account=None):
        from expenses.models import Account
        return Account.objects.get(pk=(account or self.bank).pk).balance

    def expense(self, description, amount='500', category='Food'):
        from expenses.models import Expense
        return Expense.objects.create(user=self.user, category=category, amount=Decimal(amount), base_amount=Decimal(amount),
                                      currency='₹', date=timezone.localdate(), account=self.bank, description=description)


class ImportTests(Base):
    def csv(self):
        today = timezone.localdate()
        rows = [(today - timedelta(days=n), name, amount) for n, name, amount in ((1, 'Zomato', 450), (2, 'Uber', 230), (3, 'Rent', 12000))]
        text = 'Date,Description,Amount\n' + ''.join(f'{d.isoformat()},{name},{amount}\n' for d, name, amount in rows)
        return {'name': 'statement.csv', 'mimeType': 'text/csv', 'buffer': text.encode()}

    def preview(self, page):
        self.open(page, reverse('upload'))
        page.set_input_files('#formFile', self.csv())
        page.click('button:has-text("Preview")')
        page.wait_for_selector('#import-submit')

    def test_a_csv_statement_is_previewed_imported_and_listed_in_the_history(self):
        from expenses.models import Expense
        page = self.new_page(self.user)
        self.preview(page)
        self.assertIn('Zomato', page.inner_text('#import-preview'))
        page.click('#import-submit')
        self.wait_until(lambda: Expense.objects.filter(user=self.user).count() == 3, 'the rows were not imported')
        self.assertEqual(self.balance(), Decimal('100000') - Decimal('450') - Decimal('230') - Decimal('12000'))
        self.open(page, reverse('import-history'))
        self.assertIn('statement.csv', page.inner_text('body'))

    def test_an_import_can_be_undone_and_the_balance_comes_back(self):
        from expenses.models import Expense
        page = self.new_page(self.user)
        self.preview(page)
        page.click('#import-submit')
        self.wait_until(lambda: Expense.objects.filter(user=self.user).count() == 3, 'the rows were not imported')
        self.open(page, reverse('import-history'))
        page.click('button:has-text("Undo") >> visible=true')
        page.click('.modal.show button:has-text("Undo import")')      # the confirmation
        self.wait_until(lambda: Expense.objects.filter(user=self.user).count() == 0, 'the import was not undone', seconds=20)
        self.assertEqual(self.balance(), Decimal('100000.00'))


class BigPurchaseTests(Base):
    def test_a_big_amount_offers_the_one_time_big_purchase_and_opens_it_filled_in(self):
        from expenses.models import CapitalEvent
        page = self.new_page(self.user)
        self.open(page, reverse('expense-create'))
        page.fill('#tmr-quick-input', 'sofa 150000')
        page.click('#tmr-go')
        link = page.locator('#tmr-capital-link')
        link.wait_for()
        self.assertEqual(page.locator('#tmr-confirm-large').count(), 0)       # (no second "is that right?" box)
        link.click()
        page.wait_for_url('**/capital-events/add/**')
        self.assertIn('150000', page.input_value('input[name="amount"]').replace(',', ''))
        page.click('button[type="submit"]:visible')
        page.wait_for_url(lambda url: '/capital-events/add' not in url)
        event = CapitalEvent.objects.get(user=self.user)
        self.assertEqual((event.amount, event.account_id), (Decimal('150000.00'), self.bank.pk))
        self.assertEqual(self.balance(), Decimal('-50000.00'))


class ManageTests(Base):
    def test_a_category_can_be_added_renamed_and_deleted(self):
        from expenses.models import Category
        page = self.new_page(self.user)
        self.open(page, reverse('category-create'))
        page.fill('input[name="name"]', 'Pets')
        page.click('button[type="submit"]:visible')
        page.wait_for_url(lambda url: '/category/add' not in url)
        category = Category.objects.get(user=self.user, name='Pets')
        self.open(page, reverse('category-edit', kwargs={'pk': category.uuid}))
        page.fill('input[name="name"]', 'Pet care')
        page.click('button[type="submit"]:visible')
        page.wait_for_url(lambda url: '/edit' not in url)
        self.assertTrue(Category.objects.filter(user=self.user, name='Pet care').exists())
        self.open(page, reverse('category-delete', kwargs={'pk': category.uuid}))
        page.click('button:has-text("Delete")')
        page.wait_for_url(lambda url: '/delete' not in url)
        self.assertFalse(Category.objects.filter(user=self.user, name='Pet care').exists())

    def test_an_account_can_be_renamed(self):
        page = self.new_page(self.user)
        self.open(page, reverse('account-edit', kwargs={'pk': self.bank.uuid}))
        page.fill('#id_name', 'HDFC Salary')
        if page.locator('#nextBtn').is_visible():                     # (the account form has two steps)
            page.click('#nextBtn')
        page.click('button[type="submit"]:visible')
        page.wait_for_url(lambda url: '/edit' not in url)
        self.bank.refresh_from_db()
        self.assertEqual(self.bank.name, 'HDFC Salary')

    def test_deleting_a_transfer_puts_the_money_back(self):
        from expenses.models import Transfer
        cash = make_account(self.user, 'Cash', 'CASH_WALLET', 0)
        transfer = Transfer.objects.create(user=self.user, from_account=self.bank, to_account=cash, amount=Decimal('5000'),
                                           converted_amount=Decimal('5000'), date=timezone.localdate())
        self.assertEqual(self.balance(cash), Decimal('5000.00'))
        page = self.new_page(self.user)
        self.open(page, reverse('transfer-delete', kwargs={'pk': transfer.uuid}))
        page.click('button:has-text("Delete")')
        page.wait_for_url(lambda url: '/delete' not in url)
        self.assertEqual(self.balance(cash), Decimal('0.00'))
        self.assertEqual(self.balance(), Decimal('100000.00'))

    def test_the_profile_name_and_salary_day_are_saved(self):
        from expenses.models import UserProfile
        page = self.new_page(self.user)
        self.open(page, reverse('profile-settings'))
        page.fill('input[name="first_name"]', 'Asha')
        self.set_select(page, 'select[name="salary_date"]', '15')
        page.click('button[type="submit"]:visible')
        self.wait_until(lambda: UserProfile.objects.get(user=self.user).salary_date == 15, 'the salary day was not saved')
        self.user.refresh_from_db()
        self.assertEqual(self.user.first_name, 'Asha')


class ExportAndSearchTests(Base):
    def test_exporting_gives_a_file_with_the_entries(self):
        self.expense('Business lunch with a client')
        page = self.new_page(self.user)
        self.open(page, reverse('export-data'))
        with page.expect_download() as download:
            page.click('button:has-text("Export Now")')
        path = download.value.path()
        data = open(path, 'rb').read()
        if zipfile.is_zipfile(io.BytesIO(data)):
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                data = b''.join(archive.read(name) for name in archive.namelist())
        self.assertIn(b'Business lunch with a client', data)

    def test_searching_the_expense_list_narrows_it(self):
        self.expense('Lunch at the office')
        self.expense('Taxi to the airport', category='Travel')
        page = self.new_page(self.user)
        self.open(page, reverse('expense-list'))
        self.assertIn('Taxi to the airport', page.inner_text('body'))
        box = page.locator('.tmr-search-input >> visible=true').first
        box.click()                                                   # (the box only takes focus from a real tap)
        box.fill('Lunch')
        page.wait_for_function("() => !document.body.innerText.includes('Taxi to the airport')")
        self.assertIn('Lunch at the office', page.inner_text('body'))
