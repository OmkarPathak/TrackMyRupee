# TrackMyRupee Insights Engine: Stage 1 audit and plan

Status: **for approval. No code has been changed.** This file is untracked and not committed.
Environment checked: `.env12` is Python 3.13.7. Tests run with Django's unittest runner (`manage.py test --parallel`), not pytest, so "django_assert_num_queries" becomes `assertNumQueries` / `CaptureQueriesContext` (the pattern already used in `test_dashboard_query_budget.py`).

Contents: 1 Headline findings · 2 Inventory of what exists · 3 Architecture · 4 Migration plan · 5 Rule catalogue · 6 Decisions for you · 7 Test plan · 8 Rollout plan · 9 What I did not verify

---

## 1. Headline findings (read these first)

1. **There is no engine today.** Insight logic lives in five places: one 2,600-line `home_view` (`views/dashboard.py`, insights ~L1094-2130, nudges ~L2564), `analytics/*`, `send_notifications.py`, `views/goals.py`, `views/accounts.py`. Roughly 40 distinct messages, none pluralised (`ngettext` is not used anywhere in dashboard.py).
2. **The dashboard does not use `analytics/facts.py`.** It hand-rolls `monthly_summary_map` (floats, calendar months) next to the good definitions in `savings.py`. The engine will read only `facts.py` / `savings.py`, so some migrated numbers will differ from today's for some users. See section 1a. You asked me to stop and tell you rather than fix silently, so this is decision D1.
3. **Two legitimate definitions of "spend" exist and rules must name which one they use.**
   - *Spending* (savings.py / facts.py): expenses + loan interest + capital events with `exclude_from_averages=False` (plus the Analytics switch).
   - *Budget-counted spend* (budgets.py): capital events with `exclude_from_budget=False`, loan interest as a "Loan Interest" category.
   Budget rules read the budget payload. Cash-flow rules read facts. A rule may not mix them.
4. **Nothing in the repo schedules any job.** Jobs run only when an external caller (cron-job.org per code comments) POSTs to `/api/cron/...` with `CRON_SECRET`. The cadence is not in the repo, so I cannot see or change it. The cron lock uses Django cache; production cache is LocMem per process unless `REDIS_URL` is set (`settings.py:296-330`), so that lock is not global across gunicorn workers. Consequence for the design: **the database is the source of truth, caches are only a micro-optimisation**, and generation must be idempotent.
5. **Existing notifications are English-only and baked in.** `send_notifications` is `@no_translations`; `Notification.title/message` store rendered English. Hindi and Marathi users get English bell items, push and emails. The engine must render in `profile.language` at generation time (`translation.override`).
6. **There is no email opt-out of any kind** (monthly report goes to every verified user; the digest is tier-gated only; `daily_reminder` is the only preference). A daily or weekly insight digest needs a settings flag and a one-click unsubscribe before it ships.
7. **Plan facts** (`plans.py`): Free has 0 loans, 2 accounts, 2 recurring, 5 budget categories, 1 goal, `email_notifications=False`. Plus has `email_notifications=True`, 1 loan. `ai_insights` and `has_ai_access` are True on every tier (they gate the Analytics page; the name is historical, nothing uses AI). I propose **no change to plans.py**.
8. **Security issue found in existing code, not mine to fix silently:** capital-event notes (`ev.note`) go through `.format()`, `mark_safe` and `|safe` on the dashboard bullet card, so HTML in a note is rendered (stored XSS, user-to-self). Recommend a small separate fix commit before Phase A (D13).
9. **Soft delete is off.** `SOFT_DELETE_ENABLED` is never set, so rows are hard-deleted and the `is_deleted` gap between dashboard and `facts.py` is dormant. The engine will use `facts.py` (which filters capital events by `is_deleted=False`). No action needed.
10. **Privacy hazard to design around:** Sentry's default captures local variables in stack frames, and exception messages can contain values (`KeyError('Dining')`). The engine's failure isolation must log only the rule id and the exception class name, never `exc_info` or the message.

### 1a. Existing dashboard logic that disagrees with the app's own definitions (reported by the audit; items marked "to verify" I have not run)

| # | What | Why it matters | Effect on migration |
|---|---|---|---|
| a | Traffic Alert compares a **salary-cycle** total with an average of **calendar** months; Speed Alert uses `now.day` (calendar) with cycle limits | Wrong for salary-cycle users | Engine uses cycle-aware numbers; calendar users see identical numbers |
| b | For salary-cycle users `prev_year/prev_month` stay `None`, so the previous-month category query filters `date__year=None` and returns nothing (to verify) | No spike/drop/"top savers" data for them | Engine compares cycle with cycle |
| c | "vs 3-month average" (`viral_insight`) uses Expense only (no loan interest or capital events), a `-100 day` window, calendar months | Differs from the savings definition | Engine uses facts, 3 prior cycles |
| d | Net-worth forecast `avg_monthly_savings` omits counted capital events and always divides by 3; the same variable name is reused for the YTD projection | Not in Phase A scope, noted | Not migrated, flagged |
| e | Three different "this year" projections can show together (Wealth Projection YTD average, "High five" `savings*12`, Monthly Story suffix) | Contradictory numbers | One rule (`cashflow.year_pace`), the others retire (D2) |
| f | Streak uses `income > expense` from the 5-minute cached float map | Same maths as savings>0, separate implementation | Engine uses `Summary.saved` per cycle |
| g | Monthly Story uses total income as denominator, not `income - cashback/refund` | Different "rate" basis | Widget kept as is, noted |
| h | Near-limit threshold is 85% (budget page, alerts) but 90% in the Spending Pace card | Inconsistent | Engine uses `BUDGET_WARNING_PERCENT=85`; the pace card is untouched |
| i | Cached text is baked in the language at cache time; 4-hour dashboard cache | A language switch shows stale-language insights | Engine panel is not part of that cache |
| j | `date.today()` vs `timezone.localdate()` mixed (Upcoming Payment) | Midnight drift | Engine uses `localdate()` |

---

## 2. Inventory of every existing insight, alert and nudge

Legend. Where: file:line. Trans = text goes through gettext (E = English only). Surface: D dashboard alerts card, S dashboard smart-insights card, B bell, P push, M email, W in-page widget. Disposition: **A** = migrate in Phase A, **B-E** = migrates in that phase, **Keep** = metric widget, not an insight, **Retire?** = recommend removing (needs your approval, D2).

### Dashboard (`views/dashboard.py`)

