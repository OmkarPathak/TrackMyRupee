# Philosophy

Understand the core design decisions and mental models behind TrackMyRupee.

---

## 1. Why Manual Entry, Not Bank Sync

TrackMyRupee does not read your SMS or connect to your bank account. You log transactions yourself.

This is a deliberate privacy-first design decision, not a missing feature. The trade-off is a few extra seconds of typing in exchange for not giving a third party read access to your bank credentials or message inbox. You stay fully in control of your data.

---

## 2. Why Salary Cycle, Not Calendar Month

Most budgeting tools reset budgets on the 1st of every month. TrackMyRupee lets you define a period that starts on your actual salary landing date (for example, the 28th) and ends the day before the next payment arrives.

This matters because your spending and saving decisions happen relative to when money arrives, not relative to the calendar. A salary-cycle budget reflects how money actually flows in your life.

---

## 3. Income Types: Salary vs. Passive vs. One-off

The app separates income into distinct source types: Salary, Freelance / Consulting, Business, Investment Returns, Rental Income, Cashback and Rewards, Refund / Reimbursement, and Other.

Mixing predictable recurring income with lumpy one-off receipts hides your true monthly run-rate. Keeping them separate gives you an honest picture of how much reliable income you actually have each cycle.

---

## 4. What Smart Insights and Financial Health Actually Are

The Financial Health score and Smart Insights panel perform descriptive pattern-matching over your own historical data. For example, they can tell you that you are spending a higher percentage on a category this cycle compared to last cycle.

They are not financial advice and are not predictions about the future. Use them as data-informed prompts for your own judgment.

---

## 5. Net Worth as the North Star

Every feature in TrackMyRupee, including budgets, loans, transfers, and capital events, ultimately feeds into your net worth number. Keeping that number accurate is the purpose of everything else in the app.

!!! example "Real-world use case"
    Priya is a software engineer paid on the 28th who does not want to link her bank account to any app after a past data-leak concern. She earns Rs. 95,000 per month and wants to know honestly whether she can afford an Rs. 80,000 trip in March. By using manual entry and salary-cycle budgets, TrackMyRupee gives her a clear picture of her savings run-rate so she can answer that question without ever sharing bank credentials.

---

## Related Links
- [Getting Started](./01-getting-started/index.md)
- [Accounts and Net Worth](./02-accounts/index.md)
- [Analytics, Trends and Financial Health](./11-analytics-and-health/index.md)
