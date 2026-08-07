# Loans

Manage and track your active loans and repayment progress. Note: Loans feature requires a Plus or Pro plan.

1. **Opening the Loans page** — **Sidebar → Loans** (desktop, visible on Plus/Pro plans) or **More → Loans** (mobile). Click **+ Add** to open the 'Add New Loan' form.
2. **Filling the loan form** — required fields: **Loan Name**, **Loan Type** (dropdown: Home Loan, Vehicle Loan, Education Loan, Personal Loan, Business Loan, Loan Against Property, Gold Loan), **Currency**, **Principal Amount**, **Interest Rate (%)**, **Duration (Months)**, **Start Date**. The form shows a live **Estimated Monthly EMI** preview as you fill in the numbers. Click **Create Loan** to save.
3. **How repayments are logged** — loan repayments are entered as recurring transactions (Transaction Type: Loan Repayment) via **Add → Add Subscription**. Each repayment reduces the remaining principal shown on the loan detail page.
4. **Reading the paid-off % and remaining balance** — on the Loans list page, each loan card shows the paid-off percentage bar and remaining principal. Click the loan card to open the loan detail page, which shows the full repayment and amortization history.
5. **What happens when a loan is fully paid off** — when the remaining principal reaches ₹0, TrackMyRupee automatically marks the loan as **closed** (inactive). The loan stays visible in the Loans list for your records but is marked as closed — it no longer counts as a liability in your net worth. You do not need to manually close it.

!!! example "Real-world use case"
    Ravi took a ₹1.2L personal loan 6 months ago to fund a travel trip. He adds it to TrackMyRupee with Principal Amount **₹1,20,000**, Interest Rate **14%**, Duration **12 months**, Start Date the day the loan was disbursed. The app calculates his estimated EMI at ~₹10,754/month. Each time he makes a payment, the recurring engine auto-posts a Loan Repayment entry, and the paid-off % bar on the loan card ticks up — giving him a visual motivation to make an extra prepayment and close it in 10 months instead of 12.

<!-- TODO: screenshot (desktop, 1280x800) of /loans/, active loans with paid-off % -->
![Loans overview on desktop](../screenshots/08-loans/loans-overview-desktop.png)

<!-- TODO: screenshot (mobile, 375x812) of /loans/ -->
![Loans overview on mobile](../screenshots/08-loans/loans-overview-mobile.png)

<!-- TODO: screenshot (desktop, 1280x800) of the 'Add New Loan' form -->
![Add loan form on desktop](../screenshots/08-loans/add-loan-form-desktop.png)

<!-- TODO: screenshot (mobile, 375x812) of the Add New Loan form -->
![Add loan form on mobile](../screenshots/08-loans/add-loan-form-mobile.png)

<!-- TODO: screenshot (desktop, 1280x800) of a loan's repayment/amortization detail view -->
![Loan detail on desktop](../screenshots/08-loans/loan-detail-desktop.png)

<!-- TODO: screenshot (mobile, 375x812) of loan detail -->
![Loan detail on mobile](../screenshots/08-loans/loan-detail-mobile.png)

Related links:
- [Accounts & Net Worth](../02-accounts/index.md)
- [Recurring Transactions & Subscriptions](../05-transactions-recurring/index.md)
- [Capital Events](../09-capital-events/index.md)