| # | Insight | Trigger | Text (abridged) | Surface | Trans | Disposition |
|---|---|---|---|---|---|---|
| 1 | Traffic Alert L1153 | current cycle view, projected > 1.25 × avg of up to 3 prior calendar months, spend > 1000 (hard-coded) | "You're pacing X% higher than usual. Slow down to stay on track!" | D | gettext, `%%` | **A** `cashflow.pace_vs_usual` |
| 2 | Fixed-Income Maturing L1192 | active deposit maturing within 30 days | "{name} matures on {date} — {amount} will need a home." | D | gettext | **A** `accounts.deposit_maturing` |
| 3 | Spending Speed Alert L1208 | category projected > 100% of limit, used <= 100% | "Trends show you might overspend on {cat} soon. Take care!" (1 / 2 / n variants) | D | gettext, no ngettext | **A** `budgets.category_runout` |
| 4 | Budget Breached L1497 | category used > 100% | "Even strong months have leaks. {cat} exceeded by {amt}." / "… let's rebalance to stay safe." | D | gettext (curly apostrophe) | **A** `budgets.limit_passed` |
| 5 | Approaching Limit L1536 | status 'limit' (>= 85%) | "Heads up! You're close to overspending on {cats}." | D | gettext | **A** `budgets.limit_nearing` |
| 6 | Upcoming Payment L1714 | active recurring due in 7 days, top 3 | "Your recurring payment for {x} is due {when}." | D | gettext | **C** `recurring.bills_soon` (stays legacy in A) |
| 7 | Super Saver Status L1572 | rate >= 20% (almost never shown) | "You've saved X% of your income this month." | S | gettext, emoji | **Retire?** (covered by 8, 10) |
| 8 | Momentum Building L1595 | savings up vs previous period | "Your savings grew by X vs last month." | S | gettext, emoji | **A** `cashflow.savings_momentum` |
| 9 | You're in Control L1606 | only when no other insight, spend down > 5% | "You've cut spending by X%." | S | gettext, emoji | **Retire?** |
| 10 | On a Roll (streak) L1628 | >1 consecutive months income > expense | "This is your {ordinal} month in a row staying under budget." | S | gettext; English ordinals hard-coded | **A** `cashflow.saving_streak` |
| 11 | Wealth Projection L1663 | YTD savings > 0 | "If you maintain this pace… you could accumulate {proj} this year." | S | gettext; "L/k" suffix, non-INR too | **A** `cashflow.year_pace` |
| 12 | In the Green / Fresh Start L1687 | fallbacks | "You've saved {raw Decimal} so far. Keep it up!" | S | gettext | **Retire?** (replaced by empty state) |
| 13 | Trend Alert / viral L1243 | top category > 1.1 × its 3-month average | "You spent X% more on {cat} than your 3-month average." | S | gettext; `_(user category)` | **A** `expenses.category_above_usual` |
| 14 | Fun Fact L1321 | top category ÷ price of Netflix/Starbucks/Gym | "…equivalent to N Netflix subscriptions." | S | gettext; INR prices for all currencies; `random.choice` | **Retire?** (non-deterministic, trivialising) |
| 15 | Bullets: Capital Event callout L1892 | capital events in period | "Capital Event: {note} this month" (HTML, note unescaped) | S | gettext, `|safe` | **D** `capital_events.rate_effect` |
| 16 | Bullet: Maturing L1922 | same as #2, any view | duplicate of #2 | S | gettext | merged into #2 |
| 17 | Bullet: Power Insight L1934 | top non-"Rent" category | "…Cut 20% to save {x}." | S | gettext; English "Rent" literal | **Retire?** (prescriptive, conflicts with tone) |
| 18 | Bullets: top category L1960, breach L1973 | | "{cat} is your top expense this month." | S | gettext | merged (top category dropped; breach merged into #4) |
| 19 | Bullets: MoM spikes/drops L1988 | category ±10% vs previous calendar month, not for salary-cycle users | "Heads up! Spending is up in…" / "Look at you!…" | S | gettext | **B** `expenses.category_change` |
| 20 | Bullet: Net-worth milestone L2080 | net worth >= 100k…10M (state, not event: shows forever) | "You crossed X in net worth! … Most people never get here." | S | gettext; peer comparison | **Retire?** (peer framing, repeats) |
| 21 | Bullet: High five L2101 | savings > 1.2 × previous | "…you could save X this year!" | S | gettext | **Retire?** (second projection) |
| 22 | Monthly Story L1746 | always | "In {month}, you earned… and saved… (X%)" | W | gettext; month name untranslated; not ngettext | **Keep** (narrative widget, noted g) |
| 23 | Spending Pace card L1348 | budget pace on_track / near_limit (90%) / over | chips | W | `{% trans %}` | **Keep** (metric, not insight) |
| 24 | Impulse vs Planned L1398 | description match to recurring | chips; swallowed `except Exception: pass` | W | partly | **Keep** |
| 25 | Hero `short_insight`, `trend_text`, `power_insight` | computed, **not rendered** | | none | | **Retire** dead code |
| 26 | Nudge: Multiple Accounts L2568 | exactly 1 account | "Add separate accounts…" | B | **E** | **B** `accounts.add_more_accounts`? (optional, D17) |
| 27 | Nudge: Organize Your Spend | >= 3 expenses in Misc/Other | "Categorizing helps you…" | B | **E** | optional |
| 28 | Nudge: Automate Repeat Bills? | same description+amount >= 3× in 90 days | "Looks like {desc} repeats monthly…" | B | **E** | **C** `recurring.looks_recurring` |

### Analytics (`analytics/*`, `views/analytics.py`)

| # | Insight | Trigger | Text | Trans | Disposition |
|---|---|---|---|---|---|
| 29 | `Page.insight()` common.py:153 | always | "You kept X of Y (Z%)." + "ahead of / behind the comparison period" | gettext | **E** `analytics.period_summary` (inline) |
| 30 | `takeaways()` cashflow.py:124 (max 3) | biggest cost; spent more than earned; costs exceeded income; invested/kept; category moved | 5 sentences | gettext | **E** inline rules `cashflow.*` |
| 31 | `health()` cashflow.py:153 | 4 numbers with good/warn/bad tone | "Savings rate…", "Emergency fund N months" | gettext | **Keep** (metric rows) |
| 32 | `rising_categories()` wealth.py:82 | 3 full calendar months rising; ignores toolbar filters | "Categories creeping up" | gettext | **E** `expenses.categories_creeping_up` |

### Budgets, accounts, goals, loans, notifications, reports

| # | Insight | Where | Trigger | Surface | Trans | Disposition |
|---|---|---|---|---|---|---|
| 33 | Budget "Smart move" banner | `budget_suggestions.py`, `budgets_api.py:200` | one category at/over limit, a donor < 10% for 3 months | page banner (HTMX fragment) | not checked | **B** `budgets.room_to_share` |
| 34 | Budget page pace text | `_budget_dashboard_content.html` | safe-to-spend, ahead/behind pace | W | `{% trans %}` | **Keep** |
| 35 | Accounts attention cards | `views/accounts.py:509` | bill within 10d, maturity within 180d, stale 30d | W | `{% trans %}` | **Keep** widget; rules C reuse extracted constants |
| 36 | Goal timing text | `views/goals.py` | "N months early / late" | W | gettext | **Keep** |
| 37 | Loan "Pay 1 Extra EMI/Year" card | `loan_detail.html:221`, `LoanService.calculate_extra_emi_savings` | per loan | W | `{% trans %}` | **C** rule reuses the function; card stays |
| 38 | Cron: recurring due in 3 days (+ insurance variant) | `send_notifications.py:234` | | B, P, M | **E** | stays; converts later (D3) |
| 39 | Cron: credit-card billing in 3 days | `:273` | | B, P, M | **E** | stays |
| 40 | Cron: Budget Exceeded / Budget Alert | `:291` | status over / 85% | B, P, M | **E** | stays |
| 41 | Cron: goal milestone 50/90/100 | `:321`, slug `milestone-{goal}-{pct}` | | B, P, M | **E** | shared helper extracted (D) |
| 42 | Cron: subscription expiring | `:349` | | B, P, M | **E** | not an insight; stays |
| 43 | Daily expense reminder | `send_daily_reminders.py` | no Expense today | P only | **E** | stays; engine respects it |
| 44 | Monthly report + "AI insight" sentence | `send_monthly_report.py:~203` | "Reducing this by 15%… could save you X!" | M | one `_()`; template English | **E** replaced by picks; subject text is the dedup key, do not change |
| 45 | Year in Review | `wrapped.py` | personas, thresholds | W | all `_()` | **E** lines section |
| 46 | Lifecycle drips, welcome, announcements | | | M | **E** | out of scope |

Not present anywhere (greenfield): loan rate or EMI-to-income text, credit-card utilisation, recurring price change, XIRR or allocation text, income-gap, late salary, SIP missed, low-balance lookahead, reconcile-age, goal stall or projection text outside the detail page, duplicate-entry detection, weekday pattern.

---

## 3. Architecture

### 3.1 File layout

```
expenses/insights/
  __init__.py          # public API: generate(), refresh(), STATE helpers
  types.py             # Insight, Evidence, Action, enums, validation
  registry.py          # @rule decorator, InsightRule, REGISTRY
  context.py           # InsightContext (lazy loaders), cycles
  engine.py            # generate(), refresh(), suppression, ranking, caps
  gating.py            # the ONLY place that reads plan tier
  format.py            # money/percent/date text (wraps analytics/money.py), plural helpers
  telemetry.py         # whitelisted events (rule_id, surface, action, count)
  prefs.py             # reads/writes UserProfile.insights_prefs
  rules/
    budgets.py expenses.py cashflow.py accounts.py loans.py recurring.py
    income.py goals.py holdings.py capital_events.py calendar.py networth.py analytics.py
  adapters/
    dashboard.py analytics.py module_strip.py bell.py push.py digest.py monthly.py wrapped.py
  views.py urls.py     # panel fragment, dismiss, snooze, helpful, go (acted redirect)
  README.md            # "add a rule in under 30 minutes"
expenses/management/commands/generate_insights.py  deliver_insights.py  insights_shadow_report.py
expenses/tests/insights/ ...
templates/expenses/partials/_insight_cards.html  _insight_strip.html  _insight_skeleton.html
docs/insights-tone.md        # tone rules + five "say this, not that"
guide-src/18-insights/index.md (+ .hi.md .mr.md, the budgets page has all three)
```

One app, one place (`expenses`) as the rest of the repo does. Model code goes at the end of `expenses/models.py` following `BudgetSuggestionDismissal` and `Notification`.

### 3.2 Data classes (immutable)

```python
@dataclass(frozen=True, slots=True)
class Evidence:   label: str; value: str; link: str | None = None     # value is pre-formatted

@dataclass(frozen=True, slots=True)
class Action:     label: str; url: str | None = None; hx_post: str | None = None

@dataclass(frozen=True, slots=True)
class Insight:
    rule_id: str; module: Module; kind: Kind; tone: Tone          # tone: calm | positive | gentle_attention
    priority: int                                                  # 0..100
    title: str; message: str; detail: str | None
    evidence: tuple[Evidence, ...]; action: Action | None
    dedupe_key: str                                                # "{subject}:{period}" e.g. "cat12:2026-10"
    valid_until: date
    surfaces: frozenset[Surface]; plan: Plan | None
    share_text: str | None
    sensitive: bool = False        # ADDED: debt, shortfall, red month
```

`__post_init__` validates: title <= 8 words, message <= 2 sentences, no `!`, no ALL CAPS word, evidence links are site-relative, `sensitive` implies `share_text is None` and no PUSH in `surfaces`. A broken rule fails its own tests and, at runtime, is dropped (failure isolation), never rendered.

Added field `sensitive` is the only addition to your list. Money amounts inside text are already formatted strings; no float ever enters an Insight.

### 3.3 Registry and rules

```python
@rule(id="budgets.limit_nearing", module=BUDGETS, surfaces={DASHBOARD, MODULE, BELL},
      min_plan=FREE, cooldown_days=7, needs={"budgets"}, min_history=Cycles(0),
      cost="light", mode="stored", push=True,
      preview=_("Budget limits are worth a look"),        # figure-free line for locked users
      sample=sample_limit_nearing)                          # synthetic context for tone/translation tests
def limit_nearing(ctx: InsightContext) -> list[Insight]: ...
```

- Rules are pure functions of `ctx`. They never import models or run querysets (a test greps `rules/` for `objects.` and fails).
- `sample` is **mandatory**: the registry refuses a rule without one. That one requirement lets a single generic test cover every rule for the tone linter, hi/mr translation and link resolution.
- `cost`: `light` (arithmetic over data the context already loaded, may run inline) or `heavy` (history scans; command only).
- `mode`: `stored` (persisted, dismissible) or `inline` (Analytics sentences that depend on the toolbar's period and filters; computed from the page's own `Facts`, never stored, no dismiss).
- `min_history`: cycles of data needed; below it the rule returns `[]` and the engine records "still learning" (3.8).

### 3.4 InsightContext (how rules get data)

Per-request/per-run object. Every loader is a `cached_property`, calls the existing function, and is declared by rules through `needs`. The engine pre-loads the union of `needs` for eligible rules only, so a user with no loans never loads loans.

| Loader | Backed by (existing) | Queries |
|---|---|---|
| `ctx.cycle`, `ctx.prev_cycle`, `ctx.cycles(n)` | `budget_cycle.budget_range / current_budget_month / salary_day_of` (pure date maths) | 0 |
| `ctx.facts` (history, N cycles) | `analytics.facts.load_facts(first_of_oldest_cycle, today)` once; `Summary` per cycle via `facts.summarize` | 5 |
| `ctx.facts_with_capital` | new `Facts.with_capital_events(flag)` sharing the same arrays (see below) | 0 |
| `ctx.budgets` | `budget_page.build_budget_page(user, month, today)` + `budget_resolver.effective_limits_range_by_name` for history | 3-4 |
| `ctx.inventory` | one query of `Exists/Count` for accounts, loans, goals, recurring, holdings, deposits (drives min-data gating) | 1 |
| `ctx.accounts` | `Account` rows + `LedgerReadService.get_account_balances` | 2 |
| `ctx.loans` | `annotate_loan_principal_totals(...).prefetch_related('interest_rates')`; `LoanService.current_rate / calculate_extra_emi_savings` | 2 |
| `ctx.goals` | `SavingsGoal` + contribution aggregates (as in `views/goals.py:137`) | 2 |
| `ctx.recurring` | active `RecurringTransaction` | 1 |
| `ctx.holdings` | public wrapper over `LedgerReadService._fetch_latest_holding_valuations` | 2 |
| `ctx.expense_rows` | bounded slice (90 days, id/date/category/base_amount/account/created_at/description) for duplicate and large-entry rules | 1 |
| `ctx.net_worth` | `analytics.networth.net_worth_points` (already cached 10 min) | 0-2 |

Context also exposes `user`, `today` (`timezone.localdate()`), `currency` (`Money(profile.currency)`), `tier` (`profile.active_tier`), `language`, `salary_day`, `prefs`.

In tests the context runs in **strict mode**: touching a loader the rule did not declare in `needs` raises, so query budgets cannot silently grow.

**Additions to the facts layer** (your rule: add numbers there, never in rules). All additive, behaviour of existing callers unchanged:
1. `Facts.with_capital_events(flag)`: a view sharing arrays. (`dataclasses.replace` would re-run `__post_init__` and wipe the arrays.)
2. `load_expense_rows(user, first, last)` slice loader (no aggregation).
3. `income_by_source(first, last)`: per-cycle totals by `source_type` for the steady-vs-variable rule.
4. `interest_by_loan(first, last)` for the interest-paid rule.
5. Extractions (moves, not rewrites, with their existing tests as the safety net): goal projection out of `views/goals.py` into a shared helper; attention constants and stale/bill logic out of `views/accounts.py`; the 50/90/100 milestone check out of `send_notifications.py`; a public holdings-valuation wrapper.
6. `find_suggestion()` gets a parameter to skip its own `BudgetSuggestionDismissal` lookup (engine owns dismissal), default unchanged.

### 3.5 Engine pipeline

`generate(user, surface, module=None, limit=None) -> list[Presented]` (read path, cheap) and `refresh(user, cost=...)` (write path, evaluates rules and upserts state).

Read path (what a page does): one query for `InsightState` rows for the user (valid, not dismissed, not snoozed) → drop by plan (`gating`), user prefs (module off, feature off, muted rules), `valid_until`, surface and module filter → rank → diversity → caps → mark shown. **1 query, plus 1 UPDATE only when something is shown for the first time that day.**

Ranking: `priority` then kind weight (upcoming > attention > opportunity > pattern > progress > milestone > observation; positive first when equal) then recency of `valid_until`. Diversity: no more than 1 per module in the top 3 on the dashboard, and never the same `(rule_id, dedupe_key)` on two surfaces in one view (the panel fragment is the only owner of "this view's shown set"; adapters ask the engine for the set once per page and pass it to others).

Write path: for each eligible rule (not suppressed by cooldown, min history met, data present per `inventory`), call `evaluate(ctx)` inside `try/except Exception`: on failure log only `rule_id` and `type(exc).__name__` at WARNING without `exc_info`, count it, continue. Upsert `InsightState` by `(user, rule_id, dedupe_key)`: preserves `first_shown`, dismissal and snooze; refreshes `payload`, `priority`, `valid_until`, `generated_at`.

Caps (constants in one place, `engine.CAPS`): dashboard 3, module page 2, analytics panel 3, bell 1 new per day, push 2 per rolling 7 days (time-sensitive only), email 1 per day and only with at least 1 item, plus "never two from one module in top 3". Quiet hours for push: fixed 21:00-08:00 IST (D15).

### 3.6 Persistence

**`InsightState`** (new): `uuid`, `user` FK, `rule_id` (64), `dedupe_key` (120), `first_shown`, `last_shown`, `times_shown`, `dismissed_at`, `snoozed_until`, `acted_at`, `payload` JSON (the serialized Insight: rendered strings in `lang`, evidence, action; this is also what emails read), `lang`, `priority`, `valid_until`, `generated_at`, `locked` (bool; locked rows store only the figure-free preview), `bell_at`, `push_at`, `email_at` (delivery caps are computed from these, no extra table).
Unique `(user, rule_id, dedupe_key)`. Index `(user, valid_until)`. Pruned by the command: rows 60 days past `valid_until`.

**`InsightFeedback`** (new): `user`, `rule_id`, `helpful` (bool), `updated_at`. Unique `(user, rule_id)`. No free text. Two "not helpful" on a rule mute it for that user for 90 days (D16).

**`UserProfile`** gets 3 fields (profile is already loaded every request, so reading them costs 0 queries): `insights_prefs` JSON (`enabled`, `modules_off`, `frequency` off/weekly/daily, `push`, `emergency_fund_months` default 6, optional `invest_target_pct`), `insights_generated_at`, `insights_stale` (bool).

**`Notification`**: add type `INSIGHT` (choices only) and an index on `(user, slug)`; engine bell items use slug `insight-{rule_id}-{dedupe_key}`. The existing table has no slug index and no unique constraint (audit item), which I would add together.

All in **one migration**: `expenses/migrations/00xx_insights_engine.py`.

### 3.7 Freshness, scheduling, caching (the "loads instantly" part)

- **Pages never compute.** The Dashboard panel and the per-module strip are small **HTMX fragments** loaded after paint (the same pattern as the budget suggestion banner), with the existing skeleton loader and reserved height. The page itself adds **0 queries** and does not touch the 4-hour `home_view` cache.
- **Warm fragment cost: 1 query** (state rows). Dismiss/snooze/helpful: 1-2 queries. Bell: 0 added (they are `Notification` rows already in the cached badge blob).
- **Nightly batch** (`generate_insights`, idempotent): active users only (activity in last 60 days, onboarded), `iterator(chunk_size)` in batches of ~200, grouped by salary day via `budget_cycle.current_groups`, heavy and light rules, per-user ceiling asserted in tests (about 30 queries).
- **Light refresh in the fragment**: if `insights_stale` is set and `insights_generated_at` is older than 10 minutes, the fragment runs `refresh(user, cost='light')` once (budget pacing, bills, goals; ceiling about 15 queries, only on this path, only after paint), then serves from state.
- **How a write marks staleness**: the existing `invalidate_dashboard_cache` signal path gains `UserProfile.objects.filter(pk=..., insights_stale=False).update(insights_stale=True)`. After the first write it matches 0 rows (a cheap no-op). Bulk imports already suppress that signal. Alternative (D6): time-based only, no signal change.
- **Cache**: no reliance on shared cache (LocMem is per worker). An optional 60-second per-process cache of the rendered fragment keyed by `(user, state-version)` is a micro-optimisation I would skip unless measurement asks for it.
- **Cron endpoint**: `/api/cron/generate_insights` and `/api/cron/deliver_insights` following the existing pattern (secret, thread, lock). **You will need to add them to your external cron service**; I cannot see or edit that. Suggested: generate 02:30 IST, deliver 09:00 IST.
- **Idempotency**: upserts keyed by the unique constraint; dismissal and snooze are never reset; delivery columns are set inside the same transaction as the send decision (`UPDATE … WHERE bell_at IS NULL`), so a double run does not double-send even when the cache lock fails.

### 3.8 Surfaces and adapters (adapters only format)

| Surface | Adapter | Source | Notes |
|---|---|---|---|
| Dashboard | `adapters/dashboard.py` | `generate(surface=DASHBOARD)` max 3 | HTMX fragment replaces the two legacy cards after switch-over; friendly "still learning your patterns" state for new users |
| Analytics panel | `adapters/analytics.py` | stored rules + inline sentences | inline sentences use the page's own `Facts` (no extra queries) |
| Module strip | `adapters/module_strip.py` | `generate(module=...)` max 2 | on Budgets, Expenses, Accounts, Loans, Goals, Recurring, Capital Events, Calendar, Holdings pages |
| Bell and push | `adapters/bell.py`, `push.py` | state rows with `BELL`/`PUSH` surface | creates `Notification`, calls existing `send_user_notification`; push only for rules flagged time-sensitive; never `sensitive`; text rendered in the user's language |
| Daily/weekly email | `adapters/digest.py` | top items across modules | Plus and Pro via existing `limits.email_notifications`; at most 1 a day; skipped when empty; unsubscribe link + `List-Unsubscribe` header |
| Monthly report | `adapters/monthly.py` | closed cycle | 3 best, a "went well" line first, one small thing to try next month |
| Year in Review | `adapters/wrapped.py` | same pick logic over the year | uses `wrapped.py`'s existing page |

Insight card (partial `_insight_cards.html`): small icon from the existing icon set, title, message, evidence chips (links), one primary action using existing `btn-action-*` classes, and a Bootstrap dropdown with "Not now" (snooze 7 days) and "Dismiss"; a "Helpful / Not helpful" pair in the same menu. Tone is carried by icon and a text label, not colour alone; `aria-label`s on the menu; the panel is a `role="region"` with a live-region for HTMX swaps. Light and dark come from the existing Bootstrap theme variables. **Before building I will audit the exact existing card, alert and dropdown classes and reuse them (UI consistency rule); I have not looked at the CSS yet.**

Acting on the primary action goes through `/insights/<uuid>/go/` (redirect that stamps `acted_at`, the same pattern as `notification_redirect`), so it counts as resolved and works for plain links.

### 3.9 Plan gating (one place)

`gating.py` holds the tier order and the only `allowed(rule, tier)` function. Rules declare `min_plan`; adapters and views never inspect tiers. Email delivery asks `get_limit(tier, 'email_notifications')` (the existing flag). Locked rules: the nightly pass records a `locked=True` row with the rule's figure-free `preview` line only when the user's data would have triggered it; the Dashboard and module strip may show at most 1 per week, as a kind one-liner with a "See Plus" link to Pricing, never in bell, push or email, and never for sensitive rules. A Free user with 0 loans (Free limit) never sees loan previews.

Free core: budget pacing/limit rules, upcoming bills, goal projection and milestones, plus the migrated dashboard insights that every tier gets today, plus one weekly observation from a small pool (D14). Plus/Pro: the cross-module, longer-history and emailed insights.

### 3.10 Privacy and telemetry

- `telemetry.py` is the only code that calls `ph_capture` for insights, and it builds properties from a whitelist `{rule_id, surface, action, count}`. (`ph_capture` already scrubs forbidden keys; the whitelist is stricter.)
- Failure log: `"insight rule failed rule_id=%s type=%s"` only. No `exc_info`, no message, no locals. A test injects a rule that raises with a category name in the message and asserts the captured logs contain neither the name nor an amount.
- Payload with amounts lives only in the user's own `InsightState` row (needed to render emails), pruned after 60 days.
- Shadow-mode comparison stores **match / delta-bucket counters**, never values (D-rollout).

### 3.11 Language, numbers, tone

- Every string: `gettext`/`ngettext` with **named placeholders**, one complete sentence per msgid (no concatenated fragments). Text is generated under `translation.override(profile.language)` in jobs.
- Numbers: `Money(profile.currency)` and `percent()` from `analytics/money.py`, `translate_digits` for hi/mr digits, Indian grouping for ₹. `Money.compact` only abbreviates from 1 lakh, so "about ₹9.2k" needs a small new `format.approx()` (k/L/Cr with one decimal, exact figure goes in evidence). The k/L/Cr suffixes are currently Latin in hi/mr; I recommend localised suffixes in engine copy (D19).
- Hindi and Marathi: msgids added to `update_translations.py` (`COMMON_HI`, `COMMON_MR`), then `makemessages`, `update_translations.py`, `compilemessages`; existing `test_translations.py` enforces completeness, placeholders and script. I write the first hi/mr drafts; **a native reader should review the tone** (D21).
- Tone linter (unit test): (1) every rule's `sample()` in en, hi, mr; (2) an AST scan of every gettext literal in `expenses/insights/**` (catches branches the sample doesn't hit). Checks banned words (warning, danger, overspent, you failed, wasted, bad, careless, you must, you need to, urgent, immediately, dangerously), `!`, ALL CAPS words, emoji, title word count, sentence count, and that every `sensitive` insight has no share text.
- Not financial advice: copy uses "could", "at this pace", "one option"; no product names, no buy/sell wording; the philosophy page wording is reused in the guide.

