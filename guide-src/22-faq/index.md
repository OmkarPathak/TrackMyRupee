# FAQ

Answers to the most common questions about TrackMyRupee.

---

## General

### Is TrackMyRupee free?

TrackMyRupee has a **Free plan** that covers core expense tracking, budgets, and accounts. **Plus** and **Pro** plans unlock advanced features like Loans, Goals, bulk subscriptions, and email summaries. See [trackmyrupee.com/#pricing](https://trackmyrupee.com/#pricing) for current plan details.

### Does the app connect to my bank or read my SMS?

No. TrackMyRupee is privacy-first by design. It does not read SMS, connect to net banking APIs, or access your bank statement. You log transactions manually (or by typing a quick line like `swiggy 320 upi` into the one-line box on the Add Expense page). This is a deliberate choice. See [Philosophy](../00-philosophy.md) for the full reasoning.

### Is my data safe?

Yes. Data is encrypted in transit (HTTPS), stored securely, and never sold to third parties. If you prefer to host your own instance, see [Self-Hosting](../21-self-hosting/index.md). You get full data control with zero data leaving your server.

### Can I use TrackMyRupee in currencies other than rupees?

Yes. Each transaction and account has its own **Currency** field. The app supports INR, USD, EUR, GBP, and more. Set your preferred display currency in **Settings → Profile**.

---

## Accounts and Data

### What happens if I delete an account?

The account record is removed, but transactions linked to that account are not deleted. They just lose their account reference (the account field becomes blank on those records). If you want to stop using an account without losing the association, mark it **inactive** instead. See [Accounts and Net Worth](../02-accounts/index.md) for details.

### How do I fix my opening balance?

Go to **Accounts → [your account] → Edit** (pencil icon). Update the **Initial Balance** field and save. The change takes effect immediately and all future net worth calculations will use the corrected baseline.

### How do I handle a joint account?

Create the account once under one user's TrackMyRupee account and log all transactions manually. There is no shared-account multi-user sync at this time.

---

## Transactions

### Why should I use "Internal Transfer" instead of logging an Expense and Income?

A transfer moves money between accounts you already own. It does not change your net worth. Logging it as Expense and Income double-counts it, inflating both your total income and total expense figures in Analytics. Always use **Add → Internal Transfer** for account-to-account moves. See [Transfers](../06-transfers/index.md).

### Can I import transactions from a spreadsheet?

Yes. The app supports `.xlsx` bulk upload. Your file must have columns: `Date`, `Amount`, `Description`, `Category`. During upload you select a Target Year, which overrides the year in the file. Access this via **Sidebar → Expenses → Upload**.

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

### Why does my savings rate feel lower than expected?

The savings rate denominator excludes Cashback and Rewards and Refund / Reimbursement income, so one-off recoveries do not artificially inflate your rate. This gives you a truer picture of how much you are genuinely saving from your earned income.

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
