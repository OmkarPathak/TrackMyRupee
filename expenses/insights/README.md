# The Insights Engine

One set of rules, many surfaces. A rule is written once, as a pure function of an `InsightContext`, and the engine
stores what it says. Pages, the bell, push (and later the digest, the monthly email and Year in Review) only format
what was stored. No rule is copied between surfaces, and a rule never redefines what counts as spending, savings,
income or net worth: it reads those from `analytics/facts.py` and `savings.py`.

```
rules/*.py ──evaluate(ctx)──▶ engine.refresh ──▶ InsightState rows ──▶ engine.generate ──▶ adapters
                                  ▲                  (payload, state)        (1 query)      dashboard panel
                  InsightContext (lazy, declared loaders)                                   bell, push
```

* `types.py` immutable `Insight`, `Evidence`, `Action`; enums for module, kind, tone, surface, plan.
* `registry.py` the `@rule` decorator and the registry. Rules in `rules/` are imported automatically.
* `context.py` what a rule may read. Each loader must be listed in the rule's `needs`: `facts`, `budgets`, `accounts`, `recurring`, `recent` (entries of the last few days), `rows` (about six cycles of single entries, nightly rules only) `budget_history` (the last three budget months' limits and spend), `inventory` (does the person have loans, cards, recurring payments, goals, holdings), `recurring_all`, `postings` (what each recurring expense actually cost each time), `pending` (scheduled items for two weeks), `cash` (net worth point; `.idle` is cash held), `accounts_all`, `cards` (what is owed, in the card's own currency), `loans` (totalled, rates prefetched, so the loan service runs without a query per loan), `loan_paid` (this financial year), `reconciliations`, `income_rows` (about eight months of income entries), `goals` (every goal with its contributions and whether the plan locks it), `holdings` (active holdings valued in the person's currency, nightly only) and `networth` (the nightly snapshot from before the cycle began, and today's point; nightly only).
* `engine.py` `refresh` (evaluate and store), `generate` (read, suppress, rank, cap), dismiss, snooze, feedback.
* `gating.py` the only place a plan tier is read. `prefs.py` the person's Settings. `telemetry.py` the only analytics.
* `format.py` money, percent and date text; `tone.py` the tone linter; `sampling.py` synthetic contexts for tests.
* `adapters/` formatting only (the bell and push so far). Templates are in `templates/expenses/partials/_insight_*.html`.

## Add a rule in under 30 minutes

1. **Pick the facts.** What you need is almost always in the context already:
   `ctx.summary(key)` (a budget month through the savings definition: `income`, `spending`, `saved`, `rate`,
   `categories`), `ctx.facts.category_series(...)`, `ctx.budgets` (the Budget page payload), `ctx.maturing`,
   `ctx.bills_due`. `ctx.cycle`, `ctx.keys_back(n)` and `ctx.elapsed_days` follow the person's salary cycle. If a number
   is missing, add a loader to the context (or to the facts layer) and declare it in `needs`; do not query from a rule.
2. **Write the rule** in `rules/<module>.py`:

   ```python
   @rule(id='expenses.example', module=Module.EXPENSES, surfaces={Surface.DASHBOARD, Surface.MODULE},
         needs={'facts'}, cooldown_days=14, min_history=2, sample=sampling.default_sample)
   def example(ctx) -> list[Insight]:
       ...
       return [Insight(rule_id='expenses.example', module=Module.EXPENSES, kind=Kind.PATTERN, tone=Tone.CALM,
                       priority=45, title=_('...'), message=_('... %(x)s ...') % {...},
                       evidence=(Evidence(_('Label'), ctx.fmt.money(value), link),),
                       action=Action(_('Next step'), link), dedupe_key=f'{ctx.key:%Y-%m}', valid_until=end,
                       surfaces=frozenset({...}), numbers={...})]
   ```

   * `dedupe_key` names the subject and the period (`'cat12:2026-10'`): a new period is a new insight, a dismissed one
     stays dismissed for its own period only.
   * `min_history` is completed cycles with any activity. Below it the engine skips the rule and shows "still learning".
   * `pool='weekly'` puts a rule in the Free plan's weekly observation: Free people see one pool rule a week, Plus and Pro all of them.
   * `requires={'loans'}` (or `'cards'`, `'recurring'`) skips the rule, at no cost, for a person who has none.
   * `cost='heavy'` is for rules that scan history (nightly only); `cost='light'` may also run when a dashboard visit finds the data changed.
   * `push=True` (and `Surface.PUSH` in `surfaces`) only for things that are time-sensitive *and* helpful. A
     `sensitive=True` insight (debt, a shortfall, a month in the red) never has share text and is never pushed.
   * `min_plan=Plan.PLUS` locks it for Free: give `preview=lambda: _('...')`, a figure-free line.
3. **Write the words** following `docs/insights-tone.md`: what we noticed, a little context, at most one optional
   next step. Every string is `gettext` with named placeholders, one whole sentence per msgid (never glue translated
   pieces together). Use `ctx.fmt.approx()` in sentences and `ctx.fmt.money()` in evidence. Use two msgids when one and
   many differ (`if count == 1`), and `ngettext` only when the placeholders are identical.
4. **Add the sample.** `sample=` builds a synthetic context (see `sampling.scenario`), so the tone, translation and link
   tests run your rule in English, Hindi and Marathi with no extra fixture. The registry refuses a rule without one.
5. **Translate.** Add the strings to `ENTRIES` in `update_insight_translations.py` (Hindi and Marathi), then
   `python update_insight_translations.py` and `python manage.py compilemessages`. `python update_insight_translations.py
   --check` lists anything missing. The tests fail on a missing or broken translation.
6. **Test it** in `expenses/tests/insights/test_rules.py`: the happy path, not enough history, a zero budget,
   another currency, a salary cycle that crosses a month, a single month of data. Run
   `python manage.py test expenses.tests.insights --parallel`.

## Rules of the road

* Rules never query. They read `ctx` only, and only what they declared in `needs` (reading anything else raises).
* Money is `Decimal`, always from base amounts. Never a float.
* A rule that raises is skipped and logged by id and exception class only. Never log, print or send an amount, a
  description, an account name or a category (the telemetry module whitelists `rule_id`, `surface`, `action`, `count`).
* Insights describe the person's own data and offer optional next steps. No peer comparison, no products, no predictions.

## Caps and cadence

Dashboard 3 (at most 2 from one module), a module page 2, the bell 1 new insight a day (1 a week for "quietly", none
for "only on the pages"), push 2 a week for time-sensitive rules only and never between 9 pm and 8 am. These are in
addition to the alerts `send_notifications` already creates. The email digest is Pro only.

## Running it

| | |
|---|---|
| `INSIGHTS_ENGINE_MODE` | `off` (default), `shadow` (legacy dashboard, engine compared with it for a sample), `on` |
| `INSIGHTS_ENGINE_USER_IDS` | comma-separated user ids that always get the engine, whatever the mode |
| `INSIGHTS_CHANNELS` | which channels may send: `bell`, `push` (and later `email`). Empty sends nothing |
| `INSIGHTS_SHADOW_PERCENT` | share of people compared in shadow mode (default 10) |
| `manage.py generate_insights` | nightly, idempotent, batched: `--user-id`, `--batch-size`, `--active-days`, `--light-only` |
| `manage.py deliver_insights` | the bell and push, capped, safe to re-run |
| `POST /api/cron/generate-insights/` and `/api/cron/deliver-insights/` | the same, for the external cron (secret header) |

Suggested schedule: generate at 02:30 IST, deliver at 09:00 IST.

Reading is one query (`generate`). When a person's data has changed since the last run, the panel fragment re-runs
the quick rules once (about 14 queries) and not more than every ten minutes.

Every registered rule, with an example of what it says, is in `docs/insights-rules.md` (regenerate it with `python scripts/generate_insights_rules_doc.py`).

## UI conventions (settled with the product owner; follow them for every new rule and surface)

* **Transactions come first.** On a page that is about entries (Expenses), the insights sit under the title, summary and
  toolbar and above the list, never above the title. On the Budget page the strip sits directly under the hero card.
* **A strip is one compact line per insight**, in one card: a small muted icon (no filled circle), a title, and a one-line
  summary (desktop only; the title alone on a phone). Titles are regular weight on a phone and semibold from 768px up.
  The line opens on a click to show the full text; while it is open the header summary hides (never say it twice).
  Strips show at most two, are absent when there is nothing to say, and keep a gap above them (`mt-3`).
* **An opened insight reads by importance:** the sentence (normal weight), then small quiet number chips, then one footer
  row: the next step on the left, the quiet controls on the right.
* **The next step is a plain green link with a small arrow in strips** ("Review the limit →"), not a bordered button. (The
  dashboard card keeps the outlined `btn-action-outline` button.) Controls (Not now, Dismiss, thumbs up and down) are small,
  muted and always inline: **no dropdowns or overlays inside anything that scrolls** (they get clipped and stretch the card).
* **The dashboard card never makes its row taller:** beside the metrics it takes their height; stacked it stops at 30rem;
  more insights scroll inside it, clipped to the card's rounded corners.
* **Never repeat what a page already says:** the strip for a page skips rules whose content the page shows itself
  (`STRIP_SKIPS` in `views.py`).
* Reuse the app's Bootstrap classes and variables so light and dark themes work; check a new surface at phone width.