---

## 4. Migration plan for the existing insights

Approach: **build the engine beside the legacy code, prove parity, then switch.** Nothing in `home_view` is deleted until after the shadow period (separate, approved commit).

1. **Golden-fixture parity tests**: for calendar-month users, build fixtures, run `home_view` and the engine rules, assert the *same numbers* (percentages, projected amount, category lists, streak length) for all 9 migrated rules.
2. **Deliberate differences, listed for your approval** (D1): salary-cycle users on rules 1, 3, 13 (a, b, c above); every other number is the same.
3. **Copy changes**: calmer text per section 3.11. Example, Budget Breached:
   - Before: "Even strong months have leaks. Groceries exceeded by ₹1,200." / "…let's rebalance to stay safe."
   - After (title "Groceries went past its limit"): "Groceries is about ₹1.2k over its ₹8k limit this cycle. The other 4 categories are within theirs, and one busy category is common." Action: "Review the limit".
4. **Shadow mode** (`INSIGHTS_ENGINE_MODE=shadow`): the legacy dashboard renders as today. For a sample of users the engine's migrated rules also run and a comparator records only {rule_id, matched, delta bucket}; `insights_shadow_report` prints the match rate per rule. Values are compared in tests, never logged.
5. **Switch** per surface: dashboard fragment replaces the Alerts and Smart Insights cards, `Upcoming Payment` stays legacy until Phase C, Spending Pace, Impulse, Monthly Story and Net-worth forecast stay.
6. **Cleanup commit (after approval)**: remove dead code (`short_insight`, `trend_text`, `power_insight`), the migrated blocks, and the unescaped `|safe` path.
7. `send_notifications` is **not touched in Phase A**. Later (D3) its budget, bills, card and milestone items can be produced by the engine to gain translation, caps and calmer copy. Until then the engine milestone rule reuses the same slug scheme `milestone-{goal}-{pct}` and the extracted threshold helper so nothing is duplicated.

