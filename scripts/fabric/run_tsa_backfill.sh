#!/bin/bash
# Backfill nb_bronze_tsa across a list of weekly TSA throughput PDFs.
# For each week: regenerate the notebook definition with that week's URL
# baked in (see build_tsa_week_notebook.py and docs/decisions/0005/0007),
# push it, run it, and poll to completion before moving to the next week
# -- sequential, same reasoning as run_bts_backfill.sh (limited concurrent
# Spark capacity on a trial tier). Each weekly PDF takes ~4 min to parse
# plus Spark/pip-install overhead, so this is a genuinely long run.
#
# Input file format: one week per line, "YYYY-MM-DD<TAB>https://full/pdf/url"
# (see scripts/fabric/filter_tsa_links.py-style output, or
# docs/fabric_backfill_tsa_weeks.txt).
#
# Usage:
#   scripts/fabric/run_tsa_backfill.sh <workspaceId> <notebookItemId> \
#       <weeksFile> [--reset-first] [--skip-weeks "2026-01-04,2026-01-11"]

set -u

WS_ID="$1"
ITEM_ID="$2"
WEEKS_FILE="$3"
shift 3

RESET_FIRST=""
SKIP_WEEKS=""
while [ $# -gt 0 ]; do
  case "$1" in
    --reset-first) RESET_FIRST="--reset-first" ;;
    --skip-weeks) shift; SKIP_WEEKS="$1" ;;
  esac
  shift
done

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
NOTEBOOK_DIR="$REPO_ROOT/fabric/nb_bronze_tsa.Notebook"
LOG_FILE="$REPO_ROOT/docs/fabric_backfill_tsa_log.csv"
UV="/c/Users/medha/.local/bin/uv.exe"

mkdir -p "$(dirname "$LOG_FILE")"
if [ ! -f "$LOG_FILE" ]; then
  echo "week_start,status,job_instance_id" > "$LOG_FILE"
fi

get_status() {
  az rest --method GET \
    --url "https://api.fabric.microsoft.com/v1/workspaces/$WS_ID/items/$ITEM_ID/jobs/instances/$1" \
    --resource "https://api.fabric.microsoft.com" 2>/dev/null \
    | "$UV" run --project "$REPO_ROOT" python -c "import json,sys; print(json.load(sys.stdin).get('status',''))" 2>/dev/null
}

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

failed_weeks=()
is_first=1

while IFS=$'\t' read -r week_start pdf_url; do
  [ -z "$week_start" ] && continue
  if [[ ",$SKIP_WEEKS," == *",$week_start,"* ]]; then
    echo "=== $week_start: skipping (already loaded) ==="
    is_first=0
    continue
  fi

  reset_flag=""
  if [ "$is_first" -eq 1 ] && [ "$RESET_FIRST" == "--reset-first" ]; then
    reset_flag="--reset"
  fi

  echo "=== $week_start (reset=$([ -n "$reset_flag" ] && echo true || echo false)) ==="

  "$UV" run --project "$REPO_ROOT" python "$REPO_ROOT/scripts/fabric/build_tsa_week_notebook.py" \
    --week-start "$week_start" --pdf-url "$pdf_url" $reset_flag --out "$NOTEBOOK_DIR"

  "$UV" run --project "$REPO_ROOT" python "$REPO_ROOT/scripts/fabric/push_notebook.py" "$NOTEBOOK_DIR"

  az rest --method POST \
    --url "https://api.fabric.microsoft.com/v1/workspaces/$WS_ID/notebooks/$ITEM_ID/updateDefinition" \
    --resource "https://api.fabric.microsoft.com" \
    --headers "Content-Type=application/json" \
    --body @"$NOTEBOOK_DIR/.push/update_body.json" > /dev/null 2>&1

  # settle time between definition update and triggering a run -- a run
  # triggered immediately after updateDefinition may have picked up stale
  # content in earlier testing; this is cheap insurance against that.
  sleep 10

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
      if [ "$empty_count" -ge 5 ]; then
        status="Unknown"
      fi
    else
      empty_count=0
    fi
  done

  echo "$week_start: $status"
  echo "$week_start,$status,$job_id" >> "$LOG_FILE"

  if [[ "$status" != "Completed" ]]; then
    failed_weeks+=("$week_start")
  fi

  is_first=0
done < "$WEEKS_FILE"

echo ""
echo "=== TSA backfill done ==="
if [ "${#failed_weeks[@]}" -eq 0 ]; then
  echo "all weeks completed successfully"
else
  echo "FAILED weeks: ${failed_weeks[*]}"
fi
