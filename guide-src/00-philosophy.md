# Philosophy
Understand the core mental models and privacy-first design decisions behind TrackMyRupee.

## Why manual entry, not bank sync
TrackMyRupee does not read your SMS or connect to your bank. Trade-off: a little more typing, in exchange for not giving a third party read access to your bank or SMS. This is a deliberate privacy-first design decision, not a missing feature.

## Why salary cycle, not calendar month
Most budgeting tools reset on the 1st. TrackMyRupee lets a 'period' run from your actual salary landing date (e.g. the 28th) to the day before it lands again, so your budget period matches how your money actually flows.

## Income vs. Passive vs. One-off
The app tracks income source types separately (Salary, Freelance / Consulting, Business, Investment Returns, Rental Income, Cashback & Rewards, Refund / Reimbursement, Other). Mixing predictable and lumpy income hides your true run-rate.

## What Smart Insights / Financial Health actually are
Descriptive pattern-matching over your own historical data (e.g. 'you're spending X% more on this category than last cycle'). Not financial advice, not guarantees. Calibrate trust appropriately.

## Net worth as the north star
Every feature (budgets, loans, transfers, capital events) ultimately feeds the net worth number. The rest of the guide is in service of keeping that number accurate.

<!-- TODO: screenshot (desktop, 1280x800) of Philosophy page rendered in browser -->
![Philosophy page on desktop](../screenshots/01-getting-started/dashboard-first-look-desktop.png)

<!-- TODO: screenshot (mobile, 375x812) of Philosophy page rendered in browser -->
![Philosophy page on mobile](../screenshots/01-getting-started/dashboard-first-look-mobile.png)

!!! example "Real-world use case"
    Priya is a software engineer paid on the 28th who doesn't want to link her bank account to any app after a past data-leak scare. She earns ₹95,000/month and wants to know honestly whether she can afford a ₹80,000 Goa trip in March. By using manual entry and salary-cycle budgets, TrackMyRupee's honest insights give her a clear picture of her savings run-rate so she can find that answer without ever handing over bank credentials.

## Related Links
- [Getting Started](./01-getting-started/index.md)
- [Accounts & Net Worth](./02-accounts/index.md)
- [Analytics, Trends & Financial Health](./11-analytics-and-health/index.md)