---

## 5. Rule catalogue

Abbreviations: Surfaces **D** dashboard, **A** analytics, **M** module strip, **B** bell, **P** push (time-sensitive only), **E** email digest, **R** monthly report, **Y** year in review. "Hist" = minimum history in completed cycles. Plan: **F** Free, **+** Plus and Pro, **Pro**. Example messages follow the tone rules and use illustrative amounts. IDs are `module.name`.

### Phase A: engine and migrated dashboard insights

| id | module | trigger | Hist | facts | example (en) | cooldown | surfaces | plan |
|---|---|---|---|---|---|---|---|---|
| `cashflow.pace_vs_usual` | cashflow | projected cycle spend > 1.25 × avg of up to 3 prior cycles; spent so far above a small floor | 1 | facts spending per cycle | "**Spending is running a little higher.** At this pace the cycle could end about 30% above your usual ₹42k. Money moves in waves, so this may just be a busy stretch." Detail: "If it is on purpose, you can dismiss this." | 7d per cycle | D M(expenses) | F |
| `budgets.category_runout` | budgets | a category's projected spend > its limit while used <= 100% (Phase B adds the date and history line) | 0 | budget payload | "**Groceries may reach its limit early.** At this pace Groceries could reach its ₹8k limit around 24 Oct; ₹5.8k is used so far." | 5d per category-cycle | D M(budgets) B | F |
| `accounts.deposit_maturing` | accounts | active deposit matures within 30 days | 0 | accounts | "**A deposit matures soon.** HDFC FD matures on 3 Nov, with about ₹2.1L coming back. You might decide where it goes next, or leave it for now." | until date | D M(accounts) B P | F |
| `expenses.category_above_usual` | expenses | top category > 1.1 × its average over up to 3 prior cycles | 1 | facts category_series | "**Dining is a little above usual.** Dining is about 18% above your 3-cycle average so far: ₹9.2k against ₹7.8k. One higher month is common, especially around festivals and travel." | 14d per category | D M(expenses) | F |
| `budgets.limit_passed` | budgets | used > 100% | 0 | budget payload | see section 4 | 7d per category-cycle | D M(budgets) B | F |
| `budgets.limit_nearing` | budgets | status limit (>= 85%) and not over | 0 | budget payload | "**Dining is close to its limit.** Dining has used about 88% of its ₹10k limit, with 9 days left. If that feels right, there is nothing to do." | 7d per category-cycle | D M(budgets) B P | F |
| `cashflow.saving_streak` | cashflow | >= 2 consecutive completed-or-current cycles with saved > 0 | 2 | `Summary.saved` | "**A steady run of saving.** You have kept more than you spent for 3 cycles in a row. Small habits like this add up quietly." (`ngettext`) | 30d | D Y R | F |
| `cashflow.savings_momentum` | cashflow | saved up vs previous cycle (both > 0) | 2 | `Summary.saved` | "**Your savings are growing.** You kept about ₹18k this cycle, up from ₹11k last cycle." | 14d | D R | F |
| `cashflow.year_pace` | cashflow | YTD saved > 0, at least 2 months | 2 | monthly saved | "**Your year is shaping up well.** If this year's average continues, you could keep around ₹2.1L by December. It is only an estimate and will move as your months do." | 30d | D A Y | F |

