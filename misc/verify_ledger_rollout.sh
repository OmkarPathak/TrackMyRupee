#!/usr/bin/env bash

# Usage:
# Preflight only, no writes: READ_ONLY=1 misc/verify_ledger_rollout.sh

# Preflight as-if ledger read is enabled (without changing persisted prod env):
# READ_ONLY=1 FORCE_LEDGER_READ_ENABLED=1 REQUIRE_LEDGER_READ_ENABLED=1 misc/verify_ledger_rollout.sh

# Full mutating verification run (ops window):
# FORCE_LEDGER_READ_ENABLED=1 REQUIRE_LEDGER_READ_ENABLED=1 misc/verify_ledger_rollout.sh

set -euo pipefail

# One-shot ledger rollout verifier.
# - Runs backfill/reconcile/maintenance checks
# - Enforces strict reconcile gates
# - Checks posting failure counts
# - Prints final GO/NO-GO verdict

THRESHOLD="${THRESHOLD:-0.01}"
BACKFILL_LIMIT="${BACKFILL_LIMIT:-10000}"
SHADOW_RETRY_LIMIT="${SHADOW_RETRY_LIMIT:-200}"
MAX_FAILED_POSTINGS="${MAX_FAILED_POSTINGS:-0}"
MAX_DRIFTS_24H="${MAX_DRIFTS_24H:-0}"
SKIP_APPLY_BACKFILL="${SKIP_APPLY_BACKFILL:-0}"
FORCE_LEDGER_READ_ENABLED="${FORCE_LEDGER_READ_ENABLED:-0}"
REQUIRE_LEDGER_READ_ENABLED="${REQUIRE_LEDGER_READ_ENABLED:-0}"
READ_ONLY="${READ_ONLY:-0}"

if [[ ! -f "manage.py" ]]; then
  echo "ERROR: Run this script from the Django project root (manage.py not found)."
  exit 2
fi

print_section() {
  echo
  echo "== $1 =="
}

run_step() {
  local title="$1"
  shift
  print_section "$title"
  "$@"
}

run_manage() {
  if [[ "$FORCE_LEDGER_READ_ENABLED" == "1" ]]; then
    LEDGER_READ_ENABLED=true "$@"
  else
    "$@"
  fi
}

run_step "Ledger flags snapshot" \
  run_manage python manage.py shell -c "import json; from django.conf import settings; print(json.dumps({'WRITE': settings.LEDGER_WRITE_ENABLED, 'READ': settings.LEDGER_READ_ENABLED, 'COHORT_PERCENT': settings.LEDGER_READ_COHORT_PERCENT, 'COMPARE': settings.LEDGER_READ_COMPARE_ENABLED, 'COMPARE_SAMPLE': settings.LEDGER_READ_COMPARE_SAMPLE_RATE, 'ENFORCE_BALANCED': settings.LEDGER_ENFORCE_BALANCED_WRITE}))"

READ_EFFECTIVE="$(run_manage python manage.py shell -c "from django.conf import settings; print('1' if settings.LEDGER_READ_ENABLED else '0')")"
if [[ "$READ_EFFECTIVE" != "1" ]]; then
  echo "NOTE: Effective LEDGER_READ_ENABLED=false for this run."
  echo "Tip: use FORCE_LEDGER_READ_ENABLED=1 to simulate future ledger-read rollout."
fi

if [[ "$REQUIRE_LEDGER_READ_ENABLED" == "1" && "$READ_EFFECTIVE" != "1" ]]; then
  echo "VERDICT: NO-GO"
  echo "Reason: REQUIRE_LEDGER_READ_ENABLED=1 but effective LEDGER_READ_ENABLED=false."
  exit 1
fi

run_step "Migrations plan" run_manage python manage.py migrate --plan

if [[ "$READ_ONLY" == "1" ]]; then
  print_section "Apply migrations"
  echo "SKIPPED (READ_ONLY=1)"
else
  run_step "Apply migrations" run_manage python manage.py migrate
fi

run_step "Opening-balance backfill dry-run" \
  run_manage python manage.py backfill_ledger_opening_balances --dry-run --limit "$BACKFILL_LIMIT"

if [[ "$READ_ONLY" == "1" ]]; then
  print_section "Opening-balance backfill apply"
  echo "SKIPPED (READ_ONLY=1)"
elif [[ "$SKIP_APPLY_BACKFILL" != "1" ]]; then
  run_step "Opening-balance backfill apply" \
    run_manage python manage.py backfill_ledger_opening_balances --limit "$BACKFILL_LIMIT"
else
  print_section "Opening-balance backfill apply"
  echo "SKIPPED (SKIP_APPLY_BACKFILL=1)"
fi

run_step "Read cohort status" run_manage python manage.py ledger_rollout_status

if [[ "$READ_ONLY" == "1" ]]; then
  READONLY_RECON_PREVIEW_CODE="$(cat <<PY
