# Double-Entry Ledger Production Deployment Runbook

This runbook is for a full production cutover to ledger-backed writes and reads.

Target context:
- Small production footprint (~121 users, ~10 active)
- Existing code includes shadow writes, dead-letter retries, reconciliation, read adapter, opening-balance backfill, and cohort controls

Use this as an operator checklist during deployment.

---

## 1. Pre-Deployment Checklist

1. Confirm branch and commit are final.
2. Confirm tests passed on deployable commit:
   - `expenses.tests.test_ledger_rollout`
   - `expenses.tests.test_ledger_backfill`
   - `expenses.tests.test_ledger_read_service`
   - `expenses.tests.test_ledger_posting`
   - `expenses.tests.test_ledger_shadow_writes`
   - `expenses.tests.test_ledger_ops`
3. Confirm production DB backup strategy is available.
4. Confirm maintenance window (recommended even for small active user base).
5. Confirm rollback env toggle access is available.

---

## 2. Production Environment Variables

Set these in production:

```bash
LEDGER_WRITE_ENABLED=true
LEDGER_ENFORCE_BALANCED_WRITE=false
LEDGER_READ_ENABLED=true
LEDGER_READ_COHORT_PERCENT=100
LEDGER_READ_COHORT_USER_IDS=
LEDGER_READ_EXCLUDE_USER_IDS=
LEDGER_RECONCILE_ENABLED=true
LEDGER_RECONCILE_ALERT_THRESHOLD=10.00
LEDGER_READ_COMPARE_ENABLED=true
LEDGER_READ_COMPARE_SAMPLE_RATE=1.0
```

Notes:
- Keep `LEDGER_READ_COHORT_USER_IDS` and `LEDGER_READ_EXCLUDE_USER_IDS` empty for full rollout.
- Keep `LEDGER_ENFORCE_BALANCED_WRITE=false` during initial cutover and backfill.
- Enable `LEDGER_ENFORCE_BALANCED_WRITE=true` only after backfill and reconciliation checks are healthy, and the async reconciliation workers are fully deployed. **Warning**: if flipped to `true` without workers running, users will see random 500s when temporary ledger write failures occur.
- If urgent rollback is needed, disable read/enforcement first (see rollback section).

---

## 3. Backup (Mandatory)

Run a full DB backup before deploying.

PostgreSQL example:

```bash
pg_dump "$DATABASE_URL" > backup_pre_ledger_cutover_$(date +%F_%H%M%S).sql
```

SQLite example:

```bash
cp db.sqlite3 db_pre_ledger_cutover_$(date +%F_%H%M%S).sqlite3
```

---

## 4. Deploy Code

Use your normal production deployment process.

After deploy artifact is active, run app-level migration/ops commands from the production app shell.

---

## 5. Cutover Commands (In Order)

Run these commands in production:

```bash
source env/bin/activate
python manage.py migrate
python manage.py backfill_ledger_opening_balances --dry-run --limit 10000
python manage.py backfill_ledger_opening_balances --limit 10000
python manage.py ledger_rollout_status
python manage.py run_ledger_maintenance --reconcile --threshold 0.01
```

Expected outcomes:
1. `migrate` applies all pending migrations.
2. `backfill_ledger_opening_balances --dry-run` shows would-create counts.
3. real `backfill_ledger_opening_balances` creates adjustment entries.
4. `ledger_rollout_status` should show near 100% cohort coverage.
5. `run_ledger_maintenance` processes retries + writes reconciliation reports.

### 5.1 Enable Strict Enforcement (After Verification)

Only after section 5 completes successfully and reconciliation drift is acceptable:

```bash
LEDGER_ENFORCE_BALANCED_WRITE=true
```

Then restart/redeploy app processes so the new setting takes effect.

---

## 6. Immediate Post-Deploy Verification

### 6.1 App Flow Smoke Test

In production UI, verify all still work with no UX changes:
1. Add expense
2. Add income
3. Add transfer
4. Delete one transaction of each type (if practical)

### 6.2 Admin/Data Checks

Confirm new rows are being created in:
1. `JournalEntry`
2. `JournalLine`
3. `LedgerReconciliationReport`

Confirm dead-letter health:
1. `LedgerPostingFailure` has no unexpected growth in `PENDING` or `FAILED`.

### 6.3 Commands for Quick Checks

```bash
python manage.py retry_ledger_shadow_failures --limit 200
python manage.py reconcile_ledgers --threshold 0.01
```

---

## 7. Monitoring for First 24-48 Hours

Watch these metrics and logs:
1. Transaction save failures from app logs.
2. Count of `LedgerPostingFailure` by status.
3. Reconciliation drift count and drift magnitude.
4. User-facing support issues around incorrect balances.

Useful periodic command:

```bash
python manage.py run_ledger_maintenance --reconcile --threshold 0.01
```

---

## 8. Scheduler Setup (Recommended)

Set up recurring jobs:

1. Retry failures every 5-10 minutes:
```bash
python manage.py retry_ledger_shadow_failures --limit 200
```

2. Reconcile daily (or every few hours first week):
```bash
python manage.py reconcile_ledgers --threshold 0.01
```

Or run both through:
```bash
python manage.py run_ledger_maintenance --reconcile --threshold 0.01
```

---

## 9. Rollback Plan (Fast)

If incident occurs, first step rollback toggles:

```bash
LEDGER_READ_ENABLED=false
LEDGER_ENFORCE_BALANCED_WRITE=false
```

Then redeploy config/restart app.

Important:
1. Keep `LEDGER_WRITE_ENABLED=true` unless severe corruption risk.
2. Do not delete ledger data during rollback.
3. Continue reconciliation to diagnose mismatch root cause.

---

## 10. Incident Triage Commands

```bash
python manage.py retry_ledger_shadow_failures --limit 500
python manage.py reconcile_ledgers --threshold 0.01
python manage.py ledger_rollout_status
```

If failures keep increasing:
1. disable strict enforcement first
2. keep shadow writes on
3. inspect latest `LedgerPostingFailure.payload` and error traces

---

## 11. Success Criteria (Go/No-Go)

Treat rollout as healthy when:
1. Create/update/delete flows remain user-stable.
2. Dead-letter `FAILED` remains near zero.
3. Reconciliation drift remains within threshold and trends down.
4. No recurring balance mismatch support reports.

---

## 12. Final Cleanup Phase (Later)

After sustained stability:
1. Remove legacy direct read paths.
2. Keep ledger as canonical source of truth.
3. Retain reconciliation and retry tooling as permanent safeguards.
