#!/usr/bin/env bash
# Heartbeat poller for any cross-model pilot driver.
# Emits one status line every 60 seconds describing:
#   - which project is currently running
#   - how long it's been running
#   - whether Ollama has the model loaded
#   - what the latest log line says
# Designed to be tail -F'd or piped into a Monitor.
#
# Generic across labels: grep matches the indented `  [<label>] project`
# header line written by run_cross_model_pilot.sh (and the legacy
# run_thesis_n20_qwen36.sh shim), regardless of what the label is.
set -uo pipefail

LOGFILE="${1:-/dev/null}"
POLL_INTERVAL="${POLL_INTERVAL:-300}"  # 5 minutes default

start_ts=$(date +%s)
last_proj=""
proj_start_ts=$start_ts

while true; do
  now=$(date +%s)
  total_min=$(( (now - start_ts) / 60 ))
  hh=$(date '+%H:%M:%S')

  # Detect which project is currently running. The driver emits a header
  # line of the form `  [<label>] <project>  (HH:MM:SS)   docs: ...`.
  # We match the leading two spaces + `[any-label] project-name` shape,
  # which is generic across qwen36, gemma4_26b, mistral_small_32, …
  current_proj=$(grep -E "^  \[[^]]+\] [a-z][a-z0-9_-]* +\(" "$LOGFILE" 2>/dev/null \
                  | tail -1 \
                  | sed -E 's/^  \[[^]]+\] ([a-z][a-z0-9_-]*).*/\1/')
  if [ -n "$current_proj" ] && [ "$current_proj" != "$last_proj" ]; then
    last_proj="$current_proj"
    proj_start_ts=$now
  fi
  proj_min=$(( (now - proj_start_ts) / 60 ))

  # Ollama state
  ollama_state=$(curl -s -m 5 http://localhost:11434/api/ps 2>/dev/null | python3 -c "
import json, sys
try:
    d = json.load(sys.stdin)
    m = d.get('models', [])
    if not m:
        print('idle')
    else:
        print(f\"loaded: {m[0]['name']}\")
except Exception:
    print('unknown')
" 2>/dev/null)

  # Latest meaningful log line
  latest=$(tail -3 "$LOGFILE" 2>/dev/null | tail -1 | tr -d '\r' | head -c 100)

  echo "[heartbeat ${hh}] total=${total_min}min  project=${last_proj:-?}  in_project=${proj_min}min  ollama=${ollama_state}  | $latest"

  # Check if any cross-model pilot script is still alive — exit if not.
  # Matches both the new model-agnostic driver and the legacy qwen36 shim,
  # plus the cross-family wrapper that chains multiple model runs.
  if ! pgrep -f "run_cross_model_pilot\.sh|run_thesis_n20_qwen36\.sh|run_cross_family_n10\.sh" > /dev/null; then
    echo "[heartbeat ${hh}] run script no longer running — exiting heartbeat"
    exit 0
  fi

  sleep "$POLL_INTERVAL"
done
