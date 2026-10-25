# Autopilot project context

> **You are reading this because you were spawned by the agent-council
> autopilot to implement a single leaf goal. This document is regenerated
> by the orchestrator before each spawn — it reflects the CURRENT state of
> the project, not the state when the project started.**

## The original user goal

```
# Project goal — Reproducibility harness for secreqgen thesis evaluation

## What I want built

Add a `secreqgen evaluate reproduce` CLI subcommand that runs phase1 (zero-shot
baseline) and phase2 (RAG-enhanced) against a frozen evaluation corpus with
fixed random seeds, producing a deterministic JSON report comparing the two
configurations. The report must be byte-stable across runs (modulo timestamps
and absolute paths) so the thesis evaluation chapter can demonstrate
reproducible results to reviewers.

## Constraints

- Python 3.13 + Poetry. Use existing project structure under `src/secreqgen/`.
- No new external dependencies if avoidable. The project already uses LangChain,
  ChromaDB, sentence-transformers, Ollama, Typer, Pydantic. Reuse those.
- Do NOT modify the existing `phase1_*` / `phase2_*` evaluation summary files
  in `coded_project/real_test_cases/` — those are historical records.
- Follow the project's commit conventions (atomic commits, conventional commit
  messages, document changes in `docs/log.md`).
- Must work offline (the frozen corpus must be self-contained; no network
  required during reproduction).

## What "done" looks like

1. `poetry run secreqgen evaluate reproduce --output report.json` runs to
   completion and produces a JSON report.
2. Running the same command twice on the same corpus + seed produces JSON
   reports that are byte-identical except for fields explicitly excluded from
   the comparison (timestamps, absolute paths in the host environment, hostname).
3. The report contains: per-user-story precision/recall/F1 for both phase1 and
   phase2, an aggregate comparison, the corpus hash (for tamper detection),
   and the seed used.
4. A test (or set of tests) verifies the byte-stability property by running
   `evaluate reproduce` twice and asserting the relevant fields match.
5. A small `docs/reproducibility.md` explains how to run reproduction, what
   fields are deterministic vs not, and how to debug drift.

## Out of scope

- Don't redesign the existing evaluation pipeline. The new subcommand should
  call into existing evaluation code, not rewrite it.
- Don't add a UI / web report. JSON only for now; Markdown rendering can be a
  follow-up.
- Don't change the LLM (still Llama 3 8B / Mistral 7B via Ollama). If
  Ollama produces non-deterministic output even with seeds, document that as
  a known limitation in `docs/reproducibility.md` and use a deterministic
  mock LLM for the byte-stability tests.
```

## Project state (snapshot)

- Started: 2026-05-02T22:45:08.943Z
- Total leaves: 6
- Completed: 5
- Failed: 0
- Remaining: 1
- Last green commit: f31e1f95e502baea2ebcb7d369fbcc5d1a7656a9
- Mode: LIVE
- **Your goal: `g6`** — see `.autopilot/goals/g6.md`

## All leaf goals (status)

- [x] **g1**: Add frozen evaluation corpus and deterministic corpus hash
- [x] **g2**: Add deterministic mock LLM for reproducible generation
- [x] **g3**: Add reproducibility report schema with stable JSON serialization
- [x] **g4**: Wire `secreqgen evaluate reproduce` CLI command
- [x] **g5**: Verify byte-stability across two reproduce invocations
- [~] **g6**: Document reproducibility procedure in docs/reproducibility.md ← YOU ARE HERE

## Conventions for this project

- Project type: **Python (Poetry + pytest)** (Python)
- Test framework: pytest
- Run `poetry run pytest` to verify the suite.
- Commit messages: Conventional Commits (`feat(g3): ...`, `fix(g3): ...`).
- Per-goal scope is enforced by convention; do not touch files outside what
  your goal requires.

## Hard rules (orchestrator-enforced)

These are not suggestions. The orchestrator (or git hooks) will reject
violations and may roll back your commits.

1. **Frozen specs.** Files under `.council/specs/` are CONTRACT. Do not
   modify, delete, or rename them. A pre-commit hook (when active in live
   mode) rejects any diff that touches an active goal's spec.
