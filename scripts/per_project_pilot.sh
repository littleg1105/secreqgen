#!/usr/bin/env bash
# Per-project ingest + pilot, for thesis multi-run comparison.
#
# Per project: clear the vectordb, ingest the project's own docs,
# run secreqgen evaluate compare-runs --pilot, save results.
#
# Lives in scripts/ — run from anywhere; resolves paths absolutely.
set -euo pipefail

REPO="/Users/georgeg/Documents/Uni Docs/Thesis/coded_project/secreqgen"
TEST_CASES="/Users/georgeg/Documents/Uni Docs/Thesis/coded_project/real_test_cases"
MODEL="qwen3:30b-a3b-instruct-2507-q4_K_M"

cd "$REPO"
export PYTHONPATH="$REPO/src"

# Per-project doc source paths. Only the 4 projects with ingestable
# material are listed; the other 6 have empty doc dirs and are
# skipped (they would still yield "no-RAG-effect" by construction).
declare -a PROJECTS=(
  "bitwarden|$TEST_CASES/bitwarden-test-data"
  "keycloak|$TEST_CASES/extracted_docs_md/keycloak"
  "nextcloud|$TEST_CASES/extracted_docs/nextcloud"
  "strapi|$TEST_CASES/extracted_docs_md/strapi"
)

run_one() {
  local proj="$1"
  local docs_dir="$2"
  echo
  echo "═════════════════════════════════════════════════════════════"
  echo "  $proj  ($(date +%H:%M:%S))"
  echo "═════════════════════════════════════════════════════════════"

  if [ ! -d "$docs_dir" ]; then
    echo "[skip] docs_dir not found: $docs_dir"
    return 0
  fi

  echo "--- 1. clearing vectordb ---"
  poetry run secreqgen ingest clear --force 2>&1 | tail -3

  echo "--- 2. ingesting $proj docs from $docs_dir ---"
  poetry run secreqgen ingest documents "$docs_dir" --type architecture 2>&1 | tail -10

  echo "--- 3. ingesting $proj system context ---"
  if [ -f "$TEST_CASES/$proj/system_context.yaml" ]; then
    poetry run secreqgen ingest system-context "$TEST_CASES/$proj/system_context.yaml" 2>&1 | tail -3
  fi

  echo "--- 4. running pilot (N=10 paired) ---"
  poetry run secreqgen evaluate compare-runs \
    "$TEST_CASES/$proj/user_stories.json" \
    "$TEST_CASES/$proj/ground_truth.json" \
    --pilot \
    --context "$TEST_CASES/$proj/system_context.yaml" \
    --model "$MODEL" \
    --output "$TEST_CASES/$proj/results_compare_runs_pilot_v2_kb_fixed" 2>&1 | tail -25
}

# Run all projects; on failure, continue with the next one.
for entry in "${PROJECTS[@]}"; do
  proj="${entry%%|*}"
  docs="${entry##*|}"
  if ! run_one "$proj" "$docs"; then
    echo "[warn] $proj failed; continuing"
  fi
done

echo
echo "═══ ALL PROJECTS DONE  $(date +%H:%M:%S) ═══"
