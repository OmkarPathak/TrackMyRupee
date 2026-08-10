# Recurring Transactions and Subscriptions

Automate repeating transactions so you never miss a due date or forget to log a regular payment.

---

## 1. Opening the Subscription Form

- **Desktop**: Click **Add** in the top navbar, then select **Add Subscription**.
- **Mobile**: Tap the **+** button in the bottom tab bar, then tap **Add Subscription**.

You can also navigate to **Sidebar → Subscriptions → Add** on desktop.

---

## 2. Filling the Three-Step Wizard

The form is organized into three steps:

### Step 1: Basics

- **Transaction Type**: Choose Expense for bills like streaming services or phone plans, Income for a monthly retainer, Transfer for a recurring investment or SIP, or Loan Repayment for EMI payments.
- **Description**: The name of the subscription (for example, "Airtel Broadband" or "Netflix").
- **Amount**: The recurring amount.
- **Currency**: Defaults to your profile currency.
- **Payment Method**: Cash, Credit Card, Debit Card, UPI, or NetBanking.

### Step 2: Schedule

- **Frequency**: How often the transaction repeats (Monthly, Weekly, Quarterly, Yearly, or a custom interval).
- **Start Date**: The date of the first occurrence.
- **End Date** (optional): The date after which the subscription stops auto-posting.

### Step 3: Account and Details

- **Account**: The account to debit (for expenses) or credit (for income).

Click **Save Subscription** when done.

---

## 3. How Auto-Posting Works

On each scheduled date, the recurring engine automatically creates a new ledger entry. You do not need to log it manually. The entry appears in your Transactions list, Expenses or Income list, and the relevant Budget bar.

---

## 4. The "Renewing Soon" Section

The Subscriptions list at `/recurring/` shows a **Renewing Soon** section for any subscription whose next due date is within the next 30 days, sorted by days remaining. The sidebar also shows a due-soon badge when subscriptions are coming up.

This gives you advance notice before a charge hits your account.

---

## 5. Cancelling a Subscription

Find the subscription in the Subscriptions list. Tap the delete or remove action and confirm the deletion. The subscription will stop auto-posting from the next scheduled date.

!!! info "There is no pause option"
    If you want to temporarily stop a subscription from auto-posting, delete it and recreate it when you want it to resume. There is no built-in pause feature.

!!! example "Real-world use case"
    Sneha sets up her Rs. 1,179 per month Airtel Broadband bill once as a recurring expense: Transaction Type Expense, Description Airtel Broadband, Amount 1179, Frequency Monthly, Payment Method NetBanking, Account HDFC Salary Account. Every month the app automatically posts the expense on the due date. She never has to remember to log it. The Renewing Soon section alerts her a few days ahead so a temporarily low balance does not catch her off guard.

---

## Related Links
- [Adding Expenses](../03-transactions-expenses/index.md)
- [Adding Income](../04-transactions-income/index.md)
- [Budgets](../07-budgets/index.md)
