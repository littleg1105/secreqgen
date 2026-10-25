# Vultr Cloud Migration — Status & Resume Plan

*Created 2026-05-17. Migration paused at Phase 4 (GPU WORKER) — Vultr GPU
stock is depleted across all suitable shapes in EU regions. User has
submitted **Request Reserved Capacity** with Vultr; resume from §4 once
that's granted. Until then, work continues on local Mac (see §7).*

Read this file together with [HANDOFF.md](HANDOFF.md), which has the
thesis-level context (empirical state, verdict tables, council
decisions). This file is the *operational* state of the cloud migration.

---

## 1. Why this migration

The Mac (M-series, ~30-50 tok/s on 24-30B q4 models) is adequate but
slow. The plan is to move compute to a Vultr Cloud GPU so cross-model
re-runs and Option C qualitative retrofits complete in hours instead
of days, AND so that the user can edit/write Chapter 7 from any device
via VS Code Remote-SSH instead of being tied to the Mac.

The user explicitly chose (2026-05-17 session):
- **GPU target:** A40 48 GB (budget; L40S 48 GB is the equivalent fallback)
- **Timing:** provision + migrate immediately (Mac pilot was killed)
- **Cost pattern:** two-instance — cheap always-on CPU "DEV" + on-demand "WORKER" GPU

---

## 2. Two-instance architecture

```
┌──────────────────────────────┐         ┌──────────────────────────────┐
│  DEV   (always-on, CPU)      │         │  WORKER (on-demand, GPU)     │
│  Vultr Cloud Compute (FRA)   │  Vultr  │  Vultr Cloud GPU             │
│  4 vCPU / 8 GB / 100 GB NVMe │  VPC    │  L40S 48 GB or A40 48 GB     │
│  ~$24/mo                     │◄───────►│  ~$1.20-1.70/hr only when    │
│                              │ private │  inference runs              │
│  • VS Code Remote-SSH host   │ network │  • Ollama on :11434 (10.9.x) │
│  • Claude Code               │         │  • Models on instance disk   │
│  • secreqgen + venv          │         │    or persistent block       │
│  • OLLAMA_HOST→worker.10.9.x │         │                              │
└──────────────────────────────┘         └──────────────────────────────┘
        ▲
        │ VS Code Remote-SSH / SSH
        │
   User device (Mac, phone, etc.)
```

**Why split:** The Python pipeline talks to Ollama over HTTP, so Ollama
can live on a different machine. Sentence-transformers (used for
embeddings + matching) is CPU-fast at the corpus sizes here (10-548
chunks per project), so the dev box never needs a GPU. Result: GPU
only exists during inference, which is most of the cost saving.

---

## 3. What's done — DEV is fully operational

### 3.1 DEV instance details

| Property | Value |
|---|---|
| **Public IP (SSH)** | `45.32.156.137` |
| **Private VPC IP** (for future WORKER → DEV) | `10.9.96.3/20` |
| VPC | `thesis-vpc` (`10.9.96.0/20`, Frankfurt) |
| UUID | `5446de3e-3291-4283-8d9d-b3bad2ae11d2` |
| OS | Ubuntu 24.04 LTS x64 |
| Hostname | `secreqgen-dev` |
| Region | Frankfurt, Germany |
| Plan | Regular Cloud Compute (4 vCPU / 8 GB RAM) |

### 3.2 Installed software (verified on DEV)

| Tool | Version | Path |
|---|---|---|
| Python | 3.13.13 (via deadsnakes PPA) | `/usr/bin/python3.13` |
| Node | 20.20.2 | `/usr/bin/node` |
| npm | 10.8.2 | `/usr/bin/npm` |
| Claude Code CLI | 2.1.143 | `/usr/bin/claude` |
| Poetry | 2.4.1 | `/home/secreqgen/.local/bin/poetry` |
| Other apt | build-essential, git, rsync, curl, jq, vim, tmux, htop, software-properties-common | system |

