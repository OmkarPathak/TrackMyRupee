# Investment Holdings & Mutual Fund Portfolio

Track your mutual funds, stocks, and asset valuations in real-time with automated Net Asset Value (NAV) synchronization, SIP transfer tracking, and precise portfolio calculations.

---

## 1. Overview & Navigation

Access your portfolio via **Sidebar → Net Worth → Holdings** on desktop or via **Mobile Menu → Holdings Portfolio** on mobile (URL: `/holdings/`).

The Holdings page provides a consolidated dashboard of all active investments across your mutual fund, investment, NPS, PF, and brokerage accounts.

---

## 2. How NAV Calculations & Portfolio Metrics Work

TrackMyRupee uses a multi-tiered valuation engine to calculate your real-time investment net worth accurately.

### 📐 Valuation Resolution Hierarchy
For any active holding, the current unit value is resolved using the following order of priority:

1. **Live Daily NAV**: Fetched automatically from official AMFI (Association of Mutual Funds in India) and `MFapi.in` live feeds using the fund's 6-digit AMFI scheme code.
2. **Cached NAV Fallback**: If the live API is temporarily unreachable, TrackMyRupee retrieves the latest cached NAV from `FundNAVCache`.
3. **Cost Basis Fallback**: If no NAV cache exists yet (e.g. newly added holding), the valuation falls back to your purchase cost basis ($\text{Units} \times \text{Average Cost}$).

### 🧮 Mathematical Formulas

* **Holding Valuation**:
  $$\text{Current Valuation} = \text{Units} \times \text{Latest Unit NAV}$$

* **Account Total Balance**:
  $$\text{Total Account Balance} = \text{Uninvested Ledger Cash} + \sum \left( \text{Units}_i \times \text{Unit NAV}_i \right)$$

* **Total Cost Basis**:
  $$\text{Total Invested} = \sum \left( \text{Units}_i \times \text{Average Cost}_i \right)$$

* **Unrealized Gain / Loss**:
  $$\text{Unrealized Gain} = \text{Total Portfolio Valuation} - \text{Total Invested Cost}$$

* **Growth Return Percentage**:
  $$\text{Gain \%} = \left( \frac{\text{Unrealized Gain}}{\text{Total Invested Cost}} \right) \times 100$$

---

## 3. How to Setup Recurring Transfers for SIPs

A **Systematic Investment Plan (SIP)** involves transferring a fixed amount regularly (e.g. ₹5,000 monthly) from your primary bank account into your mutual fund account.

### Step-by-Step SIP Setup:

1. Navigate to **Add ▾ → Add Subscription** (or **Sidebar → Subscriptions → Add**).
2. Set **Transaction Type** to `TRANSFER`.
3. Set **Description** to your SIP name (e.g. *"Monthly SIP – Parag Parikh Flexi Cap"*).
4. Set **Amount** to your monthly SIP allocation (e.g., `5000`).
5. Set **From Account** to your liquid bank account (e.g., *HDFC Salary Account*).
6. Set **To Account** to your investment account (e.g., *Mutual Funds / Zerodha Coin*).
7. Set **Frequency** to `Monthly` and select your SIP start date.
8. Click **Save Subscription**.

!!! info "How SIP Transfers Affect Your Balance"
    On each scheduled date, TrackMyRupee automatically logs an **Internal Transfer**. Money leaves your bank account and enters your Mutual Fund account as **Uninvested Ledger Cash**. Your total net worth remains untouched while your liquid cash transitions into investment capital!

---

## 4. How Holdings & Units are Updated

As your SIP executes or when you make lump-sum investments, your holdings update in two distinct ways:

### 📥 1. Updating Units & Purchase Price
When new units are allocated by the AMC:
1. Go to **Holdings** (`/holdings/`) or your account detail page.
2. Click **+ Add Holding** (or update your existing holding).
3. Enter your updated cumulative **Units** and average purchase price (**Avg Cost**).
4. The system updates your cost basis instantly:
   $$\text{New Cost Basis} = \text{Updated Units} \times \text{New Avg Cost}$$

### 🔄 2. Automatic Daily NAV Updates
- **Automatic Background Sync**: TrackMyRupee runs daily automated background jobs that pull updated NAVs after Indian market closing hours (~11 PM IST).
- **Manual Instant Refresh**: Click the **Refresh NAV** icon ($\circlearrowright$) next to any holding row to trigger an immediate live fetch.
- **External Cron Integration**: Set up a free cron job at [cron-job.org](https://cron-job.org) targeting `GET /api/cron/sync-nav/` with `X-Cron-Secret: <YOUR_CRON_SECRET>` for guaranteed daily automated syncing.

---

## 5. Responsive Interface Features

- **Desktop View**: Full multi-column table displaying Fund Name, Account, Units, Average Cost, Current Valuation & NAV, and Action buttons.
- **Mobile View**: Symmetrical single-card list with touch-friendly controls:
  - **No Text Clipping**: Long scheme names (e.g. *"Parag Parikh Liquid Fund- Direct Plan- Growth"*) automatically wrap onto multiple lines.
  - **3-Dot Action Menu**: Tap the vertical menu icon on any holding row to **Refresh NAV** or **Delete Holding**.

---

## Related Links
- [Accounts & Net Worth](index.md)
- [Recurring Transactions & Subscriptions](../05-transactions-recurring/index.md)
- [Transfers & Internal Movements](../06-transfers/index.md)
- [Analytics & Financial Health](../11-analytics-and-health/index.md)
