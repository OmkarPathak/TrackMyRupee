# Capital Events

Record large, one-time payments without distorting your regular monthly expense analytics.

---

## 1. What Is a Capital Event

A Capital Event is a large, one-off payment that you do not want skewing your regular monthly spending charts.

Examples include a home loan down payment, a lump-sum medical bill, a car purchase, or a big renovation payment.

The rule of thumb is this: if the amount is large enough to make your average monthly expense chart look abnormal for several months, log it as a Capital Event instead of a regular expense.

---

## 2. What Capital Events Are Excluded From

Capital Events are excluded from:

- Monthly budget calculations
- Monthly average spend charts
- Budget bar progress for any category

They are included in:

- Your overall cash flow
- Your net worth calculations (your account balance decreases correctly)

This means your Dining Out, Transport, and Groceries budget bars remain unaffected by the one-off payment, and your Analytics average monthly expense chart continues to reflect your real recurring spend.

---

## 3. Opening the Capital Event Form


![The Capital Events page](img/capital-events-all-desktop.webp){ loading=lazy }


- **Desktop**: Navigate to **Sidebar → Capital Events → Add**.
- **Mobile**: Go to **More → Capital Events → Add**.

The form is titled "Add Capital Event".

---

## 4. Filling the Form

Complete the following fields:


![The Add Capital Event form](img/add-capital-event-desktop.webp){ loading=lazy }


1. **Amount** (required): The total amount of the payment.
2. **Date** (required): The date the payment was made.
3. **Subtype** (required): The category of the capital event, such as Home Renovation, Vehicle Purchase, Medical, or Loan Prepayment.
4. **Account** (optional): Link the event to an account to update its balance automatically.
5. **Linked Loan** (optional): Link a down payment or prepayment to a specific loan record so it appears in that loan's history.

Click **Save** when done.

---

## 5. Linking a Capital Event to a Loan

If you are recording a home loan down payment or a lump-sum prepayment on an existing loan, use the **Linked Loan** field to connect the Capital Event to that loan record.

The loan detail page will then show the capital payment as part of the total capital committed, giving you a complete picture of your repayment history.

!!! example "Real-world use case"
    Kavya pays a Rs. 4,00,000 home renovation advance in July. She logs it as a Capital Event with Subtype: Home Renovation, Account: HDFC Savings, Amount: 4,00,000, rather than as a regular expense. Her HDFC Savings account balance drops correctly and her net worth reflects the outflow. But her Dining Out, Groceries, and Transport budget bars are unaffected, and the Analytics average monthly expense chart for the rest of the year still shows her real recurring spend instead of a Rs. 4,00,000 spike warping every future month-over-month comparison.

---

## 6. Seeing Capital Events on Your Charts

Capital Events are kept out of your spending averages on purpose, but you still want to see **when** they happened. So they appear on the spending trend charts on your Dashboard as small **amber diamonds sitting on the bottom axis**, directly under the day (or month) they happened.


![Amber diamond markers on the dashboard chart](img/trend-markers-desktop.webp){ loading=lazy width="463" }


- On the **Daily Expenses** chart (a single month), look for a diamond under the date of the event.
- On the **Expenses Trend** chart (a year), a diamond sits under each month that has an event.
- **Hover** over that day or month, and the tooltip lists the event by name and amount alongside the usual figures, for example *Investment Lump Sum, ₹1L*.
- The chart legend has a matching **Capital event** entry, so the diamond is never a mystery.

A diamond never changes the height of the spending line or bars. It is a marker, like a pin on a map, and not a spend.

!!! tip "Some Flows create Capital Events for you"
    You will not always have to add them by hand. The [TMR Flows](../13-tmr-flows/index.md) create them where it makes sense: a loan's down payment, an FD's principal, a gold purchase, and a car bought with cash.

---

## Related Links
- [Loans](../08-loans/index.md)
- [Analytics, Trends and Financial Health](../11-analytics-and-health/index.md)
- [Budgets](../07-budgets/index.md)