### 3.3 OS state

- User `secreqgen` (UID 1001) with NOPASSWD sudo
- SSH key auth only (same key as Mac `~/.ssh/id_ed25519`)
- UFW enabled, only OpenSSH allowed in
- VPC MTU 1450 applied on `enp8s0` (Vultr requirement, persistent via netplan)
- `/data` directory exists, owned by secreqgen
- `~/.bashrc` contains:
  - `export PATH="$HOME/.local/bin:$PATH"`
  - `export OLLAMA_HOST=http://localhost:11434` (placeholder; change to WORKER private IP in §4.3)

### 3.4 Code + data on DEV

| Path | Source | Size | Notes |
|---|---|---|---|
| `/data/secreqgen/` | Mac:`secreqgen/` | 4.5 MB / 716 files | Excludes `.venv`, `data/vectordb/`, `__pycache__`, caches, logs |
| `/data/real_test_cases/` | Mac:`real_test_cases/` | 4.4 MB / 465 files | Excludes `repos/` (2.5 GB of git clones — not needed; ingestion uses `extracted_docs_md/`) |
| `~/.cache/pypoetry/virtualenvs/secreqgen-F9Dommyu-py3.13/` | `poetry install` | ~500 MB | All deps including torch + transformers |

### 3.5 Patches verified on DEV

Two code patches landed in the repo on 2026-05-17 (also present on Mac
— they were rsync'd). Both verified working on DEV via unit tests + ad-hoc
import checks:

1. **Option B — raw LLM output archival** ([HANDOFF.md → patch section](HANDOFF.md))
   - `src/secreqgen/evaluation/multi_run.py` — Pydantic v2 BaseModel
     handling in `_json_safe`, plus new `serialize_individual_result()`
   - `src/secreqgen/evaluation/__init__.py` — re-exports the new symbol
   - `src/secreqgen/cli.py` — writes `raw_runs/run_NNN.json` per
     individual run alongside `multi_run_report.json`
   - Effect: every fresh `compare-runs` invocation produces full
     audit-trail JSON (generated text, retrieval context, match decisions)

2. **OLLAMA_HOST env-var support** for DEV→WORKER pointing
   - `src/secreqgen/generation/llm_client.py` — `OllamaClient.base_url`
     defaults to `None`; new `_resolve_base_url()` reads `OLLAMA_HOST`
   - Backward compatible: explicit arg > env var > `http://localhost:11434`
   - 8 input forms tested (bare host, host:port, full URL, etc.); 23/23
     existing `test_llm_client` tests pass

### 3.6 Sanity-check on DEV (2026-05-17)

```
serialize_individual_result: secreqgen.evaluation.multi_run  ✓
serialize_report:           secreqgen.evaluation.multi_run  ✓
OLLAMA_HOST resolution (4 forms): all pass  ✓
83 unit tests across test_evaluation_pipeline + test_metrics + test_schemas + test_llm_client  ✓
secreqgen.cli imports cleanly  ✓
```

### 3.7 What's NOT done on DEV (deferred)

- **sshd hardening** (`PermitRootLogin no`, `PasswordAuthentication no`).
  Deferred because if anything went wrong while the user was on a phone
  away from a recovery terminal, they'd be locked out (Vultr noVNC
  console requires the Vultr-generated root password, which may not have
  been saved). Action: SSH in interactively from a Mac terminal and run:
  ```bash
  sudo sed -i 's/^#*PermitRootLogin.*/PermitRootLogin no/' /etc/ssh/sshd_config
  sudo sed -i 's/^#*PasswordAuthentication.*/PasswordAuthentication no/' /etc/ssh/sshd_config
  sudo systemctl restart ssh
  ```

---

## 4. What's blocked — Phase 4 (WORKER)

### 4.1 Status

