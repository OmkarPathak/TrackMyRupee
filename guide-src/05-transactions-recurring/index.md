# Recurring Transactions & Subscriptions

Automate repeating transactions so you never miss a due date or income entry.

1. **Opening the subscription form** — **Desktop:** **Add** (navbar) → **Add Subscription**. **Mobile:** **+** FAB → **Add Subscription**. Also via **Sidebar → Subscriptions → (navigate to list then add)**.
2. **Filling the 3-step wizard** — **Step 1 – Basics:** **Transaction Type** (Expense, Income, Transfer, Loan Repayment, or Capital Event — choose Expense for outflows like Netflix, Income for a recurring retainer), **Description / Name** (e.g. 'Airtel Broadband'), **Amount**, **Currency**, **Payment Source Method** (Cash / Credit Card / Debit Card / UPI / NetBanking). **Step 2 – Schedule:** **Frequency** (how often it recurs), **Start Date** (first occurrence), optional **End Date**. **Step 3 – Account & Details:** the account to debit/credit.
3. **How 'Renewing Soon' works** — the Subscriptions list (`/recurring/`) shows a **Renewing Soon** section for any subscription whose next due date is within the next 30 days, sorted by days remaining. The sidebar shows a 'due soon' badge when any subscriptions are due.
4. **What happens on the renewal date** — on the scheduled date, the recurring engine **automatically posts a new ledger entry** (expense, income, or transfer depending on the Transaction Type). You do not need to log it manually. The entry appears in Transactions, Expenses/Income list, and the relevant Budget bar.
5. **Cancelling a subscription** — find it in the Subscriptions list, tap the delete/remove action, and confirm the deletion. There is no 'pause' option — if you want to temporarily stop auto-posting, delete the subscription and recreate it when needed.

!!! example "Real-world use case"
    Sneha sets up her ₹1,179/month Airtel Broadband bill once as a recurring expense: Transaction Type **Expense**, Description **Airtel Broadband**, Amount **1179**, Frequency **Monthly**, Payment Method **NetBanking**, Account **HDFC Salary Account**. Every month the app automatically posts the expense on the due date — she never has to remember to log it. The 'Renewing Soon' section alerts her when it's coming up within 30 days, so a temporarily low account balance doesn't catch her off guard.

<!-- TODO: screenshot (desktop, 1280x800) of Add ▾ → Subscription/recurring form -->
![Add subscription form on desktop](../screenshots/05-transactions-recurring/add-subscription-form-desktop.png)

<!-- TODO: screenshot (mobile, 375x812) of the subscription form -->
![Add subscription form on mobile](../screenshots/05-transactions-recurring/add-subscription-form-mobile.png)

<!-- TODO: screenshot (desktop, 1280x800) of /recurring/ with Renewing Soon filter active -->
![Subscriptions list with Renewing Soon on desktop](../screenshots/05-transactions-recurring/subscriptions-list-desktop.png)

<!-- TODO: screenshot (mobile, 375x812) of /recurring/ list -->
![Subscriptions list on mobile](../screenshots/05-transactions-recurring/subscriptions-list-mobile.png)

<!-- TODO: screenshot (desktop, 1280x800) of the cancel/delete confirmation -->
![Cancel subscription on desktop](../screenshots/05-transactions-recurring/cancel-subscription-desktop.png)

<!-- TODO: screenshot (mobile, 375x812) of cancel/delete confirmation -->
![Cancel subscription on mobile](../screenshots/05-transactions-recurring/cancel-subscription-mobile.png)

## Related links
- [Adding Expenses](../03-transactions-expenses/index.md)
- [Adding Income](../04-transactions-income/index.md)
- [Budgets](../07-budgets/index.md)
