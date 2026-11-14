"""How far an uploaded spreadsheet may unpack. Loading costs about 9.5 MB of memory per MB of unpacked XML, and the
server has 512 MB, so the cap has to keep one hostile upload well under that while leaving room for a real statement."""
import io
import zipfile
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from openpyxl import Workbook

from expenses.models import Account
from expenses.views import misc

MEGABYTE = 1024 * 1024


def zip_of(size):
    """A small file that unpacks to `size` bytes: a few kilobytes of compressed zeros."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('xl/worksheets/sheet1.xml', b'0' * size)
    buffer.seek(0)
    return buffer


def statement(rows, columns=10):
    """A statement-shaped workbook: text narrations, numbers and dates across `columns` columns."""
    book = Workbook(write_only=True)
    sheet = book.create_sheet()
    for n in range(rows):
        sheet.append([f'UPI-payment-{n}-some-longer-narration-text-here', n, n * 1.5, '2026-09-01', 'Debit'] +
                     ['x' * 20] * (columns - 5))
    buffer = io.BytesIO()
    book.save(buffer)
    buffer.seek(0)
    return buffer


class CapTests(SimpleTestCase):
    def test_the_cap_keeps_one_upload_well_inside_the_servers_memory(self):
        measured_mb_per_mb = 9.5
        worst_case_mb = misc.MAX_XLSX_UNCOMPRESSED_BYTES / MEGABYTE * measured_mb_per_mb
        self.assertLess(worst_case_mb, 128)

    def test_a_file_that_unpacks_past_the_cap_is_refused(self):
        bomb = zip_of(misc.MAX_XLSX_UNCOMPRESSED_BYTES + MEGABYTE)
        self.assertLess(len(bomb.getvalue()), 64 * 1024)               # a few kilobytes in, many megabytes out
        self.assertTrue(misc._xlsx_expands_too_much(bomb))

    def test_the_file_is_rewound_after_the_check(self):
        buffer = zip_of(1000)
        misc._xlsx_expands_too_much(buffer)
        self.assertEqual(buffer.tell(), 0)

    def test_a_file_that_is_not_a_zip_is_left_to_the_other_readers(self):
        self.assertFalse(misc._xlsx_expands_too_much(io.BytesIO(b'<html><table></table></html>')))

    def test_the_largest_statement_the_importer_accepts_fits_with_room_to_spare(self):
        from expenses.importing.parsing import MAX_IMPORT_ROWS
        book = statement(MAX_IMPORT_ROWS)
        unpacked = sum(info.file_size for info in zipfile.ZipFile(book).infolist())
        book.seek(0)
        self.assertLess(unpacked * 3, misc.MAX_XLSX_UNCOMPRESSED_BYTES)       # at least three times the room it needs
        self.assertFalse(misc._xlsx_expands_too_much(book))


class UploadScreenTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user('cap-user', password='pw')
        profile = self.user.profile
        profile.tier, profile.has_seen_tutorial, profile.currency = 'PRO', True, '₹'
        profile.consent_granted = True
        profile.save()
        self.client.force_login(self.user)
        self.account = Account.objects.create(user=self.user, name='Bank', account_type='SAVINGS_ACCOUNT',
                                              balance=Decimal('100'), currency='₹')

    def preview(self, content):
        upload = SimpleUploadedFile('statement.xlsx', content,
                                    content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        return self.client.post(reverse('upload-preview'), {'account': self.account.id, 'currency': '₹', 'file': upload})

    def test_an_oversized_archive_gets_a_message_not_a_server_error(self):
        response = self.preview(zip_of(misc.MAX_XLSX_UNCOMPRESSED_BYTES + MEGABYTE).getvalue())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'too large to import')

    def test_the_demo_account_goes_through_the_same_check(self):
        demo = User.objects.create_user('demo', password='pw')
        demo.profile.tier, demo.profile.has_seen_tutorial, demo.profile.consent_granted = 'PRO', True, True
        demo.profile.save()
        Account.objects.create(user=demo, name='Demo bank', account_type='SAVINGS_ACCOUNT', balance=Decimal('1'), currency='₹')
        self.client.force_login(demo)
        response = self.preview(zip_of(misc.MAX_XLSX_UNCOMPRESSED_BYTES + MEGABYTE).getvalue())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'too large to import')
