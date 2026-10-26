# TrackMyRupee: decisions and trade-offs

A running record of why the codebase looks the way it does. This is the public copy: hosting, incident history and interview preparation are kept in private notes. For every decision: what we chose, what we gave up, what
we rejected, and when to revisit it. Written so that you can (a) remember the reasoning months later, (b) explain it to
someone else, and (c) onboard a new contributor.

**How to keep it useful**

- Add an entry (template at the end) in the same commit as the change. One line in the [changelog](#changelog) is enough
  for small things; a full entry for anything with a real trade-off.
- Record numbers you measured, and say how you measured them. Mark anything you did not verify as such.
- Never delete an old entry that was reversed: mark it **Superseded by** the new one. The reversals are the best
  interview material.

Conventions: `path:line` references drift, so entries name files and functions instead. Dates are when the decision was
made. "Measured" means a number we actually observed; "estimate" means reasoning only.

---

## Contents

1. [The product in one paragraph](#1-the-product-in-one-paragraph)
2. [Stack and shape](#2-stack-and-shape)
3. [Money correctness](#3-money-correctness)
4. [Budgets](#4-budgets)
5. [Analytics, Year in Review and Wrapped](#5-analytics-year-in-review-and-wrapped)
6. [The Insights Engine](#6-the-insights-engine)
7. [Holdings and privacy](#7-holdings-and-privacy)
8. [Performance](#8-performance)
9. [Frontend and UX](#9-frontend-and-ux)
10. [Infrastructure and deployment](#10-infrastructure-and-deployment)
11. [Testing](#11-testing)
12. [Incidents and lessons](#12-incidents-and-lessons)
13. [Open questions and future work](#13-open-questions-and-future-work)
14. [Changelog](#changelog)
15. [Entry template](#entry-template)

---

## 1. The product in one paragraph

A personal finance tracker for people in India: expenses, income, transfers, budgets, loans, goals, investments
(mutual funds and others), insurance, net worth, analytics and a calm "insights" layer. No SMS reading and no bank
login: everything is entered by the person, so correctness, trust and privacy are the product. It is a solo-developer
project running on a small server, which shapes almost every trade-off below (cheap to run, simple to operate, few moving
parts).

## 2. Stack and shape

| Choice | Why | What we gave up |
|---|---|---|
| **Django (server-rendered templates), htmx for partial updates, vanilla JS for widgets** | One codebase, fast first paint, SEO for public pages, no build pipeline or API versioning, easy for one person to keep consistent. | A rich client-side app feel. Some interactions (instant tile filters, charts) need hand-written JS that is not shared with a component framework. |
| **Managed Postgres** (SQLite locally and in tests) | No database to operate. | We cannot run Postgres locally in tests, so Postgres-only behaviour (UNION column typing, locking) is checked by reasoning and by compiling SQL for the Postgres backend, not by executing it. See [8.3](#83-combine-per-table-aggregates-into-one-query). |
| **Bootstrap (purged build) + Bootstrap Icons, self-hosted** | Familiar components and consistent look; purged CSS is about half the size. | A generated file (`bootstrap.purged.min.css`) that a test keeps in sync; new classes need `purge_css`. |
| **English, Hindi, Marathi** via Django i18n | The audience. | Every new string needs two translations. We edit `.po` files by appending text, never by re-saving them with a tool that re-wraps the whole file (huge diffs). |

## 3. Money correctness

### 3.1 Double-entry ledger, introduced as a shadow
- **Decision.** Every money movement also posts balanced journal entries (`ledger_service.py`). Balances are still kept
  on `Account` for speed and simplicity; the ledger was added in shadow first (`LEDGER_WRITE_ENABLED`), compared against
  the old numbers (`LEDGER_READ_COMPARE_*`), then reads were cut over by cohort percentage (`LEDGER_READ_COHORT_PERCENT`).
- **Why.** An audit trail that cannot silently drift, and a single place to answer "why is this balance this number".
- **Trade-off.** Two sources of truth during and after migration (account balance column and ledger) and extra queries
  on every write (about 14 statements per posting, 6 of them savepoint round trips). The extra cost is real: see
  [8.7](#87-the-write-path-is-heavier-than-it-looks).
- **Rejected.** Replacing balances with a pure ledger sum in one step (too risky for live data), and skipping the ledger
  (no audit trail).
- **Revisit when.** The shadow comparison has been clean for a long time and the balance column can become a cache.

### 3.2 Store the amount in the base currency on every row
- **Decision.** Every transaction stores `amount`, `currency`, `exchange_rate` and `base_amount` (in the profile
  currency) at the time of entry. Totals sum `base_amount`.
- **Why.** Reports must not change when today's exchange rate changes, and sums stay a single indexed `SUM`.
- **Trade-off.** Changing the profile currency later does not retroactively convert history; edits can re-derive the rate.
- **Related.** Editing or deleting a foreign-currency transaction returns *exactly what the ledger recorded*
  (`_undo_amount` reads it back), not today's rate, so balances do not drift.

### 3.3 One definition of "savings"
- **Decision.** `expenses/savings.py` is the only place that defines savings and savings rate: income minus expenses,
  minus loan interest, minus capital events counted in averages. Loan *principal* is not spending (it repays debt).
  Cashback and refunds count as income but are left out of the rate's denominator.
- **Why.** Several screens used to compute it differently and disagreed. A test asserts every screen lands on the same
  rate.
- **Trade-off.** Every screen calls into this module, even when a bespoke query would be a little cheaper.

### 3.4 Idempotent saves
- **Decision.** The New Expense form sends a client key (`client_dedup_key`, unique per user); replaying it returns the
  original expense. A concurrent double submit is resolved by catching the integrity error.
- **Why.** Phones double-tap and retry on flaky networks; a duplicated expense is a trust-breaker.

### 3.5 Capital events are a separate table, not a flag on expenses
- **Decision.** One-off large events (down payment, big medical bill) live in `CapitalEvent`, excluded from averages and
  budgets by default but included in cash flow and net worth.
- **Why.** Keeps analytics and budget queries free of "except where flagged" branching, keeps distinct fields (subtype,
  linked loan) off `Expense`, and does not touch plan limits on expense counts.
- **Trade-off.** Converting between an expense and a capital event is a data copy, and every report that should include
  both must union them.
- **2026-10: the New Expense form offers it.** From ₹5,000 (a per-currency threshold) the review step shows a hint with
  a link that opens the Capital Event form with amount, currency, date, account, note and category already filled in.
  It opens a form rather than saving directly, so the person reviews the flags. The older "is that right?" prompt only
  started at ₹1,00,000 and its link carried only the amount; it now carries everything.

### 3.6 Plan limits lock rather than delete
- Accounts or categories beyond a plan's limit are *locked* (visible, not editable, never counted as stale), not
  deleted. Downgrading never destroys data. The cost is that many views must filter on the plan's allowed set.

## 4. Budgets

### 4.1 Versioned limits instead of a mutable column
- **Decision.** A budget limit is a `BudgetLimitVersion` row ("from this month onward") plus optional
  `BudgetMonthOverride` rows ("this month only"), resolved in `budget_resolver.py`. The old `Category.limit` column is a
  legacy mirror kept in step by `sync_legacy_limit`.
- **Why.** Changing a limit must not rewrite history: last month's "over budget" has to stay true.
- **Trade-off.** More tables and a resolver; every old writer (forms, admin, demo data) has to go through the mirror.
- **Lesson.** A limit written through the legacy column lands on the budget month of the *real* "today". Several tests
  assumed a calendar month and started failing on the 25th. See [11.4](#114-tests-must-not-depend-on-the-day-they-run).

### 4.2 Salary-cycle budget months
- **Decision.** For a salary day of 2 to 15 the cycle that *starts* in a month is that month's budget month; for 16 to
  31 the cycle that *ends* in the month is. Months shorter than the salary day use their last day
  (`expenses/budget_cycle.py`, pure date arithmetic).
- **Why.** Exactly one cycle belongs to each month and "this month" is the one a person would call the current month.
- **Trade-off.** On and after the 25th with a salary day of 25, "this month" is next month. Correct, but surprising to
  anyone who assumes calendar months.
- **Rejected.** A separate "cycle view" toggle (two truths for one number).

### 4.3 Budget alerts and the dashboard use the same range
- All of Budget page, alerts and Dashboard resolve their dates through the same helpers, so the three never disagree
  about which days a month covers.

## 5. Analytics, Year in Review and Wrapped

### 5.1 Facts-based analytics with versioned caches
- Analytics builds a compact set of facts per month and renders from them, with caches versioned per user
  (`analytics_version_{user}`), so any write invalidates by bumping the version instead of hunting keys.

### 5.2 Wrapped: spending story is shareable, money story is opt-in
- **Decision.** Year in Review ("Wrapped") is split: the *Spending story* (what was bought) is safe to share; the
  *Money story* (savings rate, net worth) is income-based and is shared only when the person chooses. Images are made in
  the browser (html2canvas, loaded only on share) at 1080x1920, and nothing is uploaded.
- **Why.** People will post these. A leaked salary or net worth is the failure mode to design against.
- **Trade-off.** Client-side rendering is slower and font handling is fiddly (letter-spacing split words in the export),
  but no image ever touches the server. Cards scale with container-query units so one design fits phone and desktop.

## 6. The Insights Engine

### 6.1 Rules produce stored insights; surfaces only format
- **Decision.** Rules (`expenses/insights/rules`) run against an `InsightContext`, results are stored as `InsightState`
  rows, and every surface (dashboard card, page strips, bell, push, weekly email, monthly report, Year in Review) reads
  the stored rows through adapters.
- **Why.** Pages stay fast (they read, they do not compute), every surface says the same thing, and dismiss/snooze/
  feedback work everywhere.
- **Trade-off.** Insights can be a few minutes old. A refresh runs after a write (marked stale in a cache write, so a save
  costs no extra query) and on page load in a background thread; the page shows what is stored.

### 6.2 Descriptive, never advice; compare only with yourself
- Tone rules are part of the design (`docs/insights-tone.md`): calm sentences, one optional next step, no predictions
  about markets, no comparison with other people, lead with what is going well when it is true.
- **Why.** A money app that lectures loses trust, and advice has regulatory weight in India.

### 6.3 Rollout by mode and by person
- `INSIGHTS_ENGINE_MODE` is `off`, `shadow` or `on`; `INSIGHTS_ENGINE_USER_IDS` forces it on for named users. Shadow mode
  runs the rules next to the legacy code and compares. The legacy dashboard insights stay for people outside the rollout.
- **Cost.** Two code paths for a while (including the legacy "net worth milestone" insight, which is why Net Worth is
  still computed inline for engine-off users, see [8.5](#85-net-worth-and-allocation-load-after-the-page-paints)).

### 6.4 Delivery caps
- Bell: at most one new insight a day; push: at most two a week, never 9pm to 8am, only for time-sensitive things;
  weekly email is Pro. Insights about debt or a shortfall are never emailed.

### 6.5 UI conventions (settled after iteration)
- Strips go under the title and toolbar and above the list; compact lines; quiet controls; no overlays inside scrolling
  containers; the dashboard card never grows its row (it takes the metrics' height and scrolls inside).
- 2026-10: the dashboard card got its own type hierarchy (semibold title 15px, sentence 13.4px, tone-coloured icon,
  dividers only *between* items). The strips are untouched. A selector bug (`:first-child` when a `<style>` tag was the
  first child) drew a divider above the first item; use `:first-of-type` when a style tag precedes the content.

## 7. Holdings and privacy

### 7.1 Track funds without sending anything to a third party
- **Decision.** Holdings are entered by the person. The only thing that leaves the server is a fund's public scheme code,
  to fetch its NAV (AMFI feed, with `mfapi.in`). No broker login, no statement upload, no aggregator.
- **Why.** It is the product's main differentiator against apps that ask for a broker login or inbox access.
- **Trade-off.** More manual entry; no automatic import.

### 7.2 Valuation order
Live NAV, then cached NAV, then the person's own price (stocks, gold have no feed), then cost basis. The value of a
NAV-priced holding is always `units x latest NAV`, so correcting units changes the value immediately.

### 7.3 Net worth does not double count a SIP
Transferring cash to an investment account only moves cash; logging the holding must not add the same money twice.
Account value is `sum(holding values) + max(0, ledger balance - cost basis)` ("uninvested cash"). Selling puts proceeds
in as cash and records the gain, so a sold holding no longer brings its cost back as cash.
- **Known limit.** The `max(0, ...)` floor hides a shortfall when holdings exceed the ledger cash; a reconciliation hint
  is planned.

### 7.4 Allocate a transfer to funds
The transfer form can allocate part of a transfer into one or more funds (optional; the rest stays as uninvested cash),
and a subscription of type Transfer can split across funds. Fund rows are built by JavaScript after page load, so each
new row has to be passed through `initSearchableSelects` to get the searchable select on desktop (phones use their native
picker, which hid the bug).

## 8. Performance

How we found out: a `Server-Timing` middleware (`finance_tracker/server_timing.py`, off unless `ENABLE_SERVER_TIMING`) reports total time, database time and query count per request, with no SQL or user data, and an optional slow-query log names the file and line. Two facts drove everything below: a database round trip costs real time, so **query count dominates a cold page**; and the CPU is small, so avoid per-request Python work you can cache. Numbers in this section are query counts on a demo account unless stated.

### 8.1 Cache the whole dashboard context
- **Decision.** The default dashboard view caches its entire context for four hours per user (`home_default_data_{id}`);
  any write invalidates it by signal (`invalidate_dashboard_cache`). A warm load is 3 queries.
- **Trade-off.** A cold build is large (see 8.3) and happens after every write, deploy and worker recycle. Invalidation
  must be complete or numbers go stale; a set of tests guards it.

### 8.3 Combine per-table aggregates into one query
- **Decision.** Income, expenses, loan repayments and capital events were summed in about six separate places. Each
  group is now one `UNION ALL` (`period_totals`, `monthly_parts`, `totals_by_period` in `expenses/savings.py`); the
  category chart, payment split and totals share one grouped query; the income-vs-expense trend reuses the Total Spent
  trend. **Measured:** cold dashboard 84 to 66 queries on demo data, rendered HTML byte-identical across eight filter
  variants (only CSRF tokens differ).
- **Trade-off.** UNION branches must have matching column types; Postgres is stricter than SQLite. We follow the pattern
  already used by the budget resolver (typed constants), compile the SQL for the Postgres backend without connecting,
  and have not executed it against Postgres locally. Result rows come back tagged with a "kind" column, which is less
  readable than four named queries.
- **Rejected.** Running the queries on parallel threads (needs one connection per thread, breaks transactional tests),
  and a materialised summary table (invalidation burden for a solo project).

### 8.4 Why not "just move cards to fragments" everywhere
- We first proposed moving all heavy cards to htmx fragments and estimated about 3 queries on a cold load. **That was
  wrong:** `home_view` is one 2,600-line function and nearly every card is built from the same shared variables, and the
  4,000-line template starts charts and filters from `DOMContentLoaded`, which would not re-run for swapped content.
  Splitting everything means recomputing the shared part per fragment (about 40 queries each) or caching and passing
  intermediate state. We reported the correction and chose a staged plan instead.
- **Rejected.** Rendering a skeleton shell and loading the whole body after paint (improves first paint, not time to
  content, and every script that expects the elements at load would need re-initialising).
- **Rejected.** Deferring the collapsed "Financial Intelligence" hub: it saves only about 10 queries and would make
  opening it feel slow because it would recompute nearly everything.

### 8.5 Net Worth and allocation load after the page paints
- **Decision.** With the Insights Engine on, the page no longer reads the ledger. Net Worth and Asset Allocation show a
  skeleton and fetch `/dashboard/networth/` (18 queries, rendered without context processors). Figures are cached four
  hours and retired by the same signal; when cached the page includes them directly, so a warm load makes **no extra
  request**. **Measured (demo data):** cold build 66 to 53 queries.
- **Trade-off.** Engine-off users still compute net worth inline because the legacy "net worth milestone" insight needs
  it. A second code path, and a cache key that must be invalidated with the rest.

### 8.6 Rebuild the dashboard in the background after a write
- **Decision.** After an expense is added, edited, deleted or undone, `schedule_dashboard_warm` rebuilds the dashboard
  (and the Net Worth card) on a short-lived thread once the save has committed (`transaction.on_commit`). A page asked
  for while the rebuild is running waits up to 2.5 seconds for it instead of repeating the work. **Measured locally:**
  landing after a save is 3 queries with Net Worth inline, versus 48 queries without the rebuild.
- **Safety by construction.** Every write bumps a per-user version; a build is kept only if the version did not move
  while it ran (this also fixed a latent race for ordinary requests). The rebuild is skipped, never run inside the
  request, when all background threads are busy. It is off under test.
- **Bug worth telling.** The rebuild thread first waited on its *own* "rebuild in progress" flag and stalled for the full
  2.5 seconds. Found with `faulthandler.dump_traceback_later`; fixed by marking the synthetic request as the rebuild.
- **Trade-off.** More database load per save (about 60 queries on a thread), and correctness depends on the version
  discipline.

### 8.7 The write path is heavier than it looks
- **Measured (local):** adding an expense was 34 queries, editing 47. Cheap wins taken (add 34 to 27, edit 47 to 43):
  keyword learning moved after the response (`expenses/background.py`), the form loads accounts once instead of up to
  three times and shares the category list with the composer, the edit query joins the user and profile, and the
  expense list no longer processes recurring transactions twice.
- **Not done (option C).** Edit is still 43 queries, mostly the ledger reversing the old entry and posting a new one,
  with nested savepoints (about 6 to 9 extra round trips on Postgres). Dropping savepoints touches every transaction
  type; it needs careful tests.
- **Background jobs.** `run_after_response` runs a job on a daemon thread with its own connection (closed after), at most
  four at a time; if all are busy it either runs inline (nothing is dropped) or skips (jobs that only save time).

### 8.8 Landing page and static assets
- Self-hosted fonts and vendor files, content-hashed static files (WhiteNoise manifest storage), no third-party request
  before first paint on public pages, the anonymous landing page may be cached briefly by a CDN (everyone else gets a private copy).

---

## 9. Frontend and UX

### 9.1 Reuse the app's own components
Forms, buttons and modals use the existing Bootstrap classes and `btn-action-*` variants; never bespoke controls. New
surfaces are checked at phone width and in the browser before being reported done.

### 9.2 One toast style
- **Decision.** Django messages, the "Added ... Undo / Edit" toast and the Budget toast share `static/css/toasts.css`: a
  frosted-glass pill with a tinted icon, message, optional outlined action and close button, in success / danger /
  warning / info tones, light and dark. Phones: above the bottom bar, centred. Desktop (992px and up): top right under the
  header. One toast region holds them all, so they stack instead of overlapping, with one corner radius (28px).
- **Why top right on desktop.** Users could not find toasts at the bottom of a large screen. The earlier all-bottom
  version looked fine on a phone and was easy to miss on desktop.
- **Detail.** Bootstrap's `.toast:not(.show)` has the same specificity as our display rule, so the show/hide states are
  restated explicitly. The browser pane's screenshots do not show `backdrop-filter` blur; check on a real device.

### 9.3 Searchable selects
`app_base.js` upgrades `select.form-select` into a searchable widget on load and after htmx swaps. **Anything inserted by
your own JS after load must call `window.initSearchableSelects(row)`**, or it stays a native select on desktop.

### 9.4 Account list: columns follow the group in view
- **Decision.** The list is one grid that the group tiles filter in the browser (instant, no request). Extra cells for
  every group are in the HTML; CSS keyed on `data-view-type` on the shell shows Policy number for Insurance, Rate and
  Matures for Fixed income, Limit / Used / Next bill for cards, and nothing extra elsewhere.
- **Why CSS rather than re-rendering.** The tiles already switch the list client-side; a request per switch would be a
  regression.
- **Trade-off.** Extra markup per row. Wide columns only appear from 1,200px; narrower screens keep the details under the
  name.
- **Tab counts.** "Pinned" and "Needs update" counted every account, so "Needs update 10" sat beside two accounts. They
  now count what a click would show for the chosen group and search, computed on the server for the first paint and
  recomputed in the browser as the view changes.

### 9.5 The New Expense form
Quick-add line with parsing, "your usual" chips, a review state with tags (Auto, Last used, Check), and a large-amount
prompt. A learned keyword hint ("swiggy" to Food, account, payment method) makes the next entry faster; it is saved after
the response because nothing waits for it.

---

## 10. Infrastructure and deployment

_Kept in private notes (not in the public repository)._

---

## 11. Testing

### 11.1 Parallel runner and an offline suite
`finance_tracker/test_runner.py`: a custom runner pickles exceptions, tracebacks and frames across worker processes,
stubs the public exchange-rate APIs so the suite needs no internet, and gives each spawned worker the same stub (on macOS
workers are spawned fresh and never run the runner's setup). Run with `manage.py test --parallel` (about 70 to 90 seconds
for about 4,400 tests).

### 11.2 Query-count ceilings as tests
`test_dashboard_query_budget` fails if a cold dashboard exceeds its ceiling or a warm one exceeds 4 queries; the ceilings
are lowered consciously when a change reduces them. Other tests assert constant query counts for the combined aggregates.

### 11.3 Known false failures
Mixing `expenses.tests.insights` with `expenses.tests.test_views` in one parallel invocation can fail two money-text tests
(language leakage between tests in a worker). Run the full suite or each package alone.

### 11.4 Tests must not depend on the day they run
Four tests failed on the 25th: a salary day of 25 makes the 25th the first day of next month's budget, and a snooze test
counted from the real clock. Fixes: hold the clock still (`mock.patch('django.utils.timezone.localdate')`), compute the
expectation from the same helper the app uses, or make the fixture outlive any real date. **Rule:** if a test hard-codes a
date, it must also control "today".

### 11.5 Workers that crash at exit
Parallel test workers produced macOS "Python quit unexpectedly" reports: a segmentation fault in the interpreter's final
garbage-collection pass when a worker exits. Older reports from days before the threaded work show the same trace, so it
predates the background-thread changes. The runner now ends workers with `os._exit(0)` after flushing, since a worker has
nothing left to clean up once its results are sent. Verified: a full run of about 4,400 tests with 12 workers produced
no new crash reports (25 before, 25 after).

### 11.6 Never run two parallel suites at once
Django's parallel runner on SQLite clones the test database into `default_N.sqlite3` files in the working directory.
Two overlapping runs (or deleting those files during a run) make workers fail with "unable to open database file"; the
pool keeps respawning them, so the run looks hung at low CPU and floods macOS with crash reports. If a run is stuck, kill
it (`pkill -f "manage.py test"`), delete `default_*.sqlite3`, and start one run. There is no `timeout` command on macOS.

## 12. Incidents and lessons

_Kept in private notes (not in the public repository)._

---

## 13. Open questions and future work

- **Option C: lighter ledger posting.** Drop nested savepoints where they are not needed; about 150 ms per write on
  Postgres. Needs per-transaction-type tests.
- **Holdings reconciliation hint** where holdings exceed ledger cash (the `max(0, ...)` floor hides it).
- **Insights deferred review items:** legacy dashboard insight code when the engine is on, goal-pace and small-change
  rules, currency-scaled thresholds, regularity check in recurring detection, overlapping rules, cron batch running in the
  web thread, an index on `valid_until`.
- **Ledger cohort:** retire the balance column as a source of truth once the shadow comparison has been clean long enough.

---

## Changelog

Newest first. One line per change with the area and the commit subject; add a full entry above when there is a real
trade-off.

| Date | Area | Change |
|---|---|---|
| 2026-10-26 | Tests | Parallel test workers exit without Python's final garbage-collection pass (no more crash reports); doc: never run two suites at once. |
| 2026-10-26 | Accounts | List shows the columns of the group in view; tab counts follow group and search. |
| 2026-10-26 | Forms | Fund pickers under "Allocate to funds" are searchable selects on desktop. |
| 2026-10-26 | Insights UI | Smaller type on the dashboard Insights card; no divider before the first or after the last item. |
| 2026-10-25 | Performance | Dashboard rebuilt in the background after an expense is added, edited, deleted or undone. |
| 2026-10-25 | Performance | Add and edit with fewer queries; keyword learning runs after the response. |
| 2026-10-25 | Tests | Four day-dependent tests fixed; query-budget ceiling lowered. |
| 2026-10-24 | Expenses | New Expense form offers "Log as Capital Event" from 5,000. |
| 2026-10-24 | UI | One glass-pill toast style on phone and desktop; top right on desktop. |
| 2026-10-23 | Performance | Net Worth and Asset Allocation load after the page paints (cold build 66 to 53 queries). |
| 2026-10-23 | Performance | Per-table aggregates fetched together with `UNION ALL` (cold dashboard 84 to 66 queries). |
| 2026-10 | Landing | PageSpeed pass: self-hosted assets and fonts, consent-gated analytics, CDN cache rule. |
| 2026-10 | Insights | Insights Engine reviewed, optimised and marketed (landing, features, pricing, docs). |
| 2026-10 | Budgets | Salary-day (cycle) budget months. |
| 2026-10 | Wrapped | Redesign with a privacy-aware share sheet. |

---

## Entry template

```markdown
### N.N Short title of the decision
- **Date / area.** 2026-MM-DD, area.
- **Decision.** What we chose, in one or two sentences, and where it lives (files, functions, settings).
- **Why.** The problem and the evidence (measured numbers, a bug, a user complaint).
- **Alternatives rejected.** And why.
- **Trade-off.** What this costs us, what it makes harder.
- **Revisit when.** The condition that should reopen the decision.
- **Verification.** How we know it works (tests, measurement) and what we did *not* verify.
```