User submitted **Request Reserved Capacity** on a Vultr Cloud GPU after
exhausting on-demand availability across:
- Frankfurt: A40 48 GB (`vcg-a40-24c-120g-48vram`), L40S 48 GB, A100 80
  GB — all unavailable
- US regions: same shapes — also unavailable; only multi-GPU bare-metal
  ($30+/hr) or fractional shapes <24 GB VRAM available

### 4.2 What to do when Reserved Capacity is granted

1. Provision in Vultr dashboard:
   - **Products → Cloud GPU → L40S 48 GB or A40 48 GB**
   - Region: **Frankfurt** (same as DEV, so VPC works)
   - VPC: attach to **`thesis-vpc`** (10.9.96.0/20)
   - OS: Ubuntu 24.04 LTS
   - Boot disk: 500 GB
   - Hostname: `secreqgen-worker`
   - SSH key: same Mac `mac-key` used for DEV

2. Capture the WORKER's **private VPC IP** (will be `10.9.96.x`) — this
   is what DEV connects to, NOT the public IP.

3. SSH into WORKER (public IP, one-time) and install Ollama bound to
   the VPC private interface. Approximate command sequence (drive via
   the same askpass workaround used for DEV if working from a phone):

   ```bash
   # As secreqgen on WORKER:
   sudo DEBIAN_FRONTEND=noninteractive apt-get update -qq
   sudo apt-get install -y ufw curl
   # NVIDIA drivers + CUDA usually pre-installed on Vultr's GPU images;
   # verify with: nvidia-smi
   curl -fsSL https://ollama.com/install.sh | sh
   sudo systemctl edit ollama   # add Environment="OLLAMA_HOST=0.0.0.0:11434"
   sudo systemctl restart ollama
   # Pull models needed for cross-family + Option C retrofits:
   ollama pull qwen3:30b-a3b-instruct-2507-q4_K_M
   ollama pull qwen3.6:latest
   ollama pull gemma4:26b
   ollama pull mistral-small3.2:24b
   # Firewall: SSH from anywhere, Ollama port from VPC only
   sudo ufw allow OpenSSH
   sudo ufw allow from 10.9.96.0/20 to any port 11434 proto tcp
   sudo ufw --force enable
   ```

### 4.3 Wire DEV → WORKER

On DEV, update `~/.bashrc`:
```bash
# Replace the placeholder line:
sed -i 's|^export OLLAMA_HOST=.*|export OLLAMA_HOST=http://10.9.96.<WORKER_IP>:11434|' ~/.bashrc
source ~/.bashrc

# Verify connectivity:
curl -s -m 5 $OLLAMA_HOST/api/tags | jq '.models[].name'
```

Then test the end-to-end path:
```bash
cd /data/secreqgen
poetry run secreqgen generate check-ollama
```

### 4.4 Snapshot WORKER for fast re-spin-up

Once WORKER works end-to-end, in Vultr dashboard:
**Instance → Snapshots → Take Snapshot**. ~500 GB → ~$25/mo storage.
Then to save money, **destroy** the instance. To resume inference
later: create a new instance **from this snapshot** (~2 min spin-up,
models already pulled, Ollama already configured).

---

## 5. Reference — addresses, paths, commands

### 5.1 SSH from Mac (when at a terminal)

```bash
ssh secreqgen@45.32.156.137
```

If `ssh-add` hasn't loaded the key (e.g., fresh login), the passphrase
will be prompted. Run once to persist across reboots:

```bash
ssh-add --apple-use-keychain ~/.ssh/id_ed25519
```

### 5.2 Driving DEV remotely without a Mac terminal

Used during 2026-05-17 mobile/away-from-home session. Per-command
SSH_ASKPASS, no agent persistence:

