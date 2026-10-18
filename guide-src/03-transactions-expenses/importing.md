# Importing a Statement

Bring in a bank, UPI or credit card statement instead of typing each row. Nothing is saved until you have seen a preview, and every import can be undone.

Go to **Settings → Import**.

---

## 1. What you can upload

| Format | Notes |
|---|---|
| `.csv` | Comma, semicolon or tab separated |
| `.xlsx` / `.xls` | One sheet per file |
| `.pdf` | Read **in your browser**. The PDF and its password never leave your device |

A file can be up to 5 MB and 5,000 rows. Split a bigger one.

Choose the **account** the transactions belong to and the **currency** of the amounts, then pick the file.

### PDF statements

When you choose a PDF, TrackMyRupee finds the table by its heading row (Date, Narration, Withdrawal, Deposit, Balance and the like), turns it into a CSV in the file box, and carries on as for a spreadsheet. If the PDF is password protected you are asked for the password; it stays on your device.

This works for statements with a real text table, which is how most banks produce them. A scanned or photographed statement cannot be read. In that case, download the statement as CSV or Excel from your bank. PDF layouts differ more than spreadsheets do, so check the preview carefully.

---

## 2. Preview

Click **Preview**. Nothing is saved yet. Every row is marked:

| Mark | Meaning |
|---|---|
| **New** | Will be imported as an expense |
| **Duplicate** | You already have it (same date, amount and description) |
| **Possible duplicate** | Same amount as something you typed in, within two days, with a word in common. Left unticked: tick it if it is really new |
| **Transfer** | Names one of your other accounts (a credit card bill, a "self" transfer). Recorded as a transfer, not spending |
| **Money in** | Salary, refunds, interest. Left out unless you switch on the income option |
| **Error** | Could not be read, with the reason |

Each importable row has a tick box so you can leave some out.

**Check how the file was read** lets you correct a column that was picked wrongly, say which amounts are spending, or say whether dates are day first or month first.

---

## 3. How money in and out is told apart

In this order: separate debit and credit columns, a Dr/Cr column or *Dr*/*Cr* after the amount, the running **Balance** column, and last the amounts themselves. With a single signed Amount column and nothing else to go on, words such as *salary* and *refund* in the description are used, and you are told when an assumption was needed.

## 4. Money coming in

Switch on **Also import money coming in as income** and every such row becomes an income entry, with a source guessed from the narration (*salary* is Salary, *interest* is Investment Returns, *refund* is Refund / Reimbursement, *cashback* is Cashback & Rewards, anything else is Other). You can change the source on each row. Money that came from one of your own accounts is a transfer, not income.

## 5. No double entries

Importing the same file twice adds nothing. Identical rows inside one file are kept: two ₹50 teas on one day are two payments. A repeated reference or UTR number is treated as one transaction listed twice.

---

## 6. Long files and dropped connections

Large imports are written in batches and stop starting new work after about 25 seconds. If that happens, or the connection drops, the import shows as **Not finished** in History. Choose the same file again and import it: it carries on where it stopped and writes nothing twice. Only one import runs at a time per person.

## 7. History and undo

**Settings → Import → History** lists every import. **Undo** removes what it created and puts your balance back, as if you had deleted each row yourself. You can import the same file again afterwards. Rows that were not imported, with the reason for each, can be downloaded as a CSV.

---

## Tips

- Pick the right account first: balances and ledger entries follow it.
- Always use **Preview** for a first import from a new bank. An import without a preview never acts on guesses (possible duplicates and transfers are left out and listed in the download).
- On the Free plan the monthly expense limit also applies to imports.