Phase A also ships: the persistence models and migration, `generate()`/`refresh()`, the dashboard HTMX panel, dismiss/snooze/helpful/go endpoints, bell adapter (Notification `INSIGHT`), Settings → Insights section, the tone linter, shadow mode, and the empty "still learning" state ("We are still learning your patterns. A few more weeks of entries and we will have something useful to say.").

### Phase B: budgets and expenses

| id | module | trigger | Hist | facts | example | cooldown | surfaces | plan |
|---|---|---|---|---|---|---|---|---|
| `budgets.category_runout` (extended) | budgets | as A, plus date and a line from the user's own best cycles | 1 | budget payload, history | detail: "In your last 3 cycles you stayed within the limit when weekly Groceries stayed near ₹1.8k." | 5d | D M B | F |
| `budgets.limit_often_over` | budgets | over limit in each of 3 completed cycles, limits in force for all 3 | 3 | resolver range + facts | "**Dining has run past its limit 3 cycles in a row.** It ended at ₹9.1k, ₹9.4k and ₹9.0k against ₹8k. You could raise the limit to match how you live, or keep it as it is." Action "Review limit" | 45d per category | M(budgets) D | + |
| `budgets.limit_often_under` | budgets | under 70% of limit for 3 cycles | 3 | same | "**Travel keeps coming in under its limit.** About ₹2.5k was left unused each cycle. You might lower it or share the room with another category." | 45d | M(budgets) | + |
| `budgets.room_to_share` | budgets | existing `find_suggestion` rule (donor < 10% for 3 months, needy at/over) | 3 | `budget_suggestions` | "**Travel has room to spare.** It has used under 10% of its limit for 3 months while Dining has reached its limit. You could move about ₹1,500 across for this cycle, or leave things as they are." Action reuses the existing apply endpoint | per pair-month | M(budgets) D | F (donors within plan allowance, unchanged) |
| `expenses.unusual_amount` | expenses | an entry in last 7 days >= max(3 × category median, P95) with >= 10 prior entries over >= 2 cycles; not a duplicate candidate; not a recurring posting | 2 | `expense_rows` | "**A larger Groceries entry.** ₹6.4k on 18 Oct is about 4 times your usual Groceries amount of ₹1.6k. If it was planned, you can dismiss this." | per entry; 14d per category | M(expenses) D | + |
| `expenses.possible_duplicate` | expenses | same amount, account, normalised description within 24h by `created_at`; key is the two ids | 0 | `expense_rows` | "**Two entries look alike.** Two ₹499 entries for Netflix were added within a few minutes on 12 Oct. If both are right, dismiss this; otherwise you could remove one." Evidence links both | per pair (permanent once dismissed) | M(expenses) D | F |
| `expenses.busiest_weekday` | expenses | one weekday averages >= 1.4 × the other days over 8 weeks, >= 20 entries | 2 | `Facts.daily_spending` | "**Saturdays tend to be your busiest.** Over 8 weeks Saturdays averaged about ₹1.8k, against ₹0.9k on other days. Plans and outings often cluster at weekends." | 28d | M(expenses) D | F (weekly observation pool) |
| `expenses.spending_timing` | expenses | >= 50% of cycle spend in first third of the cycle in each of 3 cycles | 3 | `Facts.daily_spending` | "**Most spending comes early in the cycle.** In the last 3 cycles about 55% landed in the first 10 days. That is common when rent and bills fall early." | 45d | M(expenses) | F (pool) |
| `expenses.category_change` | expenses | category ±10% vs previous cycle (replaces dashboard spikes/drops, now cycle-aware); up to 3 clubbed | 1 | facts | "**Groceries is a little lower.** About 22% down on last cycle (₹6.1k against ₹7.8k)." Positive tone for drops | 14d | D M | F |