```bash
ASKPASS=$(mktemp /tmp/askpass.XXXXXX)
trap 'rm -f "$ASKPASS"' EXIT
printf '#!/bin/sh\nprintf "%%s\\n" "<PASSPHRASE>"\n' > "$ASKPASS"
chmod 700 "$ASKPASS"
SSH_ASKPASS="$ASKPASS" SSH_ASKPASS_REQUIRE=force DISPLAY=:0 \
  ssh -o ConnectTimeout=15 secreqgen@45.32.156.137 \
  '<remote command>' < /dev/null
```

**Note**: the user's passphrase was `1105` during this session. They
intend to rotate it; if the new passphrase isn't documented when you
read this, ask. Better still, ask the user to add the key to macOS
keychain via `ssh-add --apple-use-keychain` so this whole dance becomes
unnecessary.

### 5.3 rsync resync (if Mac is the source of truth and DEV needs updating)

```bash
# Code
rsync -avz --exclude='.venv' --exclude='__pycache__' --exclude='.pytest_cache' \
  --exclude='*.pyc' --exclude='.DS_Store' --exclude='data/vectordb/' \
  --exclude='logs/' \
  "/Users/georgeg/Documents/Uni Docs/Thesis/coded_project/secreqgen/" \
  secreqgen@45.32.156.137:/data/secreqgen/

# Data
rsync -avz --exclude='repos/' --exclude='.DS_Store' --exclude='__pycache__' \
  "/Users/georgeg/Documents/Uni Docs/Thesis/coded_project/real_test_cases/" \
  secreqgen@45.32.156.137:/data/real_test_cases/
```

### 5.4 Common DEV commands

```bash
ssh secreqgen@45.32.156.137                          # SSH in
cd /data/secreqgen && poetry shell                    # Activate Python env
poetry run python -m pytest tests/unit/ -q            # Run unit tests
poetry run secreqgen generate check-ollama            # Validate OLLAMA_HOST wiring
```

### 5.5 Cross-cloud alternative for WORKER (if Vultr stays out of stock)

If the Reserved Capacity request takes too long, WORKER can live on a
different provider. DEV (Frankfurt) → WORKER (anywhere) just needs:

- Public IP firewall pinhole: on WORKER, allow port 11434 only from
  DEV's public IP (`45.32.156.137`)
- `OLLAMA_HOST=http://<worker-public-ip>:11434` on DEV

Options surveyed (2026-05-17):
- **Lambda Labs** — A100 80 GB ~$1.79/hr, usually reliable EU + US stock
- **RunPod** — L40S 48 GB ~$1.19/hr, has community + secure cloud tiers
- **Hetzner GEX series** — flat-rate ~$130-200/mo, EU regions

---

## 6. Open decisions (defer until resuming Phase 4)

- **Tailscale vs UFW pinhole.** If WORKER ends up cross-region (US) or
  cross-provider, Tailscale gives a private mesh net without exposing
  Ollama publicly + lets the user SSH into both boxes from any device
  (phone/tablet). Probably worth it; defer until WORKER exists.
- **VS Code Remote-SSH onto DEV** from a phone/tablet. The Mac SSH key
  doesn't transfer; either add a second public key to DEV's
  `authorized_keys`, or use Tailscale + Termius (iOS).
- **Per-instance .env file vs `.bashrc` for `OLLAMA_HOST`.** Currently
  in `.bashrc`. If multiple shells / cron jobs / non-interactive
  invocations need it, move to a `.env` file loaded by the secreqgen
  CLI directly. Not urgent.

---

## 7. Plan A — local Mac resume (active now)

While Reserved Capacity is pending, the cross-family pilot resumes on
the Mac. The patches that landed today (Option B + OLLAMA_HOST) are
on disk on the Mac already; new runs will automatically produce
`raw_runs/` directories with full LLM outputs.

### Pre-flight checklist before re-launching

- [ ] Ollama is up locally and reachable at `http://localhost:11434`
- [ ] All four models pulled locally:
  - `qwen3:30b-a3b-instruct-2507-q4_K_M`
  - `qwen3.6:latest`
  - `gemma4:26b`
  - `mistral-small3.2:24b`
