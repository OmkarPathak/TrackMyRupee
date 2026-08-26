title: Accounts and Net Worth
description: Understand account types, net worth calculation, and how recurring deposits and valuations are handled in TrackMyRupee.
keywords: TrackMyRupee accounts, net worth calculation, recurring deposit, account types, holdings

# Accounts and Net Worth

Manage all your assets and liabilities in one place and track your overall net worth accurately.

---

## 1. Opening the Accounts Page

Navigate to **Sidebar → Accounts** on desktop, or tap the **Accounts** tab in the bottom navigation on mobile. The page groups your accounts into asset categories such as Cash and Bank, Fixed-Income, and Investments.

The **Filtered Total Balance** card at the top shows the combined cash and ledger balance of all accounts matching your current filter.

!!! info "Filtered Total Balance vs. Net Worth"
    The Filtered Total Balance shows the sum of cash and accrued deposit balances for the accounts listed. It does not include the live market value of mutual fund holdings, real estate appraisals, or the deduction of outstanding loans. The Dashboard **Net Worth** card gives you the complete picture.

---

## 2. Adding an Account

Click **+ Add** next to Accounts in the sidebar on desktop, or tap **+ Add** on the Accounts page on mobile.

The form is a two-step wizard:

1. **Account Basics**: Enter Account Name, Account Type, Currency, and Initial Balance.
2. **Type-specific fields**: Fill in details that apply to your account type. For example, Fixed Deposits show deposit terms and maturity date fields. Credit Cards show a credit limit field.

Click **Save** when done.

---

## 3. Account Types Reference

| Type | Use for |
|---|---|
| Cash Wallet | Physical cash you carry |
| Savings Account | Regular bank savings account |
| Salary Account | Bank account where your salary lands |
| Fixed Deposit | FD or RD with interest accrual |
| Mutual Funds | Mutual fund or SIP investment account |
| Demat Account | Stock trading and brokerage account |
| NPS / PF | Provident fund or pension account |
| Credit Card | Credit card (tracked as a liability) |
| Loan Account | Linked to a loan for repayment tracking |
| Physical Asset | Real estate, vehicle, or other asset |
| Insurance | Life or endowment policy |

!!! info "Note on Recurring Deposits (RD)"
    RD valuation is not treated like a single lump-sum FD from day one. Each installment accrues from its own deposit date, and only installments that were actually posted are counted. If a month is skipped, that installment is not assumed automatically.

!!! info "Note on Credit Card, BNPL, and Overdraft Billing Dates"
    For revolving credit accounts (Credit Card, BNPL, Overdraft), you can set an optional **Billing Day of Month** (1–31). TrackMyRupee computes the next upcoming billing date, displays a badge on your account list, and sends a **3-day advance reminder**.
    - **In-App Notification Panel**: Delivered to all active users.
    - **WebPush Notifications**: Delivered to all active users who have enabled browser push notifications.
    - **Email Digest**: Included in daily financial digest emails for Plus and Pro tier subscribers.

---

## 4. Editing Account Details and Balance

Click any account row to open its detail page. Use the pencil (edit) icon to update the Account Name, Initial Balance, or any type-specific fields.

If an account no longer needs new transactions but has transaction history you want to keep, mark it **Inactive** instead of deleting it. Inactive accounts are hidden from transaction pickers but their full history is preserved.

!!! warning "Deleting vs. deactivating"
    Deleting an account removes the account record. Any past expenses, incomes, and transfers linked to it will lose their account reference - those transaction records stay in the system but the account field becomes blank on them. If the account has any history, deactivate it instead.

---

## 5. Reading Account Transaction History

Click any account row to open its detail page. The page shows a ledger of all credits and debits, with relative timestamps such as "3 days ago" and "7 days ago".

Click the **History** button at the top right of the detail page to view the full unfiltered ledger for that account.

---

## 6. How Net Worth Is Calculated

The **Net Worth** tile on the Dashboard sums all your active account valuations and subtracts liabilities:

- **Cash and Bank accounts**: ledger balance
- **Fixed Deposits**: principal plus accrued interest
- **Mutual Funds and Demat**: live market value using NAV (units x current NAV)
- **Physical Assets**: latest appraised valuation or acquisition cost
- **Insurance**: latest surrender value
- **Credit Cards and Loans**: subtracted as liabilities

!!! example "Real-world use case"
    Before deciding whether to make a Rs. 60,000 laptop purchase, Rahul opens Accounts to check his real net worth across his HDFC Salary Account, SBI Savings Account, and Cash Wallet in one place rather than opening three separate banking apps. The Filtered Total Balance shows Rs. 1,92,000 across all three, confirming he can absorb the purchase without going below his Rs. 1,00,000 emergency reserve.

---

## Related Links
- [Holdings and Mutual Funds Portfolio](holdings.md)
- [Accrued vs Invested Balance View](accrued-vs-invested.md)
- [Getting Started](../01-getting-started/index.md)
- [Transfers](../06-transfers/index.md)
- [Analytics, Trends and Financial Health](../11-analytics-and-health/index.md)