### Phase C: recurring, loans, accounts

| id | module | trigger | Hist | facts | example | cooldown | surfaces | plan |
|---|---|---|---|---|---|---|---|---|
| `recurring.bills_soon` | recurring | active recurring due in next 7 days (migrates Upcoming Payment and the 3-day cron reminder) | 0 | recurring, `pending_by_day` | "**Bills coming up this week.** 3 payments totalling about ₹14.2k are due in the next 7 days, the first on Fri 24 Oct." | per due date | D M(recurring) B P | F |
| `recurring.fixed_costs` | recurring | recurring expenses monthly-equivalent total and share of avg income | 1 | `recurring_utils`, facts income | "**Your fixed costs.** Recurring payments come to about ₹28k a month, around 31% of your income. Knowing this makes the rest of the month easier to plan." | 30d | M(recurring) D R | + |
| `recurring.price_change` | recurring | latest posting differs from previous by > 5% (**needs verification** that postings link to the recurring item; otherwise the rule is not feasible and I will say so) | 2 postings | expense_rows | "**A recurring amount changed.** Netflix went from ₹499 to ₹649 on the latest entry. If you expected this, you can dismiss it." | per change | M(recurring) D | + |
| `recurring.looks_recurring` | recurring | existing nudge: same description and amount >= 3× in 90 days, no recurring | 3 | expense_rows | "**Gym fee looks like it repeats.** The same ₹1,200 entry appeared 3 times in 3 months. You could make it recurring, or leave it." | 60d per description | M(recurring) | F |
| `recurring.similar_services` | recurring | two active monthly recurring in the same category with amounts within 25% (**low confidence, recommend cutting** D17) | 0 | recurring | "**Two similar payments.** Two monthly Entertainment payments are close in size (₹499 and ₹549). If you use both, there is nothing to do." | 90d | M(recurring) | + |
| `cashflow.low_point_before_salary` | cashflow | projected lowest liquid balance over next 14 days (balances + scheduled items − typical daily spend) falls below 7 days of typical spend before the next salary; `sensitive` if below zero | 1 | accounts, `pending_by_day`, facts | "**A tight stretch before payday.** At this pace your bank balance could dip to about ₹3.2k around 2 Nov, a few days before your salary on 5 Nov. If it helps, you could move a bill or keep a small buffer." | 7d | D M(accounts) | + |
| `loans.interest_paid_year` | loans | any active loan; interest so far this financial year | 1 repayment | `interest_by_loan` | "**Interest on your loans this year.** So far this year about ₹84k of your EMIs went to interest and ₹1.6L to principal." sensitive | 90d | M(loans) R Y | + |
| `loans.extra_emi_effect` | loans | EMI loan, active, within term; reuses `LoanService.calculate_extra_emi_savings` | 3 repayments | loans | "**What one extra EMI could do.** One extra EMI a year on your Home Loan could close it about 14 months earlier and save around ₹1.4L in interest. It is only an estimate, and entirely optional." sensitive | 90d | M(loans) | + |
| `loans.rate_gap` | loans | two active loans whose current rates differ by >= 1 point | 0 | loans | "**Your loan rates differ.** Car Loan is at 9.5%, about 1.5 points above Home Loan at 8%. This is a comparison only, for whenever you next look at your loans." sensitive | 120d | M(loans) | Pro (needs 2+ loans) |
| `loans.milestone_ahead` | loans | principal repaid crosses 25/50/75/90%, or <= 12 months left, or a recorded rate change takes effect within 30 days | 0 | loans | "**Your Home Loan is nearly halfway paid.** About 47% of the principal is repaid, with about 11 years left on the current schedule." (progress; not sensitive) | per threshold | D M(loans) | + |
| `accounts.card_billing_soon` | accounts | card with billing day within 10 days and an amount owed; merges "pay before billing date" into the detail (only a billing day exists, no due day: D10) | 0 | accounts | "**Card billing date is near.** HDFC Card bills on 28 Oct, with about ₹32k outstanding. A payment before then may lower what shows on the statement." sensitive, no push | per cycle | D M(accounts) B | F |
| `accounts.card_utilisation` | accounts | utilisation >= 50% of a known limit | 0 | accounts | "**Card use is a little higher.** HDFC Card is using about 62% of its ₹80k limit. That is common after a big purchase." sensitive | 30d | M(accounts) | + |
| `accounts.idle_cash` | accounts | idle cash expressed in months of spending exceeds the user's own target (default 6, editable in Settings) by >= 50% | 2 | `NetWorthPoint.idle`, facts | "**Cash set aside for emergencies.** About ₹4.2L sits in savings accounts, roughly 9 months of your spending, against your 6-month target. If that feels right, there is nothing to do." (descriptive only) | 60d | M(accounts) D R | + |
| `accounts.not_reconciled` | accounts | reconcilable account with activity and no reconciliation for 60+ days (or manual-value account 30+ days stale) | 0 | accounts, `AccountReconciliation` | "**A balance could use a check.** HDFC Savings was last matched to a statement 74 days ago. A quick check when you have a moment keeps it trustworthy." | 30d | M(accounts) | + |

### Phase D: income, goals, transfers and investing, holdings, capital events, calendar, net worth

| id | module | trigger | Hist | facts | example | cooldown | surfaces | plan |
|---|---|---|---|---|---|---|---|---|
| `income.steady_vs_variable` | income | share of income from Salary vs other source types over 3 cycles | 3 | `income_by_source` | "**How steady your income is.** About 82% of your income came from salary; the rest varied between ₹6k and ₹21k a cycle." | 60d | M(income) A R Y | + |
| `income.salary_late` | income | salary day set or usual salary day learned from >= 2 salary entries; none recorded this cycle and today >= expected + 3 days; gentle, no push | 2 | facts | "**Salary has not arrived yet.** It usually lands around the 28th and none is recorded this cycle. If it is on its way, there is nothing to do." | 5d | D M(income) | + |
| `income.gap` | income | no income entry for 45+ days after a regular pattern; `sensitive` | 3 | facts | "**No income recorded for a while.** There has been no income entry for 47 days. You can add income whenever you are ready." | 30d | M(income) | + |
| `goals.projected_finish` | goals | >= 2 contributions over >= 30 days, not complete | 1 | goal projection helper | "**Goa trip is on track for March.** At the pace of the last 3 months, it reaches ₹1L around March 2027." | 30d | D M(goals) | F |
| `goals.small_change` | goals | same, shows effect of +5% of remaining per month (rounded to ₹500) | 1 | goal projection | "**A small change for Goa trip.** Adding ₹500 a month would bring the finish forward by about 2 months. It is one option; the current pace is fine too." | 60d | M(goals) | + |
| `goals.stalled` | goals | no contribution for 45+ days after >= 2 earlier ones | 2 | goals | "**Goa trip has been quiet lately.** There has been no contribution for 52 days; it stands at 38% of ₹1L. A small top-up or a pause are both fine." | 30d | M(goals) D | + |
| `goals.milestone` | goals | 50/90/100% via the shared helper and existing slugs `milestone-{goal}-{pct}`; engine does not create a second bell item | 0 | goals | "**Goa trip is halfway.** You have reached 50% of ₹1L. Steady contributions got you here." | once per threshold | D M(goals) Y | F |
| `transfers.invested_share` | transfers | invested ÷ income vs the user's target if set, else vs their own average of the previous 3 cycles | 3 | `Summary.invested`, income | "**Share of income you invest.** About 14% of income went into investments this cycle, against your 15% target." | 30d | M(transfers) A R Y | + |
| `holdings.sip_missed` | holdings | SIP-linked recurring instalment due 3+ days ago and not posted (**needs verification** of how recurring posting reports failures) | 0 | recurring, holdings | "**A SIP has not been recorded.** Your ₹5,000 SIP in Nifty fund was due on 5 Oct and is not recorded yet. If it went through, you can add it." | per due date | M(holdings) D | + |
| `holdings.concentration` | holdings | one holding >= 40% or one instrument type >= 70% of invested value | 0 | holdings valuations | "**Where your invested money sits.** Mutual funds are about 71% of your invested money and one fund about 38%. This is just a picture of your mix." No advice wording | 90d | M(holdings) | + |
| `capital_events.rate_effect` | capital_events | counted-or-extra capital events in the cycle change the savings rate by >= 3 points | 1 | `facts.with_capital_events(True/False).summarize` | "**A one-off cost shaped this cycle.** Your savings rate is 31%, or 18% with the ₹42k roof repair counted. One-off costs are normal and are kept out of averages by default." | per cycle | M(capital) A D | + |
| `calendar.heavy_week` | calendar | next 7 days scheduled outflow >= 1.5 × the user's median weekly spend | 2 | `pending_by_day`, facts | "**A busy week of payments ahead.** About ₹26k is scheduled over the next 7 days, roughly twice a typical week for you." | 7d | D M(calendar) B P | + |
| `networth.what_moved` | accounts | net worth change this cycle split into kept (saved), invested transfers, and value changes (residual) | 2 | net worth points, `Summary` | "**What moved your net worth.** It is up about ₹62k this cycle: roughly ₹48k from what you kept and ₹14k from changes in value." | per cycle | A D R Y | + |

