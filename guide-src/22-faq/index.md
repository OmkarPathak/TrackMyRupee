# FAQ

Answers to the most common questions about TrackMyRupee.

---

## General

### Is TrackMyRupee free?

TrackMyRupee has a **Free plan** that covers core expense tracking, budgets, and accounts. **Plus** and **Pro** plans unlock advanced features like Loans, Goals, bulk subscriptions, and email summaries. See [trackmyrupee.com/#pricing](https://trackmyrupee.com/#pricing) for current plan details.

### Does the app connect to my bank or read my SMS?

No. TrackMyRupee is privacy-first by design. It does not read SMS, connect to net banking APIs, or access your bank statement. You log transactions manually (or by typing a quick line like `swiggy 320 upi` into the one-line box on the Add Expense page). This is a deliberate choice. See [Philosophy](../00-philosophy.md) for the full reasoning.

### Is my data safe?

We never sell your data, read your SMS or ask for bank logins, and data is encrypted in transit (HTTPS). No online service is risk-free, so see the [Privacy Policy](https://trackmyrupee.com/privacy-policy/) and [Security page](https://trackmyrupee.com/security/) for what we collect and how we protect it. If you prefer to host your own instance, see [Self-Hosting](../21-self-hosting/index.md); then your data stays on your own server.

### Can I use TrackMyRupee in currencies other than rupees?

Yes. Each transaction and account has its own **Currency** field. The app supports INR, USD, EUR, GBP, and more. Set your preferred display currency in **Settings → Profile**.

---

## Accounts and Data

### What happens if I delete an account?

The account is hidden and no longer counts in your net worth, but nothing is lost: its transactions stay linked to it and you can bring it back from the **Inactive** tab on the Accounts page (as long as your plan has room for it). Because every transaction needs an account, you cannot post new ones to a deleted account. See [Accounts and Net Worth](../02-accounts/index.md) for details.

### How do I delete my whole TrackMyRupee account?

Go to **Settings** and choose **Delete account** (or **Withdraw consent**, which does the same). You are asked to confirm, signed out at once, and your data is deleted in the background: transactions, accounts, goals, loans, notifications and the history of emails we sent you. If you pay by an auto-renewing subscription, it is cancelled so you are not charged again. You get one confirmation email when it is done. We keep the record of any payment you made (amount, date, plan and the email it was made under) for our accounts and for payment disputes; it is no longer linked to a login. This cannot be undone. To keep a copy first, use **Settings → Export Data** (free on every plan).

### How do I change my email address?

Open **Settings → Profile**, type the new address and save. We send a confirmation link to the **new** address, and your sign-in email changes only when you open it, so a typo or someone else's address can never take over your account. Until then you keep signing in with the old one. An address that already belongs to another account is refused.

### How do I fix my opening balance?

Go to **Accounts → [your account] → Edit** (pencil icon). Update the **Initial Balance** field and save. The change takes effect immediately and all future net worth calculations will use the corrected baseline.

### How do I handle a joint account?

Create the account once under one user's TrackMyRupee account and log all transactions manually. There is no shared-account multi-user sync at this time.

---

## Transactions

### Why should I use "Internal Transfer" instead of logging an Expense and Income?

A transfer moves money between accounts you already own. It does not change your net worth. Logging it as Expense and Income double-counts it, inflating both your total income and total expense figures in Analytics. Always use **Add → Internal Transfer** for account-to-account moves. See [Transfers](../06-transfers/index.md).

### Can I import transactions from a spreadsheet?

Yes. Go to **Settings → Import** and choose an `.xlsx` or `.csv` file, the **currency** of the amounts, and the **account** they were paid from (every expense is charged to an account). Then click **Preview**: nothing is saved yet, and you see every row marked *New*, *Duplicate*, *Money in* or *Error*, with a tick box on each new row so you can leave some out. **Check how the file was read** lets you correct a column that was picked wrongly, say which amounts are spending, or say whether dates are day first or month first. When it looks right, click **Import**. The app finds the header row even below a block of account details, and recognises Date, Description (or Narration, Particulars, Remarks) and Amount columns from their headings, including separate **Withdrawal / Deposit** or **Debit / Credit** columns. It reads files separated by commas, semicolons or tabs, and skips repeated headers and totals such as *Closing Balance*. A Category column is used if there is one; otherwise a category is guessed. A file can be up to 5 MB and 5,000 rows; split a bigger one.

**Spending only.** Money going out is imported as expenses and money coming in (salary, refunds, interest) is left out and counted in the summary, unless you switch on the income option described below. TrackMyRupee works out which is which, in this order: separate debit and credit columns, a Dr/Cr column or *Dr*/*Cr* written after the amount, the running **Balance** column, and last the amounts themselves. If the file has only one signed Amount column and nothing else to go on, it uses words such as *salary*, *interest* and *refund* in the description, or the sign most rows have, and tells you when it had to assume.

**No double entries.** A row is skipped if you already have the same expense (same date, amount and description) on that account, whether you typed it or imported it, so importing the same file twice adds nothing. Identical rows inside one file are kept: two ₹50 teas on the same day are two payments. If your file has a reference or UTR column, a repeated reference is treated as the same transaction listed twice.

**Money coming in.** It is left out by default, since this is an expense import. When the preview finds money coming in, a switch offers **Also import money coming in as income**. Switch it on and every such row becomes an income entry on that account, with a source guessed from the narration (*salary* is Salary, *interest* is Investment Returns, *refund* is Refund / Reimbursement, *cashback* is Cashback & Rewards, anything else is Other) that you can change on each row. Untick a row to leave it out. Income gets the same checks as spending: a row you already have is skipped, one that looks like income you typed in is marked *Possible duplicate*, and money that came from one of your own accounts is a transfer, not income. Undo removes imported income too.

**Looks like something you already have, or a transfer.** A row with the same amount as an expense you typed in, within two days and with a word in common (the bank's *UPI-SWIGGY-98765@ybl* against your *Swiggy dinner*), is marked *Possible duplicate* in the preview and left unticked: tick it if it really is new. A row that says it is a transfer and names one of your other accounts, such as a credit card bill, is marked *Transfer*: it is recorded as a transfer between the two accounts, not as spending, and a transfer you have already recorded is not counted twice. These are guesses, so an import with no preview never acts on them: those rows are left out and listed in the download. Undo removes the transfers an import recorded as well.

**A long file, or a dropped connection.** Writing thousands of rows is done in batches, and an import stops starting new work after about 25 seconds so it never runs into the server's time limit. If that happens, or the connection drops part-way, the import is marked **Not finished** in History and the summary says how many rows are left. Choose the same file again and import it: it carries on from where it stopped, and nothing already imported is written twice. Only one import runs at a time per person, so a double click or a second tab cannot import the same file twice at once.

**Changed your mind?** Every import is listed under **Settings → Import → History**. **Undo** removes the expenses that import created and puts your account balance back, exactly as if you had deleted each one yourself, and you can import the same file again afterwards. Rows that were not imported, with the reason for each, can be downloaded as a CSV from the summary or from that list.

A row with no category gets a match from your own categories or past entries, otherwise **Other**; a column called *Transaction Type* is never used as the category. A date written without a year (such as *15 Dec*) means the most recent such date, never one in the future. Dates such as 03/04/2026 are read day first, unless another date in the same file (such as 04/25/2026) shows the file is month first. Amounts can include ₹, Rs. or INR. Only one import can run at a time. On the Free plan the monthly expense limit also applies to imports: rows dated in the current month beyond the limit are skipped and the upload summary tells you how many.

### What is a Capital Event and when should I use it?

A Capital Event is a large, one-off payment such as a car down-payment, home renovation advance, or lump-sum medical bill that you do not want distorting your monthly average charts. It still appears in your cash flow and net worth but is excluded from budget calculations. As a rule of thumb: if the amount is large enough to make your average monthly expense chart look abnormal for months, log it as a Capital Event. See [Capital Events](../09-capital-events/index.md).

### Why does the new expense form show a "Check" tag on some fields?

After you type a line such as `uber to airport 450 upi`, TrackMyRupee fills in what it understood and marks those fields **Auto**. If it was not sure about something, usually the category, the field is marked **Check** and left for you to choose. Pick it once, and the app remembers your choice for that kind of expense next time. See [Adding Expenses](../03-transactions-expenses/index.md).

### I added an expense by mistake. Can I undo it?

Yes, for ten minutes. Right after you add an expense, a message appears with an **Undo** button. After those ten minutes, delete the expense from the Expenses list instead.

### Why did the app ask "Is that right?" before saving my expense?

Amounts of ₹1,00,000 or more get a second look, which catches an accidental extra zero. You can confirm it, edit the amount, or choose **Log as Capital Event instead**, which is the right home for one-off big payments. See [Capital Events](../09-capital-events/index.md).

### What does "auto-posting" mean for recurring transactions?

When a recurring transaction's scheduled date arrives, the TrackMyRupee engine automatically creates a new ledger entry. You do not need to manually log it. It appears in your Transactions list, Expenses or Income list, and any relevant Budget bar. See [Recurring and Subscriptions](../05-transactions-recurring/index.md).

---

## Budgets and Analytics

### My budget bar shows 0% even though I have logged expenses. Why?

The budget bar only counts expenses in categories that have a limit set. If a category has no rupee limit, it appears in the list but without a bar. Click the pencil icon next to the category to set a limit.

### What is the Financial Health Score?

A score from 0 to 100 calculated from your year-to-date savings rate:

| Score | Label | Savings rate |
|---|---|---|
| 10 | Needs attention | Negative (spending exceeds income) |
| 40 | Needs improvement | 0 to 20 percent |
| 70 | Stable | 20 to 30 percent |
| 95 | Wealth Builder | 30 percent or more |

Expand the **Health Breakdown** section on the Analytics page to see four component metrics: Savings Rate, Expense Growth, Consistency (positive-savings months out of last 10), and Risk Buffer (months of runway). See [Analytics and Health](../11-analytics-and-health/index.md).

### How is my savings rate calculated?

Savings rate = (Income − Expenses − Loan interest − Capital Events that count towards averages) ÷ (Income − Cashback and Refund income) × 100. The same formula is used on the Dashboard, Analytics, Month on Month, the Transactions page, and the monthly email. See [How the Savings Rate Is Calculated](../11-analytics-and-health/index.md#3-how-the-savings-rate-is-calculated) for the rules and a worked example.

### Why is my savings rate different from what I expected?

The usual reasons are: loan **principal** is not counted as spending (only the interest is), Capital Events are left out unless you turned off their **Exclude from Averages & Trends** setting, and Cashback and Refund income is not part of the base your rate is measured against. Also check that the pages you compare are looking at the same period and filters.

---

## Loans

### Why did my loan disappear or turn grey?

When the remaining principal on a loan reaches zero, TrackMyRupee automatically marks the loan as inactive (closed). It does not disappear. It is still visible in the Loans list, just marked as closed. A closed loan no longer counts as a liability in your net worth. See [Loans](../08-loans/index.md).

### How do I log an extra prepayment on a loan?

Log a **Capital Event** with the relevant Subtype and link it to the loan via the **Linked Loan** field. The Loan Detail page will then show the prepayment as part of total capital committed. See [Capital Events](../09-capital-events/index.md).

---

## TMR Flows

### What is TMR Flows, and do I have to use it?

TMR Flows is a set of short, guided forms for life events, such as "I took a loan", "I started a SIP", or "I bought a car". One form creates everything connected to the event, properly linked together. You never have to use it. Everything a Flow creates can also be added by hand from its usual page. Flows are simply faster and harder to get wrong. See [TMR Flows](../13-tmr-flows/index.md).

### I ran a Flow and made a mistake. How do I fix it?

Before you confirm, use the **Edit** link next to any section on the Review screen. After you have confirmed, there is no single "undo" button, because a Flow creates ordinary items. Edit or delete those items from their normal pages: [Loans](../08-loans/index.md) for a loan, [Accounts](../02-accounts/index.md) for cards, deposits and assets, [Subscriptions](../05-transactions-recurring/index.md) for salary, rent and other recurring entries, and [Savings Goals](../12-goals/index.md) for goals. Please do not run the Flow a second time to fix a typo, because that creates a duplicate.

### Why does the Review screen warn me about my plan?

Some Flows create things that are limited by your plan, such as loans, accounts, recurring entries, or savings goals. If the Flow would take you over the limit, the warning appears on the Review screen, before anything is saved, with a suggestion to upgrade or remove something you no longer use.

### What does "Create historical entries" do?

It matters only when the start date is in the past. Off means the schedule starts fresh from the next due date. On means every missed occurrence is posted right away. See [the backfill switch](../13-tmr-flows/index.md#4-create-historical-entries-the-backfill-switch).

---

## Searching and Filtering

### How do I find all my UPI food expenses from last quarter?

On the Expenses page, set the time period to **Last 3 months**, then use **+ Filter** to add a **Category** filter and a **Payment Method** filter. The total under the page title updates to match. See [Searching, Filtering and Sorting](../14-filters-and-search/index.md).

### Is there a keyboard shortcut to jump to a page?

Yes. Press **Cmd + K** on a Mac, or **Ctrl + K** on Windows or Linux, to open the page finder, type a few letters, and press Enter.

---

## Mobile and Self-Hosting

### Is there an Android app?

Not currently. The iOS app (built with Capacitor) is available for personal sideloading. The web app at `trackmyrupee.com` is fully responsive and works well as a Progressive Web App (PWA) on Android. Add it to your home screen from Chrome for an app-like experience.

### Can I host TrackMyRupee on my own server?

Yes. Full self-hosting is supported via Docker Compose. See [Self-Hosting](../21-self-hosting/index.md) for the step-by-step guide.

---

## Related Links
- [Philosophy](../00-philosophy.md)
- [Getting Started](../01-getting-started/index.md)
- [Self-Hosting](../21-self-hosting/index.md)
