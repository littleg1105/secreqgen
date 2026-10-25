#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────
# DEPRECATED — kept for back-compat with docs and earlier scripts.
#
# This was the original qwen3.6 cross-model sub-test driver. It has
# been generalised into `run_cross_model_pilot.sh`, which accepts the
# model + label as parameters and works for any Ollama-served model.
#
# This script is now a thin shim: it just sets the qwen3.6-flavoured
# defaults and delegates. All flags / positional args / env-var
# overrides (MODEL, N, FORCE, OUTPUT_DIR_NAME, etc.) pass through.
#
# New work should call `run_cross_model_pilot.sh` directly with explicit
# MODEL=… (and optional LABEL=…). See its header for full usage.
# ─────────────────────────────────────────────────────────────────────
set -uo pipefail

REPO="/Users/georgeg/Documents/Uni Docs/Thesis/coded_project/secreqgen"

# Preserve the historical defaults of the qwen3.6 sub-test.
export MODEL="${MODEL:-qwen3.6:35b-a3b-coding-mxfp8}"
export N="${N:-20}"
export LABEL="${LABEL:-qwen36}"
export OUTPUT_DIR_NAME="${OUTPUT_DIR_NAME:-results_compare_runs_thesis_n20_qwen36}"

exec bash "$REPO/scripts/run_cross_model_pilot.sh" "$@"
