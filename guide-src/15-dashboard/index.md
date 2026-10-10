title: Dashboard
description: How the TrackMyRupee Dashboard works out income, spending, savings, categories, trends and net worth for the period you are viewing.
keywords: TrackMyRupee dashboard, savings rate, spending by category, salary cycle, month on month, net worth

# Dashboard

The Dashboard is your month at a glance: what came in, what went out, what you kept, and where the money went. Every number on it is worked out from the transactions you have logged, in your own currency.

---

## 1. Choosing the period

By default the Dashboard shows the **current month**. If you have set a salary day (see [Getting Started](../01-getting-started/index.md)), it shows the current **salary cycle** instead, for example 25 Sep to 24 Oct, and says so in the header.

Use the period picker to switch to last month, the last 3 or 6 months, this year, all time, a single month and year, or a custom date range. The charts adapt: a single month or cycle shows **daily** bars, longer periods show **monthly** bars.

!!! info "Foreign currencies"
    Amounts in another currency are converted to your currency when you log them, and every total on the Dashboard uses that converted amount.

---

## 2. The headline numbers

| Number | What it is |
|---|---|
| **Income** | All income in the period, including cashback and refunds |
| **Spent** | Expenses, plus the **interest** part of loan repayments, plus any [Capital Event](../09-capital-events/index.md) you chose to count in averages |
| **Saved** | Income minus Spent |
| **Savings rate** | Saved divided by income, leaving cashback and refunds out of the income used for the rate |
| **Invested** | Money you moved by transfer into investment accounts. It is shown separately and is **not** taken off Saved |

The loan principal you repay is not spending: your cash goes down and your debt goes down by the same amount, so only the interest reduces your savings. This is the same definition used everywhere else in the app. See [Analytics, Trends and Financial Health](../11-analytics-and-health/index.md) for a worked example.

### Compared with last month

When you look at exactly one month or cycle, each number shows how it changed against the previous month (or previous cycle) as a percentage and an amount. The previous period is read with the **same filters** as the one you are viewing, so filtering to one category compares that category with itself last month.

---

## 3. Where the money went

- **Categories**: spending per category, largest first. The chart shows the top five and groups the rest as **Others**. Loan interest appears as its own line. Capital Events that you did not exclude from budget appear under their subtype name, the same way the [Budgets](../07-budgets/index.md) page counts them.
- **Budget bars**: for each category that has a limit, how much of it you have used. The bar turns amber at 85 percent and red once you pass the limit. For a period longer than a month the limit is scaled up to match (a 3-month view compares against three months of budget). On the current month the Dashboard also shows where you are heading at your current pace.
- **Payment methods**: the split of expenses by how you paid.
- **Top expenses**: your five largest single expenses in the period.
- **Income vs expenses**: income, spending and savings for each day or month, using the same definitions as the headline numbers.

### Filters

You can filter by category, payment method and account. Category and payment method narrow **spending** only: income is not tagged with them, so it stays as it is. An account filter narrows everything (income, expenses, loan repayments, capital events and transfers) to that account.

---

## 4. Net worth

The **Net Worth** tile adds up what you hold and subtracts what you owe, including loans, credit cards and goals. The rules, such as how funds, deposits and loans are valued, are in [Accounts and Net Worth](../02-accounts/index.md#6-how-net-worth-is-calculated).

- **Change this month** is what you saved this month, the same figure as Saved.
- The **allocation** chart splits what you hold by type (cash and bank, fixed income, investments and so on). Credit cards and loans are liabilities, so they never appear as a slice.
- The small trend line walks back from today's net worth by each earlier month's savings, then continues forward at your recent average. It is an estimate, good for direction rather than exact past values.

---

## 5. Year-to-date projection

When you are saving, the Dashboard can tell you where you would end the year if you keep this pace. It takes your savings for the year so far (income minus the same Spent as above), divides by the months passed, and extends that average to the months left. If your savings so far are zero or negative, no projection is shown.

---

## Related Links
- [Analytics, Trends and Financial Health](../11-analytics-and-health/index.md)
- [Budgets](../07-budgets/index.md)
- [Accounts and Net Worth](../02-accounts/index.md)
- [Capital Events](../09-capital-events/index.md)
- [Search and Filters](../14-filters-and-search/index.md)
