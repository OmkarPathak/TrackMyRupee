"""Phase 6 of the import: PDF statements are read in the browser, so the page offers them and the server never sees one."""
from pathlib import Path

from django.conf import settings
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from expenses.models import Account


class ImportPdfTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('pdfuser', 'p@example.com', 'pw12345!')
        self.account = Account.objects.create(user=self.user, name='HDFC Savings', account_type='SAVINGS', currency='INR')
        self.client.force_login(self.user)

    def test_page_offers_pdf_and_loads_reader_only_here(self):
        html = self.client.get(reverse('upload')).content.decode()
        self.assertIn('.pdf', html)
        self.assertIn('js/pdf_import.js', html)
        self.assertIn('vendor/pdfjs/pdf.min.mjs', html)          # fetched by the script only when a PDF is chosen
        for name in ('import-history', 'dashboard'):
            try:
                other = self.client.get(reverse(name)).content.decode()
            except Exception:
                continue
            self.assertNotIn('pdf_import.js', other)

    def test_reader_files_are_vendored_with_licence(self):
        base = Path(settings.BASE_DIR) / 'static' / 'vendor' / 'pdfjs'
        for name in ('pdf.min.mjs', 'pdf.worker.min.mjs', 'LICENSE'):
            self.assertTrue((base / name).exists(), name)

    def test_a_pdf_that_reaches_the_server_gets_a_clear_message(self):
        resp = self.client.post(reverse('upload-preview'), {
            'account': self.account.id, 'currency': 'INR',
            'file': SimpleUploadedFile('s.pdf', b'%PDF-1.4', content_type='application/pdf')})
        self.assertContains(resp, 'could not be read')

    def test_a_bank_pdf_table_keeps_the_posting_date_and_undated_rows(self):
        """What the browser builds from a statement with a Value Date column and no date on repeated-day rows."""
        csv = ('Date,Value Date,Description,Cheque,Deposit,Withdrawal,Balance\n'
               '17 Jun 19,16 Jun 19,ATM WITHDRAWAL SELF-SWITCH AT NFS,,,"1,500.00","112,953.65"\n'
               '17 Jun 19,,PURCHASE ANANDA VILAS HOTEL CHENNAI,,,966.00,"111,987.65"\n'
               '18 Jun 19,18 Jun 19,CREDIT OF INTEREST,,797.00,,"112,784.65"\n')
        resp = self.client.post(reverse('upload-preview'), {
            'account': self.account.id, 'currency': 'INR',
            'file': SimpleUploadedFile('s.csv', csv.encode(), content_type='text/csv')})
        self.assertContains(resp, 'A: Date')
        self.assertEqual(resp.context['counts']['new'], 2)
        self.assertEqual(resp.context['counts']['credit'], 1)
