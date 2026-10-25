#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────
# Autonomous orchestrator: run all ablation phases sequentially.
#
# Assumes Phase A (ideas 1-10) is already running OR completed. This
# script will WAIT for the Phase A judge to finish before starting
# any new work. Then it chains:
#
#   1. Phase A judge already running OR running → wait
#   2. Ideas 11+12+16 (LLM-query) retrieval + judge
#   3. Ideas 17-20 (agent retrieval) retrieval + judge
#   4. Ideas 13-15 (generation strategies) — generates real requirements
#
# Each phase writes its own log + JSON outputs. Incremental writes
# ensure partial progress survives a kill.
# ─────────────────────────────────────────────────────────────────────
set -uo pipefail

cd "/Users/georgeg/Documents/Uni Docs/Thesis/coded_project/secreqgen"
mkdir -p logs
TS_ROOT=$(date +%Y%m%d_%H%M%S)
MASTER_LOG="logs/ablation_master_${TS_ROOT}.log"

log() {
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$MASTER_LOG"
}

# ─── 1. Wait for any already-running Phase A judge ──────────────────
log "checking for in-flight rag_ablation_judge.py..."
while pgrep -f "rag_ablation_judge\.py" > /dev/null; do
  count=$(pgrep -af "rag_ablation_judge\.py" | grep -v "pgrep" | wc -l | tr -d ' ')
  log "  $count judge process(es) running, sleeping 60s..."
  sleep 60
done
log "no judge processes running, proceeding"

# Sanity check: did Phase A judge finish cleanly?
if [ -f /tmp/rag_ablation_judged.json ]; then
  n_cells=$(python3 -c "import json; print(len(json.load(open('/tmp/rag_ablation_judged.json'))))")
  log "Phase A judge output found: $n_cells cells"
else
  log "WARNING: /tmp/rag_ablation_judged.json not found. Phase A may have failed."
fi

# ─── 2. Ideas 11, 12, 16 — LLM-query retrieval ──────────────────────
log "═══ STARTING IDEAS 11, 12, 16 (LLM-query retrieval) ═══"
LOG_LLMQ="logs/ablation_llmquery_${TS_ROOT}.log"
log "log: $LOG_LLMQ"
PYTHONPATH=src .venv/bin/python scripts/rag_ablation_llm_query.py >"$LOG_LLMQ" 2>&1 || \
  log "rag_ablation_llm_query.py exited non-zero"

# Judge those cells. Use the existing judge but point it at the new input.
log "judging ideas 11/12/16..."
ABLATION_INPUT=/tmp/rag_ablation_llm_query.json \
ABLATION_JUDGED=/tmp/rag_ablation_llm_query_judged.json \
ABLATION_PIVOT_CSV=/tmp/rag_ablation_llm_query_pivot.csv \
  PYTHONPATH=src .venv/bin/python scripts/rag_ablation_judge.py >>"$LOG_LLMQ" 2>&1 || \
  log "judge for ideas 11/12/16 exited non-zero"
log "ideas 11/12/16 done"

# ─── 3. Ideas 17-20 — agent retrieval ───────────────────────────────
log "═══ STARTING IDEAS 17-20 (agent retrieval) ═══"
LOG_AG="logs/ablation_agents_${TS_ROOT}.log"
log "log: $LOG_AG"
PYTHONPATH=src .venv/bin/python scripts/rag_ablation_agents.py >"$LOG_AG" 2>&1 || \
  log "rag_ablation_agents.py exited non-zero"

log "judging ideas 17-20..."
ABLATION_INPUT=/tmp/rag_ablation_agents.json \
ABLATION_JUDGED=/tmp/rag_ablation_agents_judged.json \
ABLATION_PIVOT_CSV=/tmp/rag_ablation_agents_pivot.csv \
  PYTHONPATH=src .venv/bin/python scripts/rag_ablation_judge.py >>"$LOG_AG" 2>&1 || \
  log "judge for ideas 17-20 exited non-zero"
log "ideas 17-20 done"

# ─── 4. Ideas 13-15 — generation strategies ─────────────────────────
log "═══ STARTING IDEAS 13-15 (generation strategies) ═══"
LOG_GEN="logs/ablation_generation_${TS_ROOT}.log"
log "log: $LOG_GEN"
PYTHONPATH=src .venv/bin/python scripts/rag_ablation_generation.py >"$LOG_GEN" 2>&1 || \
  log "rag_ablation_generation.py exited non-zero"
log "ideas 13-15 done"

# Generation outputs are FULL REQUIREMENTS not retrieved chunks, so they
# need a different judge. The manual qualitative read is the primary
# deliverable; we'll skip an automated judge here and surface the
# generated_requirements arrays for Claude to read directly.

log "═══ ALL ABLATIONS COMPLETE ═══"
log "outputs:"
log "  Phase A retrieval:    /tmp/rag_ablation_retrieval.json"
log "  Phase A judged:       /tmp/rag_ablation_judged.json"
log "  LLM-query retrieval:  /tmp/rag_ablation_llm_query.json"
log "  LLM-query judged:     /tmp/rag_ablation_llm_query_judged.json"
log "  Agents retrieval:     /tmp/rag_ablation_agents.json"
log "  Agents judged:        /tmp/rag_ablation_agents_judged.json"
log "  Generation outputs:   /tmp/rag_ablation_generation.json"
log ""
log "next: manual qualitative read + lab-notebook write-up"