### Phase E: analytics, cashflow, reports

| id | module | what | mode | plan |
|---|---|---|---|---|
| `analytics.period_summary` | analytics | replaces `Page.insight()` ("You kept X of Y (Z%)", ahead/behind comparison) | inline | F |
| `cashflow.biggest_cost`, `cashflow.spent_beyond_income` (sensitive), `cashflow.invested_and_kept`, `cashflow.category_moved` | cashflow | replace the 5 sentences in `takeaways()` (max 3 shown); same numbers | inline | F |
| `expenses.categories_creeping_up` | expenses | replaces `rising_categories()`: 3 consecutive cycles rising, **cycle-aware and respecting the toolbar filters** (today it ignores both); one Insight per category, panel adapter renders rows | inline | + |
| `networth.what_moved` inline variant | accounts | Net Worth tab sentence | inline | + |
| Composers (not rules) | | `monthly.py` picks 3 (positive first), "things that went well" line, "one small thing to try next month" = best `opportunity` with an action; `wrapped.py` lines; **weekly digest** merges top 3 across modules (one email a week by default, daily only if chosen on Plus+) | | + |

Left as widgets, not insights: Financial Health rows (`health()`), Spending Pace card, Impulse vs Planned, Monthly Story, budget page safe-to-spend text.

Modules in your list with no rule in this catalogue: **import** (the import preview already explains decisions inline) and **transfers** beyond `invested_share`. I would leave them empty rather than invent weak rules.

---

## 6. Decisions I need from you (each with a recommendation)

| # | Decision | Recommendation |
|---|---|---|
| **D1** | Migrated dashboard rules 1, 3, 13 use correct salary-cycle bases; for salary-cycle users numbers change (section 1a). Calendar users identical. Options: (A) correct from the start, you approve the diff list before switch; (B) bug-for-bug parity (breaks "rules never redefine"); (C) disable these three for salary-cycle users until approved. | **A**, per-rule switch only after you read the shadow report |
| **D2** | Retire from the dashboard: Super Saver, You're in Control, In the Green / Fresh Start, Fun Fact, Power Insight ("Cut 20%"), "High five", net-worth milestone ("Most people never get here"), top-category line, dead code. Keep Monthly Story, Spending Pace, Impulse. | Retire (tone and consistency). Ask first because it removes visible content |
| **D3** | Existing cron alerts (bills, card billing, budget, milestones) vs the "bell: 1 new insight a day, push 2 a week" caps. They are reminders today, uncapped. | Phase A leaves them untouched. Later convert them to engine output with the caps, only on your approval |
| **D4** | Bell/push/email text rendered in `profile.language` at generation (engine only). The existing cron's English-only output is a separate problem. | Yes for the engine; fix the cron separately |
| **D5** | New persistence: `InsightState`, `InsightFeedback`, 3 `UserProfile` fields, `Notification` type `INSIGHT` plus `(user, slug)` index, one migration. | Approve |
| **D6** | Freshness: nightly batch + light inline refresh of stale users on first panel load, with a "stale" flag set by the existing invalidation signal (no-op after the first write). Alternative: time-based only. | Flag-based as described; measure, drop the signal hook if it costs anything |
| **D7** | You must add two external cron entries (generate, deliver); I cannot see the current cron setup. | Generate 02:30 IST, deliver 09:00 IST |
| **D8** | Email digest needs an unsubscribe flag and one-click link before shipping. The existing monthly report also has no opt-out. | Ship digest opt-out in Phase A settings; flag monthly report separately |
| **D9** | New preferences: emergency-fund months (default 6) and optional invest-share target, so "versus your own target" is real. | Add, stored in `insights_prefs`, no new tables |
| **D10** | Credit cards only have a billing day, not a payment due date. Copy says "billing date". Adding a due-day field is a schema change. | Not now |
| **D11** | "Interest paid so far this year": financial year (April-March) or calendar year? | Financial year for ₹ users, calendar otherwise |
| **D12** | Monthly report uses the calendar month, not the salary cycle. The new insights section should follow the user's closed cycle. | Section follows the closed cycle; moving the report's own numbers to the cycle is a separate change for you to approve. Subject text stays as is (it is the dedup key) |
| **D13** | Existing defects found: stored XSS via capital-event note, `date.today()` mix, `prev_month=None` for salary-cycle users, hard-coded Netflix/Starbucks INR prices, a hard-coded "Rent". | Fix the XSS in a small separate commit before Phase A; the rest handled by D1/D2 |
| **D14** | Plan gating: legacy insights stay Free (they are free today); new deeper rules Plus+; Free weekly observation pool = weekday, spending-timing, category-change. No `plans.py` change; keep `has_ai_access` as the existing master gate. | Approve |
| **D15** | Push quiet hours: fixed 21:00-08:00 IST (the app has no per-user timezone and promises a 9 pm reminder separately). | Fixed, no UI |
| **D16** | Two "not helpful" on a rule mutes it for that user for 90 days. | Approve |
| **D17** | Cut weak rules: `recurring.similar_services`, the two Notification nudges (multiple accounts, organise spend). | Cut the first, keep the nudges out of the engine for now |
| **D18** | "pace" means the Traffic Alert; the Spending Pace card stays a metric. | Confirm |
| **D19** | Localised k/L/Cr suffixes in engine copy for hi/mr. | Yes |
| **D20** | `Insight.sensitive` flag added to your field list. | Approve |
| **D21** | Hindi and Marathi drafts by me; native review of tone before release. | Please arrange a reviewer for the strings |

---

## 7. Test plan

Runner: `manage.py test --parallel`; each phase ends with the real summary line, reported honestly. Python 3.12 compatibility: `ruff check --target-version py312` (no `--fix`), and avoid 3.13-only features.

