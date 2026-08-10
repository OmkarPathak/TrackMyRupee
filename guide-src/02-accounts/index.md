# Accounts & Net Worth

Learn how to manage your assets, liabilities, and track your overall net worth in one central place.

1. Viewing the Accounts page — navigate via **Sidebar → Accounts** (desktop) or **Bottom tab → Accounts** (mobile). The page groups accounts into assets and liabilities. A 'Filtered Total Balance' bar at top shows the combined balance.
2. Adding an account — click the **+ Add** shortcut next to Accounts in the sidebar (desktop) or **+ Add** on the Accounts page (mobile). The form is a 2-step wizard. Required fields: **Account Name**, **Account Type**, **Currency**, **Initial Balance**. Step 2 shows type-specific fields (e.g. deposit terms for FD/RD, credit limit for Credit Card).
3. Editing an account's balance/details — click the account row, then use the edit (pencil) icon. Marking an account **inactive** hides it from future transaction pickers but keeps all historical transaction records. **Deleting** an account removes the account record; any past transactions linked to it will lose their account reference (the account field becomes blank on those transactions). Prefer inactive over delete if the account has history.
4. Reading account history — on an account's detail page, transaction rows show relative timestamps ('3 days ago', '7 days ago'). The **History** button (top-right of the detail page) shows the full ledger of all credits and debits for that account.
5. Net worth roll-up — all active account balances sum into the **Net Worth** tile on the Dashboard. Liabilities (credit cards, loans) subtract from assets.

!!! warning "Deleting vs. Deactivating"
    If you delete an account, any expenses, incomes, and transfers previously linked to it will lose their account reference — the account field on those records becomes blank. This does **not** delete the transactions themselves, but it makes them harder to audit. If you just want to stop using an account (e.g. a closed bank account), mark it **inactive** instead — it stays out of your transaction pickers but its history is fully preserved.

<!-- TODO: screenshot (desktop, 1280x800) of /accounts/list/ grouped by Cash & Bank / Investments -->
![Accounts list on desktop](../screenshots/02-accounts/accounts-list-desktop.png)

<!-- TODO: screenshot (mobile, 375x812) of /accounts/list/ -->
![Accounts list on mobile](../screenshots/02-accounts/accounts-list-mobile.png)

<!-- TODO: screenshot (desktop, 1280x800) of the Add New Account form -->
![Add account form on desktop](../screenshots/02-accounts/add-account-form-desktop.png)

<!-- TODO: screenshot (mobile, 375x812) of the Add New Account form -->
![Add account form on mobile](../screenshots/02-accounts/add-account-form-mobile.png)

<!-- TODO: screenshot (desktop, 1280x800) of a single account's transaction history -->
![Account detail on desktop](../screenshots/02-accounts/account-detail-desktop.png)

<!-- TODO: screenshot (mobile, 375x812) of a single account's transaction history -->
![Account detail on mobile](../screenshots/02-accounts/account-detail-mobile.png)

!!! example "Real-world use case"
    Before deciding whether to make a ₹60,000 laptop purchase, Rahul opens **Accounts** to check his real net worth across his HDFC Salary Account, SBI Savings Account, and Cash Wallet in one place — instead of opening three separate banking apps. The **Filtered Total Balance** shows ₹1,92,000 across all three, confirming he can comfortably absorb the purchase without dipping below his ₹1,00,000 emergency buffer.

## Related links
- [Holdings & Mutual Funds Portfolio](holdings.md)
- [Getting Started](../01-getting-started/index.md)
- [Transfers](../06-transfers/index.md)
- [Analytics, Trends & Financial Health](../11-analytics-and-health/index.md)
