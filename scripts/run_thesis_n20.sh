#!/usr/bin/env bash
# Path A driver — thesis-grade N=20 paired evaluation of the 10 hand-
# curated projects. This is the run that gives the per-story Wilcoxon
# (the methodology's PRIMARY test) a real chance of clearing
# significance. Outputs go to results_compare_runs_thesis_n20/ so they
# coexist with the v2 (N=10) and v3 (N=10) results.
#
# Usage:
#   bash scripts/run_thesis_n20.sh                     # all 10 hand-curated
#   bash scripts/run_thesis_n20.sh keycloak bitwarden  # subset
#   FORCE=1 bash scripts/run_thesis_n20.sh             # redo even if results exist
#   N=30 bash scripts/run_thesis_n20.sh                # bump N (e.g., for one project)
#
set -uo pipefail

REPO="/Users/georgeg/Documents/Uni Docs/Thesis/coded_project/secreqgen"
TEST_CASES="/Users/georgeg/Documents/Uni Docs/Thesis/coded_project/real_test_cases"
MODEL="${MODEL:-qwen3:30b-a3b-instruct-2507-q4_K_M}"
N="${N:-20}"
FORCE="${FORCE:-0}"

cd "$REPO"
export PYTHONPATH="$REPO/src"

# Apply the .pth hidden-flag fix if needed (runs every session).
PTH=".venv/lib/python3.13/site-packages/secreqgen.pth"
if [ -f "$PTH" ] && /bin/ls -lO@ "$PTH" 2>/dev/null | grep -q hidden; then
  c=$(cat "$PTH"); rm "$PTH"; printf '%s\n' "$c" > "$PTH"
  echo "[setup] reset .pth hidden flag"
fi

# Order matters: small KB first so we get fast feedback on the weakest
# projects, then the rich-KB projects which take longer per generation.
DEFAULT_PROJECTS=(
  joomla discourse grafana mastodon mattermost gitlab   # 6 newly-fetched, small docs
  nextcloud strapi                                       # 2 small from v2
  bitwarden keycloak                                     # 2 large from v2
)

if [ "$#" -gt 0 ]; then
  PROJECTS=("$@")
else
  PROJECTS=("${DEFAULT_PROJECTS[@]}")
fi

# Per-project doc source resolver (preference order):
#   1. <proj>/docs/                       (the per-project bootstrap result)
#   2. <proj>-test-data/                  (the original hand-curated raw)
#   3. extracted_docs_md/<proj>/          (extracted markdowns)
#   4. extracted_docs/<proj>/             (extracted other)
resolve_docs() {
  local p="$1"
  for cand in \
    "$TEST_CASES/$p/docs" \
    "$TEST_CASES/${p}-test-data" \
    "$TEST_CASES/extracted_docs_md/$p" \
    "$TEST_CASES/extracted_docs/$p"; do
    [ -d "$cand" ] && [ -n "$(find "$cand" -type f \( -name '*.md' -o -name '*.rst' -o -name '*.txt' \) 2>/dev/null | head -1)" ] && { echo "$cand"; return; }
  done
}

echo "═══════════════════════════════════════════════════════════════"
echo " Path A — thesis-grade N=$N pilots  ($(date '+%Y-%m-%d %H:%M:%S'))"
echo "═══════════════════════════════════════════════════════════════"
echo " Model:    $MODEL"
echo " Projects: ${PROJECTS[*]}"
echo

SUCC=0; FAIL=0; SKIP=0
for proj in "${PROJECTS[@]}"; do
  PROJ_DIR="$TEST_CASES/$proj"
  US="$PROJ_DIR/user_stories.json"
  GT="$PROJ_DIR/ground_truth.json"
  CTX="$PROJ_DIR/system_context.yaml"
  OUT="$PROJ_DIR/results_compare_runs_thesis_n${N}"

  [ -f "$US" ] && [ -f "$GT" ] && [ -f "$CTX" ] || {
    echo "[$proj] skip — missing dataset file"; SKIP=$((SKIP+1)); continue
  }
  if [ -d "$OUT" ] && [ "$FORCE" != "1" ]; then
    echo "[$proj] skip — $OUT exists (FORCE=1 to redo)"; SKIP=$((SKIP+1)); continue
  fi

  DOCS_DIR=$(resolve_docs "$proj")
  if [ -z "$DOCS_DIR" ]; then
    echo "[$proj] skip — no doc source dir"; SKIP=$((SKIP+1)); continue
  fi

  echo
  echo "═════════════════════════════════════════════════════════════"
  echo "  $proj  ($(date '+%H:%M:%S'))   docs: $DOCS_DIR"
  echo "═════════════════════════════════════════════════════════════"

  echo "[$proj] clearing vectordb"
  poetry run secreqgen ingest clear --force >/dev/null 2>&1 || true

  echo "[$proj] ingesting docs from $DOCS_DIR"
  poetry run secreqgen ingest documents "$DOCS_DIR" --type architecture 2>&1 | grep -E "Stored|chunks|FAIL" | head

  echo "[$proj] ingesting system context"
  poetry run secreqgen ingest system-context "$CTX" >/dev/null 2>&1 || true

  echo "[$proj] running paired N=$N pilot"
  if poetry run secreqgen evaluate compare-runs \
       "$US" "$GT" \
       --runs "$N" \
       --context "$CTX" \
       --model "$MODEL" \
       --output "$OUT" 2>&1 | tail -25; then
    SUCC=$((SUCC+1))
  else
    FAIL=$((FAIL+1))
  fi
done

echo
echo "═══════════════════════════════════════════════════════════════"
echo " THESIS-GRADE RUN DONE  ($(date '+%Y-%m-%d %H:%M:%S'))"
echo "  succeeded: $SUCC"
echo "  failed:    $FAIL"
echo "  skipped:   $SKIP"
echo "═══════════════════════════════════════════════════════════════"
