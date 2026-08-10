# Loans

Track your active loans, watch repayment progress, and let the app calculate your remaining principal automatically.

!!! note "Plan required"
    The Loans feature is available on the Plus or Pro plan. The Loans link appears in the sidebar only when your account has Plus or Pro tier active.

---

## 1. Opening the Loans Page

Navigate to **Sidebar → Loans** on desktop, or go to **More → Loans** on mobile.

Click **Add** to open the Add New Loan form.

---

## 2. Filling the Loan Form

Complete the following required fields:

1. **Loan Name**: A short label, for example "HDFC Home Loan" or "Personal Loan Oct 2024".
2. **Loan Type**: Select from the dropdown: Home Loan, Vehicle Loan, Education Loan, Personal Loan, Business Loan, Loan Against Property, or Gold Loan.
3. **Currency**: Defaults to your profile currency.
4. **Principal Amount**: The total amount borrowed.
5. **Interest Rate (%)**: The annual interest rate on the loan.
6. **Duration (Months)**: The total repayment tenure in months.
7. **Start Date**: The date the loan was disbursed.

As you fill in the numbers, the form shows a live **Estimated Monthly EMI** preview at the bottom.

Click **Create Loan** to save.

---

## 3. How Repayments Are Logged

Loan repayments are set up as recurring transactions via **Add → Add Subscription**. In the subscription form, set **Transaction Type** to `Loan Repayment` and link it to the relevant loan.

Each time the recurring engine auto-posts a repayment, the remaining principal on the loan detail page decreases by the principal portion of that EMI.

---

## 4. Reading the Paid-Off Percentage and Remaining Balance

On the Loans list page, each loan card shows:

- A paid-off percentage bar
- The remaining principal in rupees

Click any loan card to open its detail page, which shows the full repayment history, amortization schedule, and the breakdown of principal versus interest for each payment.

---

## 5. What Happens When a Loan Is Fully Paid Off

When the remaining principal reaches zero, TrackMyRupee automatically marks the loan as **closed** (inactive). You do not need to do anything manually.

The closed loan stays visible in the Loans list for your records and is marked as closed. It no longer counts as a liability in your net worth.

!!! example "Real-world use case"
    Ravi took a Rs. 1,20,000 personal loan 6 months ago to fund a travel trip. He adds it to TrackMyRupee with Principal Amount Rs. 1,20,000, Interest Rate 14 percent, Duration 12 months, and the disbursement date. The app calculates his estimated EMI at approximately Rs. 10,754 per month. Each time he makes a payment, the recurring engine auto-posts a Loan Repayment entry, and the paid-off percentage bar on the loan card ticks upward, giving him a visible motivation to make an extra prepayment and close it in 10 months instead of 12.

---

## Related Links
- [Accounts and Net Worth](../02-accounts/index.md)
- [Recurring Transactions and Subscriptions](../05-transactions-recurring/index.md)
- [Capital Events](../09-capital-events/index.md)
