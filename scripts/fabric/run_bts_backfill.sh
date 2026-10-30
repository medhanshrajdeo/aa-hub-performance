#!/bin/bash
# Backfill nb_bronze_bts across every BTS month from --from-year/--from-month
# through --to-year/--to-month (inclusive). For each month: regenerate the
# notebook definition with that month baked in (see
# build_bts_month_notebook.py and docs/decisions/0005), push it, run it,
# and poll to completion before moving to the next month -- sequential on
# purpose, since a trial/small Fabric capacity has limited concurrent Spark
# capacity. Continues past a failed month rather than aborting the whole
# backfill; failures are collected and printed at the end.
#
# Usage:
#   scripts/fabric/run_bts_backfill.sh <workspaceId> <notebookItemId> \
#       <fromYear> <fromMonth> <toYear> <toMonth> [--reset-first]
#
# --reset-first is REQUIRED if this invocation's first month should wipe
# and recreate the bronze table (true fresh start, e.g. Jan 2023 of a full
# backfill). Omit it when RESUMING a backfill that already has data for
# earlier months -- the flag must never be inferred from "this is the
# first month of THIS invocation," since that silently wipes prior months
# when resuming a partial run. (This is exactly the bug that wiped a real
# Jan 2023 partition during development -- see
# docs/decisions/0005-bts-backfill-mechanism.md.)
#
# Example (a true fresh full backfill, Jan 2023 - Aug 2026):
#   scripts/fabric/run_bts_backfill.sh \
#       16b2a9c6-6418-485f-865f-c47442b8b6c6 0352fa28-34f1-4ef7-b669-6909bff248f2 \
#       2023 1 2026 8 --reset-first
#
# Example (resuming from March 2023 onward, NOT wiping Jan/Feb):
#   scripts/fabric/run_bts_backfill.sh \
#       16b2a9c6-6418-485f-865f-c47442b8b6c6 0352fa28-34f1-4ef7-b669-6909bff248f2 \
#       2023 3 2026 8

set -u

WS_ID="$1"
ITEM_ID="$2"
FROM_YEAR="$3"
FROM_MONTH="$4"
TO_YEAR="$5"
TO_MONTH="$6"
RESET_FIRST="${7:-}"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
NOTEBOOK_DIR="$REPO_ROOT/fabric/nb_bronze_bts.Notebook"
LOG_FILE="$REPO_ROOT/docs/fabric_backfill_log.csv"
UV="/c/Users/medha/.local/bin/uv.exe"

mkdir -p "$(dirname "$LOG_FILE")"
if [ ! -f "$LOG_FILE" ]; then
  echo "year,month,status,row_count_note,job_instance_id" > "$LOG_FILE"
fi

get_status() {
  az rest --method GET \
    --url "https://api.fabric.microsoft.com/v1/workspaces/$WS_ID/items/$ITEM_ID/jobs/instances/$1" \
    --resource "https://api.fabric.microsoft.com" 2>/dev/null \
    | "$UV" run --project "$REPO_ROOT" python -c "import json,sys; print(json.load(sys.stdin).get('status',''))" 2>/dev/null
}

# No jq available; parse with python instead of fragile grep/sed, and
# filter to jobType==RunNotebook specifically -- a RunNotebookInteractive
# entry (e.g. from someone clicking Run in the portal concurrently) must
# not be mistaken for the job this script just triggered.
get_latest_run_notebook_job_id() {
  az rest --method GET \
    --url "https://api.fabric.microsoft.com/v1/workspaces/$WS_ID/items/$ITEM_ID/jobs/instances" \
    --resource "https://api.fabric.microsoft.com" 2>/dev/null \
    | "$UV" run --project "$REPO_ROOT" python -c "
import json, sys
data = json.load(sys.stdin).get('value', [])
runs = [j for j in data if j.get('jobType') == 'RunNotebook']
runs.sort(key=lambda j: j.get('startTimeUtc') or '', reverse=True)
print(runs[0]['id'] if runs else '')
" 2>/dev/null
}

failed_months=()
is_first=1

year="$FROM_YEAR"
month="$FROM_MONTH"
while :; do
  reset_flag=""
  if [ "$is_first" -eq 1 ] && [ "$RESET_FIRST" == "--reset-first" ]; then
    reset_flag="--reset"
  fi

  echo "=== $year-$(printf '%02d' "$month") (reset=$([ -n "$reset_flag" ] && echo true || echo false)) ==="

  "$UV" run --project "$REPO_ROOT" python "$REPO_ROOT/scripts/fabric/build_bts_month_notebook.py" \
    --year "$year" --month "$month" $reset_flag --out "$NOTEBOOK_DIR"

  "$UV" run --project "$REPO_ROOT" python "$REPO_ROOT/scripts/fabric/push_notebook.py" "$NOTEBOOK_DIR"

  az rest --method POST \
    --url "https://api.fabric.microsoft.com/v1/workspaces/$WS_ID/notebooks/$ITEM_ID/updateDefinition" \
    --resource "https://api.fabric.microsoft.com" \
    --headers "Content-Type=application/json" \
    --body @"$NOTEBOOK_DIR/.push/update_body.json" > /dev/null 2>&1

  az rest --method POST \
    --url "https://api.fabric.microsoft.com/v1/workspaces/$WS_ID/items/$ITEM_ID/jobs/RunNotebook/instances" \
    --resource "https://api.fabric.microsoft.com" > /dev/null 2>&1

  sleep 3
  job_id=$(get_latest_run_notebook_job_id)

  echo "job instance: $job_id"
  status=""
  empty_count=0
  while [[ "$status" != "Completed" && "$status" != "Failed" && "$status" != "Cancelled" && "$status" != "Deduped" ]]; do
    sleep 20
    status=$(get_status "$job_id")
    if [ -z "$status" ]; then
      empty_count=$((empty_count + 1))
      # transient API hiccup, not a real terminal state -- keep polling
      # unless it persists for ~5 consecutive checks (~100s)
      if [ "$empty_count" -ge 5 ]; then
        status="Unknown"
      fi
    else
      empty_count=0
    fi
  done
  echo "$year-$(printf '%02d' "$month"): $status"
  echo "$year,$month,$status,,$job_id" >> "$LOG_FILE"

  if [[ "$status" != "Completed" ]]; then
    failed_months+=("$year-$(printf '%02d' "$month")")
  fi

  is_first=0

  if [ "$year" -eq "$TO_YEAR" ] && [ "$month" -eq "$TO_MONTH" ]; then
    break
  fi
  month=$((month + 1))
  if [ "$month" -gt 12 ]; then
    month=1
    year=$((year + 1))
  fi
done

echo ""
echo "=== backfill done ==="
if [ "${#failed_months[@]}" -eq 0 ]; then
  echo "all months completed successfully"
else
  echo "FAILED months: ${failed_months[*]}"
fi
