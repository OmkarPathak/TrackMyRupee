title: Accounts and Net Worth
description: Understand account types, net worth calculation, and how recurring deposits and valuations are handled in TrackMyRupee.
keywords: TrackMyRupee accounts, net worth calculation, recurring deposit, account types, holdings

# Accounts and Net Worth

Manage all your assets and liabilities in one place and track your overall net worth accurately.

---

## 1. Opening the Accounts Page

Navigate to **Sidebar → Accounts** on desktop, or tap the **Accounts** tab in the bottom navigation on mobile.


![The Accounts page](img/accounts-list-desktop.webp){ loading=lazy }


From top to bottom the page has:

- **Net worth card**: your total in your own currency, with how much it changed against a month ago, a coloured bar that splits what you hold (Cash & Bank, Fixed-Income, Investments, Physical Assets and so on), and a line showing what you hold and what you owe on cards and loans. Click a part of the bar's legend to look at only that kind of account.
- **Needs your attention**: short chips for what to look at soon: a credit card bill due in the next 10 days, deposits maturing in the next 6 months (with how many more come later), and balances that have not been updated in 30 days or more.
- **Group tiles**: one tile per kind of account with its total and how many accounts it holds. Click a tile to show only those accounts, click it again to show all. **Owed on cards** and **Owed on loans** show what you owe in red.
- **All / Pinned / Needs update tabs**: switch the list to your pinned accounts, or to the manually kept balances (deposits, holdings, property, insurance) that are 30+ days old. Cash and card balances never count as stale because every transaction updates them.
- **The list**: balance, last-updated age (with a quick **Update** link when a balance is stale), interest rate and maturity date for deposits, and limit, available credit and next bill date for credit cards. Use the ⋮ menu on a row to view, reconcile, edit, pin or delete an account. The list shows the first five accounts; **Show all** reveals the rest.

The search box, **+ Filter** (account type, status, pinned, needs update) and sort menu sit above the net worth card. Searching and filtering narrow the list; the net worth card, tiles and tab counts always describe all your accounts.

Each account is valued the way Net Worth values it (market value for funds, accrued interest for deposits, outstanding principal for loans), accounts in another currency are converted once, and what you owe on **credit cards and loans counts as negative**. Each row still shows the account's balance in its own currency, and the coloured bar splits only what you hold.

!!! info "Accounts total vs. Net Worth"
    The accounts net worth and the Dashboard **Net Worth** card agree on the same accounts. Net Worth adds two things the accounts list cannot show: money you have set aside in [Goals](../12-goals/index.md) (it is still yours) and any active loan you track that is not linked to a loan account.

---

## 2. Adding an Account

Click **+ Add** next to Accounts in the sidebar on desktop, or tap **+ Add** on the Accounts page on mobile.


![The Add New Account form](img/add-account-desktop.webp){ loading=lazy }


The form is a two-step wizard:

1. **Account Basics**: Enter Account Name, Account Type, Currency, and Initial Balance.
2. **Type-specific fields**: Fill in details that apply to your account type. For example, Fixed Deposits show deposit terms and maturity date fields. Credit Cards show a credit limit field.

Click **Save** when done.

Account names must be unique among your active accounts (capital letters do not matter, so "HDFC" and "hdfc" clash). A credit limit and a deposit principal cannot be negative, and an interest rate must be between 0 and 100.

!!! info "How many accounts you can have"
    Free allows 2 active accounts, Plus allows 10, and Pro is unlimited. Deleted accounts do not count. If you go over a limit (for example after a downgrade), your **oldest** accounts stay open and the newer ones are locked until you upgrade or delete some: a locked account cannot be opened, edited or used in a transfer. Pinning or searching never changes which accounts are locked.

!!! info "Balances are stored in the account's currency"
    An expense or income in another currency is converted into the account's currency when it is posted. Because of that, the **currency of an account is locked** as soon as it has any transaction (expense, income, transfer, goal contribution, loan repayment or capital event). Change it only while the account is unused. If you later edit or delete such a transaction, the account gets back exactly the amount it took or gave at the time, even if the exchange rate has moved since.

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
| Life Insurance | Life, endowment or term policy |
| Health / Medical Insurance | Health cover |