from decimal import Decimal
from django.db.models import Sum
from django.db.models.functions import Coalesce
from expenses.models import Account, JournalEntry, JournalLine

threshold = Decimal('${THRESHOLD}')
accounts = list(Account.objects.filter(is_active=True).select_related('user'))
user_ids = {a.user_id for a in accounts}
opening_ids = set()

if user_ids:
  vals = JournalEntry.objects.filter(
    user_id__in=user_ids,
    source_type='ADJUSTMENT',
    status='POSTED',
    metadata__has_key='opening_account_id',
  ).values_list('metadata__opening_account_id', flat=True)
  opening_ids = {int(v) for v in vals if v is not None}

drifts = 0
missing = 0
total = 0

for a in accounts:
  total += 1
  if a.id not in opening_ids:
    missing += 1

  debit = JournalLine.objects.filter(
    account_ref=a,
    direction='DEBIT',
    journal_entry__status='POSTED',
  ).aggregate(total=Coalesce(Sum('amount'), Decimal('0.00'))).get('total', Decimal('0.00'))
  credit = JournalLine.objects.filter(
    account_ref=a,
    direction='CREDIT',
    journal_entry__status='POSTED',
  ).aggregate(total=Coalesce(Sum('amount'), Decimal('0.00'))).get('total', Decimal('0.00'))

  ledger = (debit - credit).quantize(Decimal('0.01'))
  model = a.balance.quantize(Decimal('0.01'))
  drift = (model - ledger).quantize(Decimal('0.01'))
  if abs(drift) > threshold:
    drifts += 1

print({'accounts': total, 'drifts': drifts, 'missing_opening_entries': missing})
PY
)"

  run_step "Read-only reconciliation gate preview" \
  run_manage python manage.py shell -c "$READONLY_RECON_PREVIEW_CODE"
else
  run_step "Strict reconciliation gate" \
    run_manage python manage.py reconcile_ledgers --threshold "$THRESHOLD" --fail-on-drift --require-opening-balances
fi

if [[ "$READ_ONLY" == "1" ]]; then
  print_section "Maintenance reconcile pass"
  echo "SKIPPED (READ_ONLY=1)"
else
  run_step "Maintenance reconcile pass" \
    run_manage python manage.py run_ledger_maintenance --reconcile --threshold "$THRESHOLD"
fi

if [[ "$READ_ONLY" == "1" ]]; then
  print_section "Shadow posting retries"
  echo "SKIPPED (READ_ONLY=1)"
else
  run_step "Shadow posting retries (pass 1)" \
    run_manage python manage.py retry_ledger_shadow_failures --limit "$SHADOW_RETRY_LIMIT"

  run_step "Shadow posting retries (pass 2)" \
    run_manage python manage.py retry_ledger_shadow_failures --limit "$SHADOW_RETRY_LIMIT"
fi

print_section "Health summary and policy gates"
SUMMARY_JSON="$(run_manage python manage.py shell -c "from expenses.models import LedgerPostingFailure, LedgerReconciliationReport; from django.utils import timezone; from datetime import timedelta; now=timezone.now(); day=now-timedelta(hours=24); import json; print(json.dumps({'posting_failures_pending': LedgerPostingFailure.objects.filter(status='PENDING').count(), 'posting_failures_failed': LedgerPostingFailure.objects.filter(status='FAILED').count(), 'reconcile_rows_24h': LedgerReconciliationReport.objects.filter(created_at__gte=day).count(), 'reconcile_drifts_24h': LedgerReconciliationReport.objects.filter(created_at__gte=day, status='DRIFT').count()}))")"
echo "$SUMMARY_JSON"

FAILED_COUNT="$(echo "$SUMMARY_JSON" | python -c "import sys, json; print(json.load(sys.stdin)['posting_failures_failed'])")"
DRIFT_24H_COUNT="$(echo "$SUMMARY_JSON" | python -c "import sys, json; print(json.load(sys.stdin)['reconcile_drifts_24h'])")"

POLICY_FAILED=0
if (( FAILED_COUNT > MAX_FAILED_POSTINGS )); then
  echo "POLICY FAIL: posting_failures_failed=$FAILED_COUNT exceeds MAX_FAILED_POSTINGS=$MAX_FAILED_POSTINGS"
  POLICY_FAILED=1
fi

if (( DRIFT_24H_COUNT > MAX_DRIFTS_24H )); then
  echo "POLICY FAIL: reconcile_drifts_24h=$DRIFT_24H_COUNT exceeds MAX_DRIFTS_24H=$MAX_DRIFTS_24H"
  POLICY_FAILED=1
fi

echo
if (( POLICY_FAILED == 1 )); then
  echo "VERDICT: NO-GO"
  echo "Recommended immediate fallback: set LEDGER_READ_ENABLED=false and redeploy."
  exit 1
fi

echo "VERDICT: GO"
echo "Ledger read rollout checks passed under current thresholds."
