"""Turning an uploaded file into plain rows: a list of sheets, each a list of rows of cell values.

Nothing here knows what a statement is. Layout detection and row meaning live in `layout` and `directions`.
"""
import csv
import io
from itertools import islice

import openpyxl
from django.utils.translation import gettext as _

MAX_IMPORT_BYTES = 5 * 1024 * 1024
MAX_IMPORT_ROWS = 5000
_ROW_SLACK = 25                         # rows read past the limit, so a file over it is noticed
_DELIMITERS = [',', ';', '\t', '|']


class ImportFileError(ValueError):
    """A problem with the file itself, worded for the person who uploaded it."""


def decode_bytes(content):
    """Text from a file's bytes. UTF-8 (with or without a BOM) first, then Windows-1252, which is what Excel
    writes on Windows; Latin-1 reads anything, so it is the last resort."""
    for encoding in ('utf-8-sig', 'cp1252', 'latin-1'):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    return None


def sniff_delimiter(text):
    """The separator a CSV uses. Excel in some regions writes semicolons, and some banks use tabs or pipes."""
    lines = [line for line in text.splitlines()[:30] if line.strip()]
    best, best_score = ',', 0
    for delimiter in _DELIMITERS:
        counts = [line.count(delimiter) for line in lines]
        counts = [c for c in counts if c]
        if not counts:
            continue
        # How many lines have it, weighted by how steady the count is
        modal = max(set(counts), key=counts.count)
        score = counts.count(modal) * (1 + min(modal, 5))
        if score > best_score:
            best, best_score = delimiter, score
    return best


def _csv_rows(text, max_rows):
    reader = csv.reader(io.StringIO(text), delimiter=sniff_delimiter(text))
    return list(islice(reader, max_rows + _ROW_SLACK))


def _html_table_rows(text):
    from bs4 import BeautifulSoup
    sheets = []
    for table in BeautifulSoup(text, 'html.parser').find_all('table'):
        rows = []
        for tr in table.find_all('tr'):
            cells = [td.get_text(strip=True) for td in tr.find_all(['td', 'th'])]
            if any(cells):
                rows.append(cells)
        if rows:
            sheets.append(rows)
    return sheets


def read_sheets(uploaded_file, file_name, max_rows=MAX_IMPORT_ROWS):
    """All the sheets of a spreadsheet or CSV as lists of rows. Raises ImportFileError for an unreadable .xls."""
    name = (file_name or '').lower()
    uploaded_file.seek(0)
    if name.endswith('.csv'):
        text = decode_bytes(uploaded_file.read())
        rows = _csv_rows(text, max_rows) if text else []
        return [rows] if rows else []

    try:
        workbook = openpyxl.load_workbook(uploaded_file, data_only=True)
        sheets = []
        for sheet in workbook.worksheets:
            rows = list(islice(sheet.iter_rows(values_only=True), max_rows + _ROW_SLACK))
            if rows:
                sheets.append(rows)
        return sheets
    except Exception:
        # Some banks save an HTML table or a CSV as ".xls"
        uploaded_file.seek(0)
        text = decode_bytes(uploaded_file.read())
        sheets = []
        if text:
            if '<table' in text.lower() or '<html' in text.lower():
                try:
                    sheets = _html_table_rows(text)
                except Exception as error:
                    raise ImportFileError(_("Failed to parse XLS as HTML table: ") + str(error))
            else:
                try:
                    rows = _csv_rows(text, max_rows)
                except Exception as error:
                    raise ImportFileError(_("Failed to parse XLS as CSV: ") + str(error))
                sheets = [rows] if rows else []
        if not sheets:
            raise ImportFileError(_(
                "This file appears to be in the older Excel (.xls) format, which is not supported directly. "
                "Please open it in Microsoft Excel, Google Sheets, or LibreOffice and save it as a newer .xlsx format, "
                "then try uploading again."
            ))
        return sheets