2. **Orchestrator state.** Do not write to `.autopilot/state.json`,
   `.autopilot/control.json`, or any file the orchestrator owns. Read-only
   from the implementing-session perspective.
3. **Other goals.** Implement exactly the goal listed as `current_goal_id`
   in this doc. Do not advance, decompose further, or "fix" other goals.
4. **No destructive git.** Never `git reset --hard`, `git push`,
   `git branch -D`, or rewrite history. The orchestrator handles rollback.
5. **Always commit.** When you make a meaningful change that compiles, commit
   it. Multiple small commits are better than one big one — they let the
   orchestrator detect progress between iterations.

## Done signal

When `bun run autopilot verify --goal <your-goal-id>` exits 0, your work is
done. Make a final commit with the title `feat(<your-goal-id>): <summary>`
and exit. The orchestrator will pick up the work from there.

## Completed-goal summaries

These goals are already done. Their code is in the repo at the
`last_green_commit` referenced above. Don't reimplement them; you may
read their code if it's useful context for your own work.

### g1: Add frozen evaluation corpus and deterministic corpus hash

Create a frozen, self-contained evaluation corpus checked into the repo (under a stable path inside the package or repo data directory) containing at least 3 user stories with hand-curated expected security requirements. Expose a Python API: FROZEN_CORPUS_PATH (Path), load_frozen_corpus() (returns list of dicts with user_story and expected_requirements), and compute_corpus_hash(path: Path) -> str returning a hex digest that is order-independent over filesystem iteration and changes when any file's content changes. This is the foundation every later leaf depends on.

Implemented in commit: `c8ec8453b91032737e027fb7a03897476eccdfcd`

### g2: Add deterministic mock LLM for reproducible generation

Create a DeterministicMockLLM class in secreqgen.evaluation.mock_llm that returns byte-identical output for identical (seed, prompt) pairs across instances and process restarts. The class should expose a generate(prompt: str) -> str method and a constructor accepting a seed integer. It must produce text that resembles a security requirement (contains 'shall' / 'must' / 'requirement') so it can be parsed by the existing pipeline downstream. This is the determinism substrate that makes byte-stability achievable when Ollama is non-deterministic.

Implemented in commit: `508333c41916db87f36bf4d0287f5eaf5400dad5`

### g3: Add reproducibility report schema with stable JSON serialization

Define ReproducibilityReport (Pydantic model) with fields: seed (int), corpus_hash (str), per_story (list of per-story metric records, each with story_id, phase1, phase2 each containing precision/recall/f1 floats), and aggregate (dict with phase1/phase2 each containing precision/recall/f1). Provide serialize_report(report) -> str that emits sorted-key JSON with stable float formatting. Provide normalize_for_comparison(dict) -> dict that strips volatile fields (timestamps, absolute paths, hostname) so two runs can be compared. This is the output contract for g4 and the comparison primitive for g5.

Implemented in commit: `68b09461b37d9d1dfdacc6e134c251b9d91bedf1`

### g4: Wire `secreqgen evaluate reproduce` CLI command

Add a Typer subcommand `evaluate reproduce` that accepts --output PATH and --seed INT (default 42). It loads the frozen corpus (g1), uses the deterministic mock LLM (g2) to generate requirements for both phase1 (zero-shot) and phase2 (RAG) by calling into the existing evaluation pipeline under src/secreqgen/evaluation/, computes per-user-story precision/recall/f1 plus an aggregate, builds a ReproducibilityReport (g3), and writes it to --output as JSON via serialize_report. Must run offline using only the mock LLM.

Implemented in commit: `db523839b33eb222002d83ebf4615e6d3b8ba3d4`

### g5: Verify byte-stability across two reproduce invocations

Add an integration test that invokes `evaluate reproduce` twice with the same seed via Typer's CliRunner, normalizes both outputs with normalize_for_comparison (g3) to strip volatile fields, and asserts byte-equality of the deterministic portions including per_story metrics, aggregate, seed, and corpus_hash. This is the meta-verification of the whole reproducibility property and corresponds directly to done-criterion #4.

Implemented in commit: `f31e1f95e502baea2ebcb7d369fbcc5d1a7656a9`
