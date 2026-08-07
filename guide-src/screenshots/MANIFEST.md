# Screenshot manifest

> **Note on 00-philosophy:** The Philosophy page (guide root `00-philosophy.md`) has no
> dedicated screenshot folder. Its screenshot placeholders point into `01-getting-started/`
> using the `dashboard-first-look-{desktop,mobile}.png` shots already listed there.

I couldn't export the screenshots I captured while auditing the demo — the
browser tool only renders them to me for inline review, it doesn't hand back
a saveable image file. Use this manifest to capture the real ones (via the
same demo at https://trackmyrupee.com/demo, or your own app) at:
- Desktop viewport: 1280x800
- Mobile viewport: 375x812

Every tutorial page needs BOTH a desktop and mobile shot of each named UI
state. File naming: `<page-slug>-<state>-desktop.png` /
`-mobile.png`, saved into the matching numbered folder.

## 01-getting-started
- `dashboard-first-look-{desktop,mobile}.png` — Dashboard on first login/demo entry, full month view
- `salary-cycle-editor-{desktop,mobile}.png` — the pencil-edit on "Salary cycle: 01 Aug – 31 Aug"

## 02-accounts
- `accounts-list-{desktop,mobile}.png` — /accounts/list/, grouped by Cash & Bank / Investments
- `add-account-form-{desktop,mobile}.png` — the "+ Add account" modal/form
- `account-detail-{desktop,mobile}.png` — a single account's transaction history

## 03-transactions-expenses
- `add-expense-form-{desktop,mobile}.png` — Add ▾ → Expense form (date, category, account, payment method fields)
- `expenses-list-{desktop,mobile}.png` — /expenses/ with a few rows populated
- `expense-category-picker-{desktop,mobile}.png` — category dropdown open, showing Dining Out/Groceries/Rent/etc.

## 04-transactions-income
- `add-income-form-{desktop,mobile}.png` — Add ▾ → Income form, Source Type selector (Salary / Freelance & Consulting / Business / Investment Returns / Rental Income / Cashback & Rewards / Refund & Reimbursement / Other)
- `income-list-{desktop,mobile}.png` — /income/list/ with source-type summary tiles

## 05-transactions-recurring
- `add-subscription-form-{desktop,mobile}.png` — Add ▾ → Subscription/recurring form
- `subscriptions-list-{desktop,mobile}.png` — /recurring/ with Renewing Soon filter active
- `cancel-subscription-{desktop,mobile}.png` — the cancel/delete confirmation

## 06-transfers
- `add-transfer-form-{desktop,mobile}.png` — Add ▾ → Transfer form, source/destination account pickers
- `transfer-in-transaction-list-{desktop,mobile}.png` — a transfer row as it appears in /transactions/

## 07-budgets
- `budget-overview-{desktop,mobile}.png` — /budget/ total budget goal + status pills
- `budget-needs-attention-{desktop,mobile}.png` — the "Needs attention" over-budget category cards
- `edit-budget-limit-{desktop,mobile}.png` — the pencil-edit on a category limit

## 08-loans
- `loans-overview-{desktop,mobile}.png` — /loans/, active loans with paid-off %
- `add-loan-form-{desktop,mobile}.png` — "+ New Loan" form
- `loan-detail-{desktop,mobile}.png` — a loan's repayment/amortization detail view

## 09-capital-events
- `capital-events-empty-{desktop,mobile}.png` — empty state ("No capital events yet")
- `add-capital-event-form-{desktop,mobile}.png` — the add-capital-event form

## 10-calendar
- `calendar-month-view-{desktop,mobile}.png` — /calendar/ full month grid with legend
- `calendar-day-detail-{desktop,mobile}.png` — a day cell expanded/clicked showing its transactions

## 11-analytics-and-health
- `analytics-health-score-{desktop,mobile}.png` — Financial Health score card
- `trends-category-creep-{desktop,mobile}.png` — Category Creep Detection cards
- `net-worth-trend-{desktop,mobile}.png` — the net worth trend chart
