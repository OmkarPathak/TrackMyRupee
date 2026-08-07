# Savings Goals

Set a savings target, contribute funds from any account, and watch a progress bar move toward your goal.

!!! note "Plan required"
    Savings Goals are available on the **Pro** plan. The Goals link appears in the sidebar and in the mobile **More** sheet under **Net Worth** only when your account has the Pro tier active.

## Steps

1. **Open the Goals page** — **Sidebar → Goals** (desktop) or **More → Goals** (mobile).
   Click **+ Add Goal** to open the form.

2. **Fill the goal form** — required fields:
   - **Goal Name** — e.g. "Goa Trip Fund", "Emergency Reserve", "MacBook"
   - **Target Amount** — the ₹ figure you're saving toward
   - **Target Date** (optional) — a deadline; the progress bar will show how much you need to save per remaining salary cycle
   - **Linked Account** — the account whose balance contributions will be tracked against this goal

   Click **Save**.

   <!-- TODO: screenshot (desktop, 1280x800) of the Add Goal form -->
   ![Add goal form on desktop](../screenshots/12-goals/add-goal-form-desktop.png)

   <!-- TODO: screenshot (mobile, 375x812) of the Add Goal form -->
   ![Add goal form on mobile](../screenshots/12-goals/add-goal-form-mobile.png)

3. **Log a contribution** — navigate to **Sidebar → Goals**, find your goal card, and click **Add Contribution**. Enter the amount and the source account. The contribution is recorded as a `GOAL_CONTRIBUTION` ledger entry — it debits the source account and credits the goal, so your net worth stays accurate.

   <!-- TODO: screenshot (desktop, 1280x800) of the goal card with progress bar -->
   ![Goal card with progress bar on desktop](../screenshots/12-goals/goal-card-progress-desktop.png)

   <!-- TODO: screenshot (mobile, 375x812) of the goal card -->
   ![Goal card with progress bar on mobile](../screenshots/12-goals/goal-card-progress-mobile.png)

4. **Reading the progress bar** — the card shows:
   - **Saved so far** (₹ contributed)
   - **Remaining** (₹ still needed)
   - **% complete** (progress bar)
   - **Per-cycle target** (if you set a Target Date: how much you need to save each salary cycle to hit the deadline)

5. **Editing or closing a goal** — click the goal card's **⋮** menu to edit details or mark the goal as **Completed**. Marking complete does not withdraw or move the funds; it simply archives the goal so it no longer appears in the active list.

!!! tip "Use a dedicated savings account"
    Create a separate **Savings Account** (e.g. "Goa Trip Savings") in the app and link it to the goal. Transfer your monthly contribution from your salary account to this dedicated account via **Add → Internal Transfer**. The goal's progress bar will reflect the transferred amount automatically.

!!! example "Real-world use case"
    Priya wants to save ₹80,000 for a Goa trip in March (4 salary cycles away). She creates a goal: **Goal Name: Goa Trip**, **Target: ₹80,000**, **Target Date: 01 Mar**. The app tells her she needs to contribute ₹20,000 per cycle. Each time her salary lands, she transfers ₹20,000 to her "Goa Savings" account and logs a goal contribution. The progress bar goes from 0% → 25% → 50% → 75% → 100% — giving her a visible commitment device that's harder to ignore than a mental note.

## Related links

- [Getting Started](../01-getting-started/index.md)
- [Accounts & Net Worth](../02-accounts/index.md)
- [Transfers](../06-transfers/index.md)
- [Analytics & Health](../11-analytics-and-health/index.md)