!!! info "Note on Recurring Deposits (RD)"
    RD valuation is not treated like a single lump-sum FD from day one. Each installment accrues from its own deposit date, and only installments that were actually posted are counted. If a month is skipped, that installment is not assumed automatically.

!!! info "How a deposit's interest is counted"
    Time is counted the way a bank does it: whole years from the start date, then the extra days over 365. A one-year deposit is therefore exactly one year, even across 29 February, so a deposit that has reached its maturity date shows the maturity amount your bank quotes (for example, ₹1,00,000 at 7% compounded quarterly for one year is **₹1,07,185.90**). Accrual stops at the maturity date, or at the closing date if you closed it early. An RD installment paid from an account in another currency is counted in the RD's own currency.

!!! info "Exchange rates"
    Amounts in another currency are converted at a rate fetched from a live exchange-rate service, once a day per currency pair. If the service cannot be reached, the most recent rate we already have is used instead, so your pages keep working. The rate used for a transaction is saved with it. Net worth for a past date uses the rate on or before that date.

!!! info "Note on Credit Card, BNPL, and Overdraft Billing Dates"
    For revolving credit accounts (Credit Card, BNPL, Overdraft), you can set an optional **Billing Day of Month** (1–31). TrackMyRupee computes the next upcoming billing date, displays a badge on your account list, and sends a **3-day advance reminder**.
    - **In-App Notification Panel**: Delivered to all active users.
    - **WebPush Notifications**: Delivered to all active users who have enabled browser push notifications.
    - **Email Digest**: Included in daily financial digest emails for Plus and Pro tier subscribers.

---

### Insurance Policies

An insurance account holds the details of one policy. When you create it, choose a **Policy type**:

- **Savings / endowment** (pays back on maturity): premiums paid so far count as the policy's value.
- **Pure protection** (term, health): worth nothing in net worth. The account shows **Premiums Paid** instead of a balance, with the note that it is not counted in your net worth. A new Health / Medical Insurance account starts as pure protection and a Life Insurance account as savings, and you can change it.

On the account's page you also see the policy number, sum assured, start date, policy type, premium and frequency, the **next premium** date and the account it is **paid from**. Open the **Insurance** group on the Accounts page and the list gets a **Policy number** column. The list shows the columns that matter for the group you are looking at (on a wide screen): **Rate** and **Matures** for Fixed income, **Limit**, **Used** and **Next bill** for credit cards, and just **Last updated** and **Balance** everywhere else.

Premiums are posted as expenses tied to the policy, from the **premium payment account** you pick, on the schedule you choose. If the policy started in the past, switch on **Create past premium records** to add the premiums that fell due since the start date. Left off, only upcoming premiums are scheduled and the premium day of the policy is kept.

!!! warning "Do not record a premium twice"
    Premiums paid so far are the premium expenses linked to the policy **plus** money moved into the policy account. If you pay a premium through the schedule and also transfer the same money into the policy account, it is counted twice. Use one or the other.

## 4. Editing Account Details and Balance

Click any account row to open its detail page. Use the pencil (edit) icon to update the Account Name, Initial Balance, or any type-specific fields.

**Deleting an account does not erase anything.** It marks the account inactive: it disappears from the list, from transaction pickers and from net worth, but every expense, income, transfer and other row you posted to it stays in place, and you can still open its history. To see deleted accounts, open the **Inactive** view on the Accounts page.

Use **Restore** on an inactive account to bring it back. Restoring is refused if you are already at your plan's account limit, or if another active account now has the same name (rename one of them first).

!!! warning "Editing the balance"
    Editing the balance by hand treats the number you enter as the truth and records the difference as an adjustment. It does not create an income or expense, so your reports do not change.

### Transfers between accounts

Moving money between your own accounts is covered in [Transfers](../06-transfers/index.md). Editing or deleting a transfer puts the money back exactly as it was before applying the change.

---

## 5. Reading Account Transaction History

Click any account row to open its detail page. Below the balance and its trend chart is the account's full **Transaction History**: every expense, income, transfer in or out, goal contribution, loan repayment and capital event posted to that account.


![An account with its balance trend and history](img/account-detail-desktop.webp){ loading=lazy }


