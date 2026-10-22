# Insights Engine: what changed, and how to roll it out

Branch `claude/insights-engine`, five commits (Phases A to E). Design: `docs/insights-engine-plan.md` (sections 10 to 14 describe each phase as built). Developer guide: `expenses/insights/README.md`. Every rule and what it says: `docs/insights-rules.md`. Tone: `docs/insights-tone.md`.

## The one migration

`expenses/migrations/0124_insights_engine.py` (Phase A; nothing after it needed another):

* `InsightState` (the stored insights and what each person did with them; unique on user, rule, dedupe key) and `InsightFeedback` ("was this helpful", one yes or no per rule).
* `UserProfile.insights_prefs` (JSON: on/off, topics, how often, push, cushion target, email digest), `insights_generated_at`, `insights_stale`.
* `Notification.notification_type` gains `INSIGHT`, and an index on `(user, slug)` for the bell's de-duplication.

It only adds tables, columns with defaults and an index; it is safe to run on a live database and safe to leave in place if the engine stays off.

## The switch (nothing changes until it is turned on)

| Setting (environment) | Meaning |
|---|---|
| `INSIGHTS_ENGINE_MODE` | `off` (default: the legacy dashboard and pages only), `shadow` (legacy renders; for a sample, the engine is run and compared, counts only), `on` |
| `INSIGHTS_ENGINE_USER_IDS` | comma-separated user ids that always get the engine, whatever the mode (for a staged start with your own account) |
| `INSIGHTS_CHANNELS` | channels allowed to send: `bell`, `push`, `email` (the digest). Empty sends nothing |
| `INSIGHTS_SHADOW_PERCENT` | share of people compared in shadow mode (default 10) |

The analytics sentences (what you kept, the takeaways) are the same words as before whichever way the switch is set; the engine only changes where they come from. Everything else new appears only with the engine on.

## Rollout steps

1. Deploy with `INSIGHTS_ENGINE_MODE=off` and run migrations. Nothing visible changes.
2. Add the three cron jobs next to the existing ones (each is a command and a secret-protected POST for the external cron):
   `generate_insights` at 02:30 IST (`POST /api/cron/generate-insights/`), `deliver_insights` at 09:00 IST (`POST /api/cron/deliver-insights/`), `send_insight_digest` at 09:30 IST (`POST /api/cron/insight-digest/`). The nightly `capture_net_worth_snapshot` must keep running: `networth.what_moved` reads it.
3. Turn on `INSIGHTS_ENGINE_USER_IDS` for your own account and look at every surface (`manage.py insights_explain --user-id N` says which rules speak, which are suppressed and why, and what each page would show; it changes nothing).
4. Set `INSIGHTS_ENGINE_MODE=shadow` with `INSIGHTS_SHADOW_PERCENT=10` for a week. Read the `insight shadow rule_id=... delta=exact|close|far|shape` log lines (or the `shadow` analytics events): per rule, how often the engine agrees with the legacy dashboard, with no amounts in them. There is no report command; the golden tests in `test_shadow_parity.py` compare the values. The approved decisions (D1 to D13 in the plan) say which differences are intended.
5. Set `INSIGHTS_ENGINE_MODE=on`. Leave `INSIGHTS_CHANNELS` empty for the first days (pages only), then add `bell`, then `push`, then `email`, one at a time.
6. To roll back at any step: set the mode to `off` (or remove a channel). Stored rows are only read when the engine is on; nothing needs undoing.

## What a person can control

Settings, Profile, Insights: on or off, topics, how often the bell mentions things (normally, quietly, only on pages), push, their own cushion target (3 to 24 months of spending), and the Pro email digest (weekly, daily, never). Any digest email has an unsubscribe link that works signed out and changes only the digest.

## Cost

A page that shows insights reads one query. A dashboard visit may re-run the quick rules once (about 15 queries, at most every ten minutes, only when data changed). The nightly run is about 32 queries for someone with everything. A person with no loans, cards, recurring payments, goals or holdings pays nothing for those rules. The Analytics sentences reuse what the tab loaded; the one new query is a grouped category query on the Net worth tab. The monthly report, Year in Review and digest evaluate or read one surface once.

## Files changed

`git diff --name-status main...HEAD` is the full list. By area:

* **New package `expenses/insights/`**: `types`, `registry`, `context`, `pagectx`, `engine`, `composers`, `prefs`, `gating`, `format`, `labels`, `telemetry`, `tone`, `msgids`, `shadow`, `sampling`, `views`, `context_processors`, `adapters/{bell,digest}`, and `rules/{accounts,analytics,budgets,capital_events,cashflow,expenses,goals,holdings,income,loans,networth,recurring,schedule,transfers}`; `README.md`.
* **New elsewhere**: `expenses/goal_progress.py`, `expenses/management/commands/{generate_insights,deliver_insights,send_insight_digest,insights_explain}.py`, `expenses/templatetags/insights_tags.py`, `scripts/generate_insights_rules_doc.py`, `update_insight_translations.py`, the migration, the templates below, and the tests in `expenses/tests/insights/` (plus `test_dashboard_d13_fixes.py`).
* **Changed existing code** (each small and covered by tests): `models.py` (the two models and fields), `signals.py` (mark insights stale on writes), `urls.py`, `settings.py` (the four settings and the context processor), `views/dashboard.py` (the panel replaces the legacy cards when on; the defects fixed first, D13), `analytics/{facts,common,cashflow,wealth}.py` (facts additions; the sentences call the engine), `calendar_data.py` (one public alias), `budget_suggestions.py` (the shared pick), `wrapped.py` (Year in Review card), `management/commands/send_notifications.py` (the goal marks come from `goal_progress`; behaviour unchanged) and `send_monthly_report.py` (Pro section).
* **Templates**: `home.html`; `partials/_insight_{cards,strip,body,action,action_link,options,thanks}.html` and `_insights_settings.html`; strips added to the Budgets, Expenses, Subscriptions, Loans, Accounts, Income, Goals, Transfers, Holdings, Capital events and Calendar pages; `emails/insight_digest.html` and `expenses/insight_digest_unsubscribe.html`; the monthly report email, the Year in Review page and the Net worth tab each gain a small block; the Calendar page lost its "Back to Dashboard" button and the month arrows lost their border (`static/css/calendar.css`).
* **Translations and docs**: Hindi and Marathi `.po` files (appended by `update_insight_translations.py`), `guide-src/18-insights/` in three languages, `mkdocs.yml`, `static/vendor/bootstrap/bootstrap.purged.min.css` (regenerated).

## Ideas that did not fit

* **Peer comparison and benchmarks** ("people like you"): left out on purpose; every insight compares a person only with their own history.
* **A target for investing** (`transfers.invested_share` compares with your own average because no target field exists), and per-goal contribution reminders.
* **Insights for the Import module** (the import preview already explains its own decisions) and for transfers beyond the invested share.
* **A "why am I seeing this" sheet** that lists the evidence figures for any insight in one tap (the data is already stored with each one).
* **Per-rule on/off in Settings** (today: per topic, plus two "not helpful" votes mute a rule for 90 days).
* **A real daily digest preview** in Settings, and an in-app inbox of everything already emailed.
* **Year in Review lines for past years** are worked out as at the last day of the year from today's data, so a past year's net worth line is absent (no snapshot); storing a yearly snapshot would fill it in.
* **`recurring.similar_services`** (cut: it needed a service catalogue we do not have).