- [ ] Mac has enough free disk (raw_runs/ adds ~500 KB - 2 MB per run,
  so ~5-20 MB per project)
- [ ] `caffeinate` running so the laptop doesn't sleep mid-run

### Launch (matches the original cross-family wrapper, plus raw_runs/)

```bash
cd "/Users/georgeg/Documents/Uni Docs/Thesis/coded_project/secreqgen"
mkdir -p logs
LOGFILE="logs/cross_family_n10_$(date +%Y%m%d_%H%M%S).log"
nohup bash scripts/run_cross_family_n10.sh > "$LOGFILE" 2>&1 &
disown
echo "log: $LOGFILE"
```

Runs `gemma4:26b` → `mistral-small3.2:24b` sequentially on the
4-project subset (mastodon, mattermost, bitwarden, keycloak). Outputs:

```
real_test_cases/<proj>/results_compare_runs_gemma4_n10/run_comparison_*/
  multi_run_report.json
  raw_runs/run_001.json … run_010.json   ← NEW from Option B patch
real_test_cases/<proj>/results_compare_runs_mistral_small_32_n10/run_comparison_*/
  multi_run_report.json
  raw_runs/run_001.json … run_010.json
```

Estimated total: ~8-14 hours.

### Monitor

```bash
tail -F "$LOGFILE"
# or:
bash scripts/heartbeat_poller.sh "$LOGFILE"
```

### Pre-2026-05-17-abort partial results

When the cross-family pilot was killed earlier today to move to cloud,
the following per-project results made it to disk but **without** raw
outputs (because the Option B patch hadn't landed yet at runtime):

- `real_test_cases/mastodon/results_compare_runs_gemma4_n10/run_comparison_2026-05-17_15-15-57/` — stats only
- `real_test_cases/mattermost/results_compare_runs_gemma4_n10/run_comparison_2026-05-17_16-04-36/` — stats only
- `real_test_cases/bitwarden/results_compare_runs_gemma4_n10/run_comparison_2026-05-17_16-04-XX/` — stats only

Decision for the re-run: **start fresh**, overwrite via `FORCE=1`. The
3 already-done projects took ~50 min each. Throwing them away costs
~2.5 hours of compute but produces a clean, consistent dataset across
all 8 (4 projects × 2 models) runs — every cell has raw_runs/ data.

If sunk-cost prevents that: use the `--no-overwrite` driver behavior to
skip the 3 completed projects and just run keycloak + all 4
mistral_small projects. They'll be the only ones with raw_runs/.
Asymmetric data, but ~2.5 hours faster.

### After the run finishes

```bash
# regenerate the cross-model comparison report
cd "/Users/georgeg/Documents/Uni Docs/Thesis/coded_project/secreqgen"
PYTHONPATH=src .venv/bin/python scripts/cross_model_comparison.py \
  --output docs/sub_test_qwen36_results.md
```

(Note: `cross_model_comparison.py` may need a small extension to render
the 4-model table — see [HANDOFF.md → Pending work #2](HANDOFF.md).)

---

## 8. Status summary (for the next session)

| Item | State | Action |
|---|---|---|
| DEV instance | ✓ live, fully provisioned | None unless updating code/data |
| DEV sshd hardening | ⏸ deferred | Do when at Mac terminal |
| WORKER instance | ⏸ pending Reserved Capacity | Wait for Vultr; then §4 |
| Cross-family pilot data | partially aborted on Mac | Re-running on Mac per §7 |
| Option B patch (raw outputs) | ✓ in code on Mac + DEV | None — picks up automatically |
| OLLAMA_HOST patch | ✓ in code on Mac + DEV | None — picks up via env var |
| Chapter 7 writing | unstarted | Council 2026-05-17 said start; possible to do on DEV via VS Code Remote-SSH right now |
