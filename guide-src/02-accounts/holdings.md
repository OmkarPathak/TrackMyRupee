title: Investment Holdings and Mutual Fund Portfolio
description: Learn how TrackMyRupee calculates holdings value, cost basis, and uninvested cash without double-counting transferred SIP funds.
keywords: TrackMyRupee holdings, mutual fund valuation, SIP transfer, uninvested cash, cost basis, net worth

# Investment Holdings and Mutual Fund Portfolio

Track your mutual funds, stocks, and asset valuations in real time with automated NAV synchronization, SIP transfer tracking, and precise portfolio calculations.

---

## 1. Overview and Navigation

Access your portfolio via **Sidebar → Net Worth → Holdings** on desktop or via **More → Holdings** on mobile (URL: `/holdings/`).

The Holdings page provides a consolidated dashboard of all active investments across your mutual fund, investment, NPS, PF, and brokerage accounts.

---

## 2. How NAV Calculations and Portfolio Metrics Work

TrackMyRupee uses a multi-tiered valuation engine to calculate your real-time investment net worth accurately.

### Valuation Resolution Order

For any active holding, the current unit value is resolved using the following order of priority:

1. **Live Daily NAV**: Fetched automatically from official AMFI (Association of Mutual Funds in India) and `MFapi.in` live feeds using the fund's 6-digit AMFI scheme code.
2. **Cached NAV Fallback**: If the live API is temporarily unreachable, TrackMyRupee retrieves the latest cached NAV from the local fund cache.
3. **Cost Basis Fallback**: If no NAV cache exists yet (for example, a newly added holding), the valuation falls back to your purchase cost basis (Units x Average Cost).

### Formulas

- **Holding Valuation**

  $$\text{Current Valuation} = \text{Units} \times \text{Latest Unit NAV}$$

- **Account Total Balance**

  $$\text{Cost Basis Total} = \sum \left( \text{Units}_i \times \text{Average Cost}_i \right)$$

  $$\text{Uninvested Cash} = \max\left(0, \text{Ledger Balance} - \text{Cost Basis Total}\right)$$

  $$\text{Total Account Balance} = \sum \left( \text{Units}_i \times \text{Latest Unit NAV}_i \right) + \text{Uninvested Cash}$$

!!! note "Correction applied"
    Before this fix (August 2026), transferred cash could be double-counted after the related holding was logged. This has been corrected by netting uninvested cash against active holdings cost basis.

- **Total Cost Basis**

  $$\text{Total Invested} = \sum \left( \text{Units}_i \times \text{Average Cost}_i \right)$$

- **Unrealized Gain / Loss**

  $$\text{Unrealized Gain} = \text{Total Portfolio Valuation} - \text{Total Invested Cost}$$

- **Growth Return Percentage**

  $$\text{Gain \%} = \left( \frac{\text{Unrealized Gain}}{\text{Total Invested Cost}} \right) \times 100$$

---

## 3. How to Set Up Recurring Transfers for SIPs

A **Systematic Investment Plan (SIP)** involves transferring a fixed amount regularly (for example, Rs. 5,000 monthly) from your primary bank account into your mutual fund account.

### Step-by-Step SIP Setup

1. Navigate to **Add → Add Subscription** (or **Sidebar → Subscriptions → Add**).
2. Set **Transaction Type** to `TRANSFER`.
3. Set **Description** to your SIP name (for example, "Monthly SIP - Parag Parikh Flexi Cap").
4. Set **Amount** to your monthly SIP allocation (for example, `5000`).
5. Set **From Account** to your liquid bank account (for example, HDFC Salary Account).
6. Set **To Account** to your investment account (for example, Mutual Funds / Zerodha Coin).
7. Set **Frequency** to `Monthly` and select your SIP start date.
8. Click **Save Subscription**.

!!! info "How SIP transfers affect your balance"
  On each scheduled date, TrackMyRupee automatically logs an Internal Transfer. Money leaves your bank account and enters your Mutual Fund account as ledger cash. Net worth does not change because the money is still in your own accounts. Once you log the resulting holding, that invested portion is counted through the holding value instead of separate cash. Any amount not yet allocated to a logged holding continues to appear as pending uninvested cash.

---

## 4. How Holdings and Units Are Updated

As your SIP executes or when you make lump-sum investments, your holdings update in two distinct ways.

### Adding and Removing Holdings

1. Go to **Holdings** (`/holdings/`) or your account detail page.
2. Click **Add Holding** and enter the fund name (pick it from the search so its scheme code is filled in), your **Units** and your **Avg Cost** per unit. Both must be greater than zero. If you leave the scheme code blank, TrackMyRupee tries to find it from the name.
3. The cost basis is calculated immediately:
   $$\text{Cost Basis} = \text{Units} \times \text{Avg Cost}$$

A holding cannot be edited in place. When more units are allocated, remove the old holding and add it again with the new cumulative units and average cost. **Delete** only hides the holding (it stops counting in the portfolio), it does not touch your account balance.

The totals at the top of the Holdings page are in your own currency: a holding in another currency is converted at the latest exchange rate before it is added in.

### Automatic Daily NAV Updates

- **Automatic background sync**: TrackMyRupee runs daily background jobs that pull updated NAVs after Indian market closing hours (approximately 11 PM IST).
- **Manual instant refresh**: Click the **Refresh NAV** icon next to any holding row to trigger an immediate live fetch.
- **External cron integration**: Set up a free cron job at [cron-job.org](https://cron-job.org) targeting `GET /api/cron/sync-nav/` with `X-Cron-Secret: <YOUR_CRON_SECRET>` for guaranteed daily automated syncing.

---

## 5. Responsive Interface Features

- **Desktop view**: Full multi-column table displaying Fund Name, Account, Units, Average Cost, Current Valuation, NAV, and action buttons.
- **Mobile view**: Single-card list with touch-friendly controls. Long scheme names such as "Parag Parikh Liquid Fund - Direct Plan - Growth" wrap onto multiple lines without clipping. Tap the three-dot menu on any holding row to Refresh NAV or Delete Holding.

---

## Related Links
- [Accounts and Net Worth](index.md)
- [Accrued vs Invested Balance View](accrued-vs-invested.md)
- [Recurring Transactions and Subscriptions](../05-transactions-recurring/index.md)
- [Transfers and Internal Movements](../06-transfers/index.md)
- [Analytics and Financial Health](../11-analytics-and-health/index.md)
