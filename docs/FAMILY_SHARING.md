# Family sharing: design note

Status: built (sharing, family net worth and inventory, gentle notes, emergency access). Behind a flag.

## What it is for

Two common questions in an Indian household that no single person's app answers:

1. **What is our family's combined net worth?**
2. **Which credit cards, insurance policies and accounts does our family hold, and who holds them?**

Each person keeps their own TrackMyRupee account and chooses which of their accounts to share. The family sees
a combined, read-only picture of only what was shared.

## Principles

1. **Default deny.** Nothing is visible to the family until the owner shares it. Joining a family shares nothing.
2. **Accounts, not transactions.** The unit of sharing is an account (a bank account, card, policy, loan, deposit or
   property). A member never sees a transaction, category, note or receipt, and never the holdings inside an account.
3. **One narrow door.** `expenses/family/summary.py` is the only code that reads another member's data. It returns
   plain values with a fixed set of fields, never model instances, so no view or template can reach a private field.
4. **Same numbers as the owner's own screens.** Values come from the same valuation code as the owner's net worth
   (`LedgerReadService.get_net_worth(only_account_ids=...)`), so a shared amount never disagrees with the owner's page.
5. **Family pages are separate pages.** The Me / Family toggle is a link between an existing page and its family
   page, not a mode inside the existing page, so no existing query changes.

## What a family member can see of a shared account

| Always | Name (or the nickname the owner chose), type, who holds it |
|---|---|
| If "show amounts" | The balance or amount (negative for what is owed) |
| Credit card | Limit, used, available, next bill date |
| Insurance | Cover, premium and how often, next due date, who it covers |
| Loan | Outstanding amount and EMI |
| If "list only" | Nothing beyond the always-visible row; the item is not counted in totals |

**Never shared:** transactions, categories, notes, receipts, holdings inside an account, account or policy numbers,
reconciliation history, accounts that were not shared (or the fact that they exist).

## Levels

Each shared account has one of two levels: **show amounts** (counts in the family net worth) or **list only** (the
family sees that the item exists, with no amounts, and it is not counted).

## History

Family history comes from the daily net worth snapshots, which store a per-account breakdown. It uses only the
accounts shared, from the day each account's **amounts first became visible** (`amounts_since`), and only **month-end points**. An account that was list only, and is later switched to show amounts, never reveals the months before that. Daily movements are never shown,
because a balance dropping on a given day reveals a purchase. The snapshot totals are never reused, because they
include private accounts.

## Plans

Family view needs **every member on Pro** (`UserProfile.is_pro`, which respects expiry, lifetime and trial).

- A warning shows from 14 days before any member's Pro ends.
- When it ends, family view pauses for everyone. Nothing is deleted: shares, members and the owner's own data are
  untouched, and family view resumes as soon as every member is on Pro again.
- An invitee without Pro is offered the 7-day trial or the plans page before joining.

## Households

- A user belongs to at most one household; a household has at most 5 members.
- The creator is the owner. Any member can leave. Only the owner can remove another member.
- Leaving or being removed ends all sharing in both directions immediately.
- An invitation is a single-use link with a hashed token that expires after 7 days.
- Joining records the member's consent (`ConsentEvent`, purpose `family_sharing`).

## Threat model: where private data could leak, and the guard for each

| Surface | Risk | Guard |
|---|---|---|
| Family pages and JSON | A private field reaches a template | Templates only receive the summary service's plain values |
| Totals and history | A total or a movement reveals a private item | Only shared items are summed; history is month-end from the share date |
| Cache | An unshared item still shows after unsharing | One cache version per household, bumped on every share, member and plan change |
| Emails and notifications | A message names a private item | Family messages are built from the summary values only |
| Invitations | A leaked link joins a stranger | Hashed single-use token, 7-day expiry, rate limit, the invitee must sign in |
| Another household | A member opens someone else's family page | Every family view resolves the household from the signed-in user, never from the URL |
| Exports | A member exports another member's data | Exports stay user-scoped; family data is never exported |
| Display names | A nickname leaks something private | The owner types it, and the preview shows exactly what family sees |

## How it is tested

- **Canary tests** (`expenses/tests/family_canary.py`): unique marker strings are planted in private data (a
  transaction's description, a category, an income note, a private account's name, a policy number). Every family
  page, JSON reply, email and notification must contain none of them.
- **Access matrix:** member, non-member, a member of another household and anonymous, against every family URL.
- **Pro-gate states:** everyone on Pro, a warning, paused, and an invitee without Pro.
- **Postgres:** the suite runs on PostgreSQL, which enforces what SQLite lets through.

## Rollout

Behind `FAMILY_ENABLED` (off by default) and `FAMILY_USER_IDS` for a cohort, the same way the ledger rollout
works. With the flag off, the Family menu item and every family URL are absent.

## Decisions made

- History is month-end only (not weekly).
- "Covers" on an insurance policy is free text, entered as names, with the household's members suggested.
- Maximum 5 members; one household per user.
- Viewers are not a separate role: every member needs Pro and every member shares for the whole household.

## Gentle notes

A few calm, optional observations on Who holds what, built only from the family summary (what is already shared):
a health-cover gap (who has none shared yet, with "if not, there is nothing to do"), card bills due in 30 days,
premiums due in 30 days. They never compare people or judge an amount. Each person can dismiss a note (stored per
person by a key that names what it was about) or switch notes off for themselves.

## Emergency access

An emergency sheet lists what exists and where to look: the accounts the owner puts on it, a nominee for each asset,
where documents are, people to call. Text that looks like an account or card number is refused. The owner chooses who
may ask (only people in the same family) and a mode:

- **On request (default):** a request tells the owner (in the app and by email). They can approve or deny at any time.
  If they do not answer within `FAMILY_EMERGENCY_DELAY_DAYS` (7) the requester gets a read-only copy, and the owner is
  told. A denied person may ask again after `FAMILY_EMERGENCY_RETRY_DAYS` (7). The owner can withdraw access later.
- **Always visible** to the people named.

Access never depends on a plan (a lapsed subscription must not lock a family out), ends when anyone leaves or is
removed, and answers 404 to anyone not allowed so the sheet's existence is not hinted at. Reading a sheet never
writes: the "delay ran out" notice is sent by the notification job or when the owner opens their emergency page.

## Safeguards added after review

- Seat counts, invitations and ownership changes run under a row lock on the household.
- `Household.owner` is `PROTECT`: deleting an owner directly is refused. The account deletion flow hands the family to
  the longest-standing member (or ends it) before deleting the user.
- Deleting or switching off an account bumps the household cache version at once (`expenses/family/signals.py`).