- **Per rule** (generated from the registry plus hand cases): happy path; not enough history (returns `[]`, never a weak insight); zero budget or zero income; multi-currency (base amounts, display currency formatting); salary cycle crossing a month boundary and salary day 31 in February; deleted or inactive account; single month of data; Capital Events switch on and off; Hindi and Marathi digits.
- **Engine**: ranking, per-surface caps, module diversity, cooldown per rule and per dedupe key, dismissal gone for the period then back next period, snooze until date, `acted_at` resolves, plan gating (including locked preview rules, no figures), failure isolation (a raising rule is skipped, logged by id only), idempotent re-run (no duplicates, dismissal preserved), batch command with `--user-id`, quiet hours, bell 1/day, push 2/week, email 1/day and not-when-empty, "never the same insight on two surfaces in one view".
- **Adapters**: dashboard, module strip, bell/push (not for sensitive), digest, monthly picks, wrapped lines; HTMX dismiss/snooze/helpful return fragments, not full pages; permission checks (another user's uuid returns 404).
- **Query counts** (`assertNumQueries` / ceilings): warm panel = 1 query; dismiss = 2; stale light refresh ≤ 15; per-user nightly ≤ 30; adding an N+1 test with many categories, loans, goals and accounts proving counts do not grow with N; `home_view` unchanged on the warm path (existing `test_dashboard_query_budget.py` stays green).
- **Parity**: golden fixtures comparing legacy and engine numbers for the 9 migrated rules (calendar users identical; documented differences for salary-cycle users asserted explicitly).
- **Tone linter**: samples in en/hi/mr plus AST scan of every gettext literal for banned words, `!`, ALL CAPS, emoji, length limits, no share text on sensitive.
- **Translations**: all new msgids present in hi and mr, placeholders intact, Devanagari script, `compilemessages` clean, rendered panel in hi/mr shows translated text and Devanagari digits (extends `test_translations.py`).
- **Privacy**: injected failing rule with a category name and amount in the message; assert captured logs and telemetry payloads contain neither; telemetry whitelist test.
- **Links**: every sample insight's evidence and action links resolve (`django.urls.resolve`) and, for transaction links, land on a filter that returns the quoted rows.
- **Accessibility smoke**: roles, aria-labels, no colour-only meaning (template assertions), plus a browser check in light and dark and at phone width using the in-app browser.

## 8. Rollout plan

Flag: **`INSIGHTS_ENGINE_MODE`** = `off | shadow | on` (env, read in settings), plus `INSIGHTS_ENGINE_USER_IDS` (allow-list that overrides the mode for named users) and `INSIGHTS_CHANNELS` (comma set of `bell,push,email`, default empty). Code merged with mode `off` has no behavioural effect.

1. Deploy migration and code with `off` (additive migration, safe).
2. `shadow` for the allow-list and a small sample: legacy renders, engine compares, `insights_shadow_report` shows match rates per rule (counts only).
3. You review the report and the diff list (D1); approve per rule.
4. `on` for the allow-list (you): dashboard panel replaces legacy cards.
5. `on` for everyone; bell, push and email channels enabled one at a time through `INSIGHTS_CHANNELS`, each watched for a week.
6. After two quiet weeks, a separate approved commit removes the legacy blocks.
Each phase ships as its own commit (as you asked), each flagged off by default for anything new; Phases B to E add rules, the flag and channels stay as they are.

## 9. What I did not verify

- The exact existing card, alert, dropdown and skeleton CSS classes (done at the start of Phase A UI work).
- Whether recurring-generated Expenses link back to their `RecurringTransaction` (decides whether `recurring.price_change` is feasible) and how recurring posting reports failures (decides `holdings.sip_missed`).
- The salary-cycle `prev_month=None` behaviour (reasoned from the code by the audit, not run).
- Whether `expiry_reminder_sent` is reset on renewal, the payment views, `email_backends.py`, `mobile/`, and the allauth emails (outside this feature).
- The external cron schedule and whether production runs Redis.
- Guide pages: Budgets has en/hi/mr; Notifications and several others are English only. The Insights page will be en/hi/mr as the philosophy and budgets pages are.

---

## 10. Phase A as built (differences from the plan above)

Decisions you gave: D1 correct cycle bases (per rule, after the shadow report); D2 retire the listed legacy items; D3 existing alerts untouched and the engine's bell and push are additional, free on every plan, email Pro only; D5 approved; D10 the billing day is a day of the month; D13 fixed first (commit 9a76ff05).

* **`recurring.bills_soon` moved into Phase A.** The dashboard's "Upcoming Payment" alert lived in the card the engine replaces, so leaving it for Phase C would have removed it. It is the same 7-day list, as one insight, and no longer lists recurring income as a payment due (a known, tested difference).
* **Bell and push surfaces are narrower than the catalogue.** `budgets.limit_passed` and `budgets.limit_nearing` are dashboard-only in Phase A, because `send_notifications` already sends "Budget Exceeded" and "Budget Alert" for the same events. The engine's bell and push carry `budgets.category_runout` (predictive, nothing like it exists) and `accounts.deposit_maturing`; `cashflow.*` and `expenses.*` stay on the pages.
* **Where it sits.** The panel replaces the visible "Smart Insights" carousel card beside the metrics. The two cards inside the collapsed "Financial Intelligence Hub" accordion (Money Flow Analysis, Actionable Alerts) are hidden when the engine is on; Monthly Story, Spending Pace and Impulse stay.
* **Shadow mode** records per rule only `exact / close / far / shape` as a log line and an analytics event (there is no `insights_shadow_report` command; the golden tests compare the values). Sample size is `INSIGHTS_SHADOW_PERCENT`.
* **Momentum** also speaks when the savings rate is 20% or more (Super Saver, which retired, used to take that case). The year projection waits for a second month of the year.
* **Storage additions** beyond the plan: `InsightState.created_at` (so a dismissal rests only rules that speak *after* it) and `InsightFeedback.not_helpful_count` (two "not helpful" votes mute a rule for 90 days).
* **Not yet:** the per-module strip is ready as an endpoint (`/insights/panel/?surface=module&modules=budgets`) but is embedded on pages from Phase B; no email channel until Phase E.
* **Measured:** a refresh is 14 queries; reading the panel is 1 query (2 the first time a row is shown that day); the dashboard page itself adds none.

## 11. Phase B as built (budgets and expenses)

Rules added (all in `docs/insights-rules.md`, with their own examples): `expenses.possible_duplicate`, `expenses.unusual_amount` (Plus, nightly), `expenses.busiest_weekday`, `expenses.spending_timing`, `expenses.category_change`, `budgets.limit_often_over` and `budgets.limit_often_under` (Plus, nightly), `budgets.room_to_share`, plus the "what has helped" line on `budgets.category_runout`.

* **`room_to_share` is the Budget page's own suggestion.** `budget_suggestions.find_suggestion` was split so the rule and the banner share one pure function (`pick_suggestion`); the banner's behaviour and tests are unchanged, and a dismissal made on the Budget page hides the insight too.
* **`category_change` compares like with like.** The dashboard's spike and drop bullets compared a partly finished month with a whole one; this compares the same number of days into the previous cycle, needs a week of the cycle, and only mentions a move that is at least 10% of the category and 5% of last time's spending.
* **The Free weekly observation** is the weekday and timing rules (`pool='weekly'`): a Free person sees one of them a week; Plus and Pro see both. `category_change` stays outside the pool because the spike and drop bullets it replaces were free for everyone.
* **Module strips** are embedded on the Budget page and the Expenses page (a bounded card above the content, absent when there is nothing to say). On the Budget page the strip leaves out what the page already says: limits passed or nearing and the smart move.
* **Facts layer additions:** `load_expense_rows` (one query, no model objects), `Facts.daily_expenses`, and context loaders `recent_expenses`, `expense_rows` and `budget_history`.
* **Cost:** a dashboard visit's quick refresh is 15 queries (it was 14); the nightly run is 19, and reads history once for every rule that needs it.
* **Not yet:** the Recurring page and Accounts page strips (Phase C, with the rules that belong there).

## 12. Phase C as built (recurring, loans, accounts)

Rules added (see `docs/insights-rules.md`): `recurring.fixed_costs` and `recurring.price_change` (Plus), `recurring.looks_recurring` (Free, Recurring page only, nightly), `cashflow.low_point_before_salary` (Plus, nightly, extra gentle below zero), `loans.interest_paid_year`, `loans.extra_emi_effect` and `loans.milestone_ahead` (Plus), `loans.rate_gap` (Pro), `accounts.card_billing_soon` (Free), `accounts.card_utilisation` (Plus), `accounts.idle_cash` and `accounts.not_reconciled` (Plus, nightly).

* **Costs nothing for people without the thing.** Rules declare `requires={'loans'|'cards'|'recurring'}`; one inventory query decides, so someone with no loans, cards or recurring payments pays no query for those rules. A dashboard visit's quick refresh is still 15 queries; the nightly run for a person with all of it is higher (net worth alone is about eight) and is only run by the command.
* **Loans reuse the loan service.** `ctx.loans` loads active loans with `annotate_loan_principal_totals` and prefetched rates, and fills each loan's cached summary, so `LoanService.calculate_extra_emi_savings` and `current_rate` run with no query per loan (tested against `get_loan_summary`). Interest and principal for the financial year come from one grouped query that uses the same `interest * exchange_rate` as the savings definition.
* **Cards:** only a billing day exists, so the copy says "billing date" (D10). What is owed is the ledger balance in the card's own currency. The existing reminders three days before billing are unchanged.
* **`recurring.price_change` is feasible**: posted entries carry the schedule's description plus " (Recurring)", so the postings are read by that description (a renamed schedule starts a fresh history).
* **`recurring.similar_services` was cut** (D17). `recurring.looks_recurring` is Recurring-page only because the bell already carries the same nudge.
* **Financial year** (April to March) for rupee accounts, calendar year otherwise (D11).
* **The cushion target** is a Setting (3 to 24 months, six by default), so "idle cash" compares with the person's own number (D9).
* **Strips** on the Subscriptions, Loans and Accounts pages; each leaves out what its page already shows (due payments; the bill-due and maturing cards).
* **Everything about debt is sensitive**: extra gentle, no share text, never pushed, and shown to a plan below the rule's minimum as nothing at all (no preview).
