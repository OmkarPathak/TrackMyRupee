title: Accrued vs Invested Balance View
description: Understand when to use Invested or Accrued balance view in TrackMyRupee for deposits, holdings, assets, and insurance.
keywords: accrued vs invested, TrackMyRupee balance view, FD accrued value, holdings valuation, insurance surrender value

# Accrued vs Invested Balance View

TrackMyRupee lets you switch between two ways of viewing value for selected account types.

- **Invested**: what you originally put in (baseline).
- **Accrued**: what it is worth right now (interest-accrued, market-priced, or appraised current value).

---

## 1. Where This Toggle Applies

The toggle applies to account types where "invested amount" and "current value" can be meaningfully different:

- Fixed Deposits and Recurring Deposits
- Mutual Funds and Demat Holdings
- Physical Assets (for example, real estate or vehicle)
- Insurance policies with surrender-value tracking

The toggle does **not** apply to account types that already represent today's real value directly:

- Cash Wallet, Savings, Salary, Current, and other liquid bank accounts: the ledger balance is already today's value.
- Credit Cards and Short-Term Credit: these are liabilities, so there is no separate invested baseline concept.
- Loan Accounts: this is outstanding liability tracking, not an invested-versus-accrued asset view.

---

## 2. Worked Examples

### Example A: Fixed Deposit

Rahul opens an FD with **Rs. 2,00,000** at **7.0%**.

- **Invested view** shows: **Rs. 2,00,000**
- **Accrued view** (after interest accrual) might show: **Rs. 2,14,860**

This helps Rahul compare principal committed versus current maturity-linked value.

### Example B: Holdings Account (SIP + Market Value)

Meera transfers **Rs. 1,00,000** into her MF account and logs holdings with total cost basis **Rs. 95,000**. Current market value of those holdings is **Rs. 98,000**.

Using TrackMyRupee's current holdings formula:

$$\text{Uninvested Cash} = \max(0, 1,00,000 - 95,000) = 5,000$$

$$\text{Accrued Value} = 98,000 + 5,000 = 1,03,000$$

- **Invested view**: **Rs. 95,000**
- **Accrued view**: **Rs. 1,03,000**

!!! example "Real-world use case"
    Meera uses Invested view during monthly planning to track how much capital she has actually deployed, and switches to Accrued view on review day to see current market-linked net worth including pending uninvested cash.

---

## 3. Insurance-Specific Note

For insurance accounts, if no surrender value is recorded yet, both views can show **Rs. 0** by design. This avoids a misleading impression that paid premium always equals current realizable value, which is usually not true for fresh or early-stage policies.

---

## Related Links
- [Managing Accounts](index.md)
- [Holdings and Mutual Funds Portfolio](holdings.md)
- [Loans](../08-loans/index.md)