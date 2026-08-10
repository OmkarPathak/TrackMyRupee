# Transfers

Move money safely between your own accounts without double-counting it in your net worth.

1. **Opening the transfer form** — **Desktop:** **Add** (navbar) → **Internal Transfer**. **Mobile:** **+** FAB → **Internal Transfer**. The form is titled 'Internal Transfer'.
2. **Filling the form** — **Amount**, **Date**, **From Account** (the account money leaves), **To Account** (the account money arrives in), **Description** (optional). Click **Execute Transfer** to save.
3. **Why a transfer is neither income nor expense** — a transfer moves money between two accounts you already own. It does not change your net worth, and it is excluded from your income and expense totals in Analytics and the Dashboard. It does appear in the **Transactions** list (`/transactions/`) with a special transfer indicator.
4. **Immediate balance effect** — the transfer debits **From Account** and credits **To Account** instantly. Both accounts' balances update the moment you click **Execute Transfer**.
5. **Common transfer patterns** — moving idle savings from a Savings Account into a Mutual Fund account (to put it to work), moving cash-in-hand receipts into a bank deposit, or sweeping a month-end surplus from a salary account into an investment account.

10: ## Setting Up Recurring Transfers for Mutual Fund SIPs
11: 
12: For automated monthly investments (SIPs), set up a recurring **Transfer** rather than logging separate expense records:
13: 1. Go to **Add ▾ → Add Subscription**.
14: 2. Set **Transaction Type** = `TRANSFER`.
15: 3. Set **From Account** = Bank Account (e.g. *HDFC Salary*) and **To Account** = Investment Account (e.g. *Mutual Funds*).
16: 4. Set **Frequency** = `Monthly` and enter your SIP amount (e.g., ₹5,000).
17: 
18: Every month, money automatically moves from your bank balance into your Mutual Fund account's cash pool. You can then update your mutual fund holdings under [Holdings & Portfolio](../02-accounts/holdings.md).
19: 
20: !!! warning "Don't log a transfer as Expense + Income"
21:     A common mistake is logging the outflow as an 'Expense' from one account and the inflow as 'Income' to another. This **double-counts** the money in your Analytics — inflating both your expense total and your income total by the transferred amount. Always use **Add → Internal Transfer** or recurring **Transfer** instead.
22: 
23: !!! example "Real-world use case"
24:     After seeing on the Trends page that ₹80,000 has been sitting idle in his SBI Savings Account for three cycles, Arjun decides to sweep ₹25,000 into his Mutual Funds account each month. He sets up the transfer via **Add → Internal Transfer**, **From Account: SBI Savings**, **To Account: Mutual Funds**, **Amount: 25,000**, and hits **Execute Transfer**. His Accounts page immediately shows SBI balance down by ₹25,000 and Mutual Funds up by ₹25,000 — net worth unchanged, but the asset allocation is now healthier.
25: 
26: <!-- TODO: screenshot (desktop, 1280x800) of Add ▾ → Internal Transfer form, source/destination account pickers -->
27: ![Add transfer form on desktop](../screenshots/06-transfers/add-transfer-form-desktop.png)
28: 
29: <!-- TODO: screenshot (mobile, 375x812) of the Internal Transfer form -->
30: ![Add transfer form on mobile](../screenshots/06-transfers/add-transfer-form-mobile.png)
31: 
32: <!-- TODO: screenshot (desktop, 1280x800) of a transfer row as it appears in /transactions/ -->
33: ![Transfer in transaction list on desktop](../screenshots/06-transfers/transfer-in-transaction-list-desktop.png)
34: 
35: <!-- TODO: screenshot (mobile, 375x812) of a transfer row in /transactions/ -->
36: ![Transfer in transaction list on mobile](../screenshots/06-transfers/transfer-in-transaction-list-mobile.png)
37: 
38: ## Related links
39: - [Holdings & Mutual Funds Portfolio](../02-accounts/holdings.md)
40: - [Accounts & Net Worth](../02-accounts/index.md)
41: - [Adding Expenses](../03-transactions-expenses/index.md)
42: - [Analytics, Trends & Financial Health](../11-analytics-and-health/index.md)
