#!/usr/bin/env bash
# Full pipeline: bootstrap a list of GitHub repos into evaluation
# datasets, then for each: clear vectordb, ingest project docs,
# run secreqgen evaluate compare-runs --pilot, save results.
#
# Designed to run unattended for several hours. Each project is
# self-contained — failure of one project does not abort the rest.
#
# Usage:
#   bash scripts/run_full_eval_v3.sh                 # all targets in repo_targets.txt
#   bash scripts/run_full_eval_v3.sh owner/repo      # one repo
#   FORCE=1 bash scripts/run_full_eval_v3.sh         # re-bootstrap even if exists
#
set -uo pipefail

REPO="/Users/georgeg/Documents/Uni Docs/Thesis/coded_project/secreqgen"
TEST_CASES="/Users/georgeg/Documents/Uni Docs/Thesis/coded_project/real_test_cases"
MODEL="${MODEL:-qwen3:30b-a3b-instruct-2507-q4_K_M}"
TARGETS_FILE="${TARGETS_FILE:-$REPO/scripts/repo_targets.txt}"
FORCE_FLAG=""
[ "${FORCE:-0}" = "1" ] && FORCE_FLAG="--force"

cd "$REPO"
export PYTHONPATH="$REPO/src"

# Ensure the .pth hidden-flag bug is worked around for this shell session.
PTH=".venv/lib/python3.13/site-packages/secreqgen.pth"
if [ -f "$PTH" ] && ls -lO@ "$PTH" 2>/dev/null | grep -q hidden; then
  c=$(cat "$PTH"); rm "$PTH"; printf '%s\n' "$c" > "$PTH"
fi

echo "═══════════════════════════════════════════════════════════════"
echo " SecReqGen full pipeline v3  ($(date '+%Y-%m-%d %H:%M:%S'))"
echo "═══════════════════════════════════════════════════════════════"
echo " Model: $MODEL"
echo " Targets: $TARGETS_FILE"
echo

# ─── Phase 1: bootstrap datasets for all targets ──────────────────────
if [ "$#" -gt 0 ]; then
  TARGETS_ARGS="$@"
  echo "[phase 1] bootstrapping ${#@} repos provided as args"
  poetry run python "$REPO/scripts/bootstrap_dataset.py" $FORCE_FLAG $TARGETS_ARGS
else
  if [ ! -f "$TARGETS_FILE" ]; then
    echo "FATAL: $TARGETS_FILE not found"; exit 1
  fi
  count=$(grep -vE '^\s*(#|$)' "$TARGETS_FILE" | wc -l | tr -d ' ')
  echo "[phase 1] bootstrapping $count repos from $TARGETS_FILE"
  poetry run python "$REPO/scripts/bootstrap_dataset.py" $FORCE_FLAG --from "$TARGETS_FILE"
fi

echo
echo "═══ phase 1 done; entering phase 2 (per-project pilots) ═══"
echo

# ─── Phase 2: for each bootstrapped project, ingest + pilot ──────────
# Walk every project dir in real_test_cases that has BOTH
# user_stories.json AND ground_truth.json AND a docs/ subdir we can
# ingest from.
SUCCEEDED=0
FAILED=0
SKIPPED=0
for proj_dir in "$TEST_CASES"/*; do
  [ -d "$proj_dir" ] || continue
  proj=$(basename "$proj_dir")

  # Skip the *-test-data and extracted_docs* helper dirs
  case "$proj" in
    *-test-data|extracted_docs|extracted_docs_md|repos|isolated_evaluation_summary.json|phase*|run_*|Examples.md)
      continue ;;
  esac

  US="$proj_dir/user_stories.json"
  GT="$proj_dir/ground_truth.json"
  CTX="$proj_dir/system_context.yaml"
  [ -f "$US" ] && [ -f "$GT" ] && [ -f "$CTX" ] || { echo "[skip] $proj — missing one of (user_stories, ground_truth, system_context)"; SKIPPED=$((SKIPPED+1)); continue; }

  OUT="$proj_dir/results_compare_runs_pilot_v3"
  if [ -d "$OUT" ] && [ -z "$FORCE_FLAG" ]; then
    echo "[skip] $proj — results_compare_runs_pilot_v3 already exists (FORCE=1 to redo)"
    SKIPPED=$((SKIPPED+1)); continue
  fi

  echo
  echo "═════════════════════════════════════════════════════════════"
  echo "  $proj  ($(date '+%H:%M:%S'))"
  echo "═════════════════════════════════════════════════════════════"

  # Pick the docs source: prefer the project's own docs/ if bootstrap
  # produced one, else fall back to *-test-data, extracted_docs_md, or
  # extracted_docs.
  DOCS_DIR=""
  for cand in "$proj_dir/docs" "$TEST_CASES/${proj}-test-data" "$TEST_CASES/extracted_docs_md/$proj" "$TEST_CASES/extracted_docs/$proj"; do
    [ -d "$cand" ] && [ -n "$(find "$cand" -type f \( -name '*.md' -o -name '*.rst' -o -name '*.txt' \) 2>/dev/null | head -1)" ] && DOCS_DIR="$cand" && break
  done
  if [ -z "$DOCS_DIR" ]; then
    echo "[skip] $proj — no doc source dir for ingestion"
    SKIPPED=$((SKIPPED+1)); continue
  fi
  echo "[$proj] doc source: $DOCS_DIR"

  echo "[$proj] clearing vectordb"
  poetry run secreqgen ingest clear --force >/dev/null 2>&1 || true

  echo "[$proj] ingesting docs"
  if ! poetry run secreqgen ingest documents "$DOCS_DIR" --type architecture 2>&1 | tail -5; then
    echo "[$proj] ingestion failed"; FAILED=$((FAILED+1)); continue
  fi

  echo "[$proj] ingesting system context"
  poetry run secreqgen ingest system-context "$CTX" 2>&1 | tail -3 || true

  echo "[$proj] running pilot (N=10 paired)"
  if poetry run secreqgen evaluate compare-runs \
      "$US" "$GT" \
      --pilot \
      --context "$CTX" \
      --model "$MODEL" \
      --output "$OUT" 2>&1 | tail -25; then
    SUCCEEDED=$((SUCCEEDED+1))
  else
    FAILED=$((FAILED+1))
  fi
done

echo
echo "═══════════════════════════════════════════════════════════════"
echo " ALL DONE  ($(date '+%Y-%m-%d %H:%M:%S'))"
echo "  succeeded: $SUCCEEDED"
echo "  failed:    $FAILED"
echo "  skipped:   $SKIPPED"
echo "═══════════════════════════════════════════════════════════════"
