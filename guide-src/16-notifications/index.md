---
title: Notifications and Reminders
description: What TrackMyRupee notifies you about, when, and how to control it - the bell, push notifications, email digests and the daily reminder.
keywords: TrackMyRupee notifications, reminders, push notifications, budget alerts, goal milestones
---

# Notifications and Reminders

TrackMyRupee tells you about things that need your attention: a bill that is about to fall due, a budget you are close to, a goal you have hit. This page lists every alert, when it fires, and how it reaches you.

---

## 1. The bell and the Notifications page

The **bell** in the top bar shows the latest nine unread alerts. The red badge counts **all** your unread alerts (it reads **9+** once there are more than nine). Tap an alert to open the page it is about; it is marked as read at the same time.

Open **Notifications** (`/notifications/`) for the full list: newest first, 20 to a page. **Mark all as read** clears the badge in one go. Alerts are kept for 90 days, except goal milestones, which are kept so that they are never announced twice.

---

## 2. What you get alerted about

| Alert | When it fires |
|---|---|
| **Upcoming recurring item** | Exactly 3 days before a subscription, recurring income, recurring transfer or insurance premium is due |
| **Credit card bill** | 3 days before the billing day you set on a credit card, BNPL or overdraft account |
| **Budget Alert** | When a category with a monthly limit reaches its warning level (see [Budgets](../07-budgets/index.md)) |
| **Budget Exceeded** | When that category goes over its limit |
| **Goal milestone** | When a [goal](../12-goals/index.md) reaches **50%**, **90%** and **100%**. Each is sent **once, ever**: a goal that stays at 60% does not remind you every month. If one deposit jumps a goal from 40% to 100%, you get the 100% alert only |
| **Plan ending** | 2 days before a paid plan (Plus or Pro, not lifetime) ends |
| **Currency change** | When recalculating your history to a new base currency finishes, or fails |

Recurring and budget alerts are sent **at most once a month** for each item. Your plan's limits apply here too: you are alerted only for the subscriptions, budget categories and goals your plan keeps open.

---

## 3. How alerts reach you

- **In the app**: always, in the bell and on the Notifications page.
- **Push notifications**: if you have turned them on in your browser or in the installed app. Each push opens the page it is about. Allow notifications when your browser asks, and open the app once on each device you want them on.
- **Email**: Plus and Pro get one **digest** a day listing everything new (a single alert is sent with its own title instead). Free accounts get no alert emails.

---

## 4. The daily expense reminder

Once a day you can get a push reminder to log your expenses. It is **skipped on any day you have already added an expense**, and it does not create an entry in the bell.

Turn it on or off in **Settings → Profile → Daily Expense Reminder**. It is on by default, and it only reaches devices with push turned on.

---

## 5. Common questions

**I added a transaction but my budget alert has not come.** Alerts are checked by a scheduled job, not the moment you save, so they arrive on its next run. The Budget page itself updates immediately.

**I get the same alert every month.** Recurring and budget alerts are monthly by design, because your bill and your spending are monthly. Goal milestones are one-time.

**I turned push on but nothing arrives.** Push needs the installed app or a browser that supports it, and notification permission for the site. See [the mobile app](../20-mobile-app/index.md).

---

## Related Links
- [Budgets](../07-budgets/index.md)
- [Savings Goals](../12-goals/index.md)
- [Recurring and Subscriptions](../05-transactions-recurring/index.md)
- [Accounts](../02-accounts/index.md)
