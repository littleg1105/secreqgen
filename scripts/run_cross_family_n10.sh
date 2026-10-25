#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────
# THESIS SUB-TEST: cross-FAMILY validation
#
# Extends the qwen3 → qwen3.6 sub-test to non-Qwen model families:
#   - gemma4:26b           (Google,    MoE, ~18 GB)
#   - mistral-small3.2:24b (Mistral AI, dense, ~15 GB)
#
# Each runs sequentially on the same 4-project subset that matches the
# original qwen3.6 sub-test design, producing a clean 4×4 model-by-
# project table for the thesis.
#
# Subset (per outcome category from the qwen3 baseline):
#   mastodon    — STRONG + (qwen3)
#   mattermost  — STRONG − (qwen3)
#   bitwarden   — null     (qwen3)
#   keycloak    — STRONG + (qwen3, the anchor)
#
# Outputs:
#   real_test_cases/<proj>/results_compare_runs_gemma4_n10/
#   real_test_cases/<proj>/results_compare_runs_mistral_small_32_n10/
#
# Usage:
#   bash scripts/run_cross_family_n10.sh
# ─────────────────────────────────────────────────────────────────────
set -uo pipefail

REPO="/Users/georgeg/Documents/Uni Docs/Thesis/coded_project/secreqgen"
DRIVER="$REPO/scripts/run_cross_model_pilot.sh"
SUBSET=(mastodon mattermost bitwarden keycloak)

echo "═══════════════════════════════════════════════════════════════"
echo " CROSS-FAMILY SUB-TEST  ($(date '+%Y-%m-%d %H:%M:%S'))"
echo "═══════════════════════════════════════════════════════════════"
echo " Models:   gemma4:26b → mistral-small3.2:24b"
echo " Projects: ${SUBSET[*]}"
echo " N runs:   10 (paired)"
echo

# ─── Model 1: Gemma 4 26B (Google, MoE) ──────────────────────────────
echo
echo "═══ MODEL 1/2: gemma4:26b ($(date '+%H:%M:%S')) ═══"
MODEL=gemma4:26b LABEL=gemma4 N=10 \
  OUTPUT_DIR_NAME=results_compare_runs_gemma4_n10 \
  bash "$DRIVER" "${SUBSET[@]}"
echo

# ─── Model 2: Mistral Small 3.2 24B (Mistral AI, dense) ─────────────
echo
echo "═══ MODEL 2/2: mistral-small3.2:24b ($(date '+%H:%M:%S')) ═══"
MODEL=mistral-small3.2:24b LABEL=mistral_small_32 N=10 \
  OUTPUT_DIR_NAME=results_compare_runs_mistral_small_32_n10 \
  bash "$DRIVER" "${SUBSET[@]}"

echo
echo "═══════════════════════════════════════════════════════════════"
echo " CROSS-FAMILY SUB-TEST DONE  ($(date '+%Y-%m-%d %H:%M:%S'))"
echo "═══════════════════════════════════════════════════════════════"
echo "Next: PYTHONPATH=src .venv/bin/python scripts/cross_model_comparison.py"
