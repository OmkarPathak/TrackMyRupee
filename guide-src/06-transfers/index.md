# Transfers

Move money safely between your own accounts without double-counting it in your net worth.

1. **Opening the transfer form** — **Desktop:** **Add** (navbar) → **Internal Transfer**. **Mobile:** **+** FAB → **Internal Transfer**. The form is titled 'Internal Transfer'.
2. **Filling the form** — **Amount**, **Date**, **From Account** (the account money leaves), **To Account** (the account money arrives in), **Description** (optional). Click **Execute Transfer** to save.
3. **Why a transfer is neither income nor expense** — a transfer moves money between two accounts you already own. It does not change your net worth, and it is excluded from your income and expense totals in Analytics and the Dashboard. It does appear in the **Transactions** list (`/transactions/`) with a special transfer indicator.
4. **Immediate balance effect** — the transfer debits **From Account** and credits **To Account** instantly. Both accounts' balances update the moment you click **Execute Transfer**.
5. **Common transfer patterns** — moving idle savings from a Savings Account into a Mutual Fund account (to put it to work), moving cash-in-hand receipts into a bank deposit, or sweeping a month-end surplus from a salary account into an investment account.

!!! warning "Don't log a transfer as Expense + Income"
    A common mistake is logging the outflow as an 'Expense' from one account and the inflow as 'Income' to another. This **double-counts** the money in your Analytics — inflating both your expense total and your income total by the transferred amount. Always use **Add → Internal Transfer** instead.

!!! example "Real-world use case"
    After seeing on the Trends page that ₹80,000 has been sitting idle in his SBI Savings Account for three cycles, Arjun decides to sweep ₹25,000 into his Mutual Funds account each month. He sets up the transfer via **Add → Internal Transfer**, **From Account: SBI Savings**, **To Account: Mutual Funds**, **Amount: 25,000**, and hits **Execute Transfer**. His Accounts page immediately shows SBI balance down by ₹25,000 and Mutual Funds up by ₹25,000 — net worth unchanged, but the asset allocation is now healthier.

<!-- TODO: screenshot (desktop, 1280x800) of Add ▾ → Internal Transfer form, source/destination account pickers -->
![Add transfer form on desktop](../screenshots/06-transfers/add-transfer-form-desktop.png)

<!-- TODO: screenshot (mobile, 375x812) of the Internal Transfer form -->
![Add transfer form on mobile](../screenshots/06-transfers/add-transfer-form-mobile.png)

<!-- TODO: screenshot (desktop, 1280x800) of a transfer row as it appears in /transactions/ -->
![Transfer in transaction list on desktop](../screenshots/06-transfers/transfer-in-transaction-list-desktop.png)

<!-- TODO: screenshot (mobile, 375x812) of a transfer row in /transactions/ -->
![Transfer in transaction list on mobile](../screenshots/06-transfers/transfer-in-transaction-list-mobile.png)

## Related links
- [Accounts & Net Worth](../02-accounts/index.md)
- [Adding Expenses](../03-transactions-expenses/index.md)
- [Analytics, Trends & Financial Health](../11-analytics-and-health/index.md)