The table looks and behaves like the [All Transactions](../03-transactions-expenses/index.md) page:

| Column | What it shows |
|---|---|
| **Date** | When it happened |
| **Type** | A coloured pill: Expense, Income, Transfer, Savings, Loan Repayment or Capital Event |
| **Description** | Your note (long notes are cut short; hover to read all of it) |
| **Category/Source** | The expense category, income source, the other account of a transfer ("To SBI Savings", "From HDFC"), the goal, the loan, or the capital event type |
| **Amount** | Green with **+** when money came into this account, red with **-** when it left. A foreign-currency row also shows its value in your own currency underneath. A capital event you excluded from net worth never moved the balance, so it is shown in grey with no sign |
| **Actions** | Edit or delete the row (or open its goal or loan). After saving you land back on this page |

Click the **Amount** heading to sort by amount, and again to flip the order. On a phone the rows become cards grouped by day.

Under the title, a line sums up what you are looking at: the number of transactions, the money **in**, the money **out** and the **net**. The totals are in the account's own currency and follow your filters. The count in the card at the top is always the account's total, whatever you filter.

### Searching and filtering

The same toolbar as the other list pages sits above the table:

- **Search** matches descriptions, categories, income sources, goal and loan names.
- **Time range** (All time by default), **Sort**, and **Filter** with **Transaction Type**, **Category** and **Amount**. Picking a category shows only expenses and income, because transfers, savings and loan rows have none.
- Each active filter appears as a chip you can clear on its own, or use **Clear all**.

## 6. How Net Worth Is Calculated

**Net worth = what you hold - what you owe.** The **Net Worth** tile on the Dashboard sums your active accounts, each valued by its type, and subtracts liabilities. Everything is converted to your currency at the latest exchange rate.

- **Cash and Bank accounts**: ledger balance
- **Fixed Deposits and other deposit accounts**: principal plus accrued interest
- **Mutual Funds and Demat**: latest value of each holding, plus any cash in the account that is not invested
- **Physical Assets**: latest valuation, or the purchase cost if you have not valued it yet
- **Insurance (savings / endowment)**: the latest surrender value if you have recorded one, otherwise the premiums you have paid so far (premium expenses linked to the policy plus any money you moved into the policy account)
- **Insurance (pure protection, term and health)**: zero. The cover pays nothing back, so premiums are a cost and never count as an asset
- **Credit Cards**: the balance owed is subtracted. A card in credit (you overpaid) adds to your net worth instead
- **Loans**: the outstanding principal, which is the amount borrowed minus the principal you repaid, minus prepayments and down payments, minus any principal you had already paid before you started tracking. A loan linked to a loan account is counted once through that account, and an active loan with no loan account is subtracted directly. A fully paid-off loan counts as zero
- **Goals**: money moved into a goal leaves your account but is still yours, so it is added back and net worth does not drop when you save towards a goal

Inactive (deleted) accounts are left out.

### Change this month and the trend

The change shown on the Net Worth tile is what you **saved this month**: income minus spending, using the same definition as the savings rate. Repaying loan principal is not spending (your cash goes down and your debt goes down by the same amount), only the interest is. Capital events count only when they are not excluded from averages.

The sparkline walks back from today's figure by each earlier month's savings, so it is an **estimate**. It does not know about market price changes, revaluations or money moved without being recorded, so use it for direction rather than for exact past values. A snapshot of your net worth is also saved each day for the record.

!!! example "Real-world use case"
    Before deciding whether to make a Rs. 60,000 laptop purchase, Rahul opens Accounts to check his real net worth across his HDFC Salary Account, SBI Savings Account, and Cash Wallet in one place rather than opening three separate banking apps. The Filtered Total Balance shows Rs. 1,92,000 across all three, confirming he can absorb the purchase without going below his Rs. 1,00,000 emergency reserve.

---

## Related Links
- [Holdings and Mutual Funds Portfolio](holdings.md)
- [Accrued vs Invested Balance View](accrued-vs-invested.md)
- [Getting Started](../01-getting-started/index.md)
- [Transfers](../06-transfers/index.md)
- [Analytics: Spending, Cashflow and Net Worth](../11-analytics-and-health/index.md)
