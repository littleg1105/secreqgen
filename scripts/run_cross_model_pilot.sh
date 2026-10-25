#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────
# Model-agnostic cross-model pilot driver for the SecReqGen thesis.
#
# Runs the multi-run paired-statistics evaluation pipeline for ANY
# Ollama-served model on a configurable subset of hand-curated projects.
# Used for the thesis's cross-model sub-tests (qwen3.6, gemma4,
# mistral-small3.2, etc.).
#
# REQUIRED ENV:
#   MODEL              The Ollama model tag (e.g. "qwen3.6:latest",
#                      "gemma4:26b", "mistral-small3.2:24b").
#
# OPTIONAL ENV (with sensible defaults):
#   LABEL              Short slug for log lines & output-dir conventions.
#                      Derived from MODEL if unset (lowercased, special
#                      chars stripped). Used in:
#                          [LABEL] project  (...)
#                          [LABEL/project] running paired pilot
#                          LABEL SUB-TEST DONE
#   N                  Number of paired runs per project. Default: 10.
#   FORCE              If "1", redo projects even when results dir exists.
#   OUTPUT_DIR_NAME    Project subdirectory for results. Default derived
#                      from LABEL+N: "results_compare_runs_${LABEL}_n${N}".
#
# POSITIONAL ARGS (optional):
#   Project names to run. If omitted, runs the DEFAULT_PROJECTS list.
#
# Usage:
#   MODEL=gemma4:26b  bash scripts/run_cross_model_pilot.sh
#   MODEL=gemma4:26b  bash scripts/run_cross_model_pilot.sh keycloak mastodon
#   MODEL=mistral-small3.2:24b  LABEL=mistral_small_32  N=10  bash scripts/run_cross_model_pilot.sh
#   FORCE=1  MODEL=qwen3.6:latest  bash scripts/run_cross_model_pilot.sh   # redo
#
# DEPRECATED but still working: `scripts/run_thesis_n20_qwen36.sh` is a
# thin shim that just sets MODEL/LABEL defaults to qwen3.6 values and
# delegates here. New work should call this script directly.
# ─────────────────────────────────────────────────────────────────────
set -uo pipefail

REPO="/Users/georgeg/Documents/Uni Docs/Thesis/coded_project/secreqgen"
TEST_CASES="/Users/georgeg/Documents/Uni Docs/Thesis/coded_project/real_test_cases"

# ─── Parameter resolution ───────────────────────────────────────────
if [ -z "${MODEL:-}" ]; then
  echo "FATAL: MODEL env var is required (e.g. MODEL=gemma4:26b)" >&2
  echo "       See script header for full usage." >&2
  exit 2
fi

# Derive LABEL from MODEL if not provided: lowercase, replace : / . with _.
# E.g. "qwen3.6:latest" → "qwen3_6_latest"; "gemma4:26b" → "gemma4_26b".
default_label_from_model() {
  echo "$1" | tr '[:upper:]' '[:lower:]' | sed -E 's/[^a-z0-9]+/_/g; s/^_+|_+$//g'
}
LABEL="${LABEL:-$(default_label_from_model "$MODEL")}"

N="${N:-10}"
FORCE="${FORCE:-0}"
OUTPUT_DIR_NAME="${OUTPUT_DIR_NAME:-results_compare_runs_${LABEL}_n${N}}"

cd "$REPO"
export PYTHONPATH="$REPO/src"

# Apply the .pth hidden-flag fix if needed (macOS quirk; runs every session).
PTH=".venv/lib/python3.13/site-packages/secreqgen.pth"
if [ -f "$PTH" ] && /bin/ls -lO@ "$PTH" 2>/dev/null | grep -q hidden; then
  c=$(cat "$PTH"); rm "$PTH"; printf '%s\n' "$c" > "$PTH"
  echo "[setup] reset .pth hidden flag"
fi

# ─── Default subset, ordered smallest KB first ──────────────────────
# These four match the original qwen3.6 sub-test design: one project
# per outcome category from the qwen3 baseline (STRONG +, STRONG −,
# null, STRONG + anchor).
DEFAULT_PROJECTS=(mastodon mattermost bitwarden keycloak)

if [ "$#" -gt 0 ]; then
  PROJECTS=("$@")
else
  PROJECTS=("${DEFAULT_PROJECTS[@]}")
fi

# ─── Per-project doc source resolver ─────────────────────────────────
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
echo " CROSS-MODEL PILOT — $LABEL"
echo " $(date '+%Y-%m-%d %H:%M:%S')"
echo "═══════════════════════════════════════════════════════════════"
echo " Model:    $MODEL"
echo " Label:    $LABEL"
echo " N runs:   $N (paired)"
echo " Projects: ${PROJECTS[*]}"
echo " Outputs:  real_test_cases/<proj>/$OUTPUT_DIR_NAME/"
echo

# ─── Per-project loop ───────────────────────────────────────────────
SUCC=0; FAIL=0; SKIP=0
for proj in "${PROJECTS[@]}"; do
  PROJ_DIR="$TEST_CASES/$proj"
  US="$PROJ_DIR/user_stories.json"
  GT="$PROJ_DIR/ground_truth.json"
  CTX="$PROJ_DIR/system_context.yaml"
  OUT="$PROJ_DIR/$OUTPUT_DIR_NAME"

  [ -f "$US" ] && [ -f "$GT" ] && [ -f "$CTX" ] || {
    echo "[$LABEL/$proj] skip — missing dataset file"; SKIP=$((SKIP+1)); continue
  }
  if [ -d "$OUT" ] && [ "$FORCE" != "1" ]; then
    echo "[$LABEL/$proj] skip — $OUT exists (FORCE=1 to redo)"; SKIP=$((SKIP+1)); continue
  fi

  DOCS_DIR=$(resolve_docs "$proj")
  if [ -z "$DOCS_DIR" ]; then
    echo "[$LABEL/$proj] skip — no doc source dir"; SKIP=$((SKIP+1)); continue
  fi

  echo
  echo "═════════════════════════════════════════════════════════════"
  echo "  [$LABEL] $proj  ($(date '+%H:%M:%S'))   docs: $DOCS_DIR"
  echo "═════════════════════════════════════════════════════════════"

  echo "[$LABEL/$proj] clearing vectordb"
  poetry run secreqgen ingest clear --force >/dev/null 2>&1 || true

  echo "[$LABEL/$proj] ingesting docs from $DOCS_DIR"
  poetry run secreqgen ingest documents "$DOCS_DIR" --type architecture 2>&1 | grep -E "Stored|chunks|FAIL" | head

  echo "[$LABEL/$proj] ingesting system context"
  poetry run secreqgen ingest system-context "$CTX" >/dev/null 2>&1 || true

  echo "[$LABEL/$proj] running paired N=$N pilot with $MODEL"
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

# ─── Final summary banner (label-aware) ─────────────────────────────
UPPER_LABEL=$(echo "$LABEL" | tr '[:lower:]' '[:upper:]')
echo
echo "═══════════════════════════════════════════════════════════════"
echo " $UPPER_LABEL SUB-TEST DONE  ($(date '+%Y-%m-%d %H:%M:%S'))"
echo "  succeeded: $SUCC"
echo "  failed:    $FAIL"
echo "  skipped:   $SKIP"
echo "═══════════════════════════════════════════════════════════════"
echo
echo "Next step: cross-model meta-analysis"
echo "  PYTHONPATH=src .venv/bin/python scripts/meta_analysis.py --version $LABEL"
echo "  PYTHONPATH=src .venv/bin/python scripts/cross_model_comparison.py"
