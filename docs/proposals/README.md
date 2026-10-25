# Proposals

Council-validated working documents for thesis-methodology decisions.
All three docs are at **rev. 2** post-council
(council-20260503-080229) and are the inputs to (a) supervisor
sign-off on Goal #6 and (b) autopilot live-mode implementation of the
`compare-runs` subcommand.

## Files

* **`compare-runs-design.md`** *(rev. 2)* — design for a new
  `secreqgen evaluate compare-runs` subcommand. Per-story pairing as
  primary axis, run-level pairing as sensitivity. N=20 default
  (N=10 via `--pilot`). Wilcoxon primary, paired t-test sensitivity,
  Shapiro-Wilk normality check, Cohen's `d_z`. Thin wrapper over
  existing `EvaluationPipeline.run()` — no mock LLM, no parallel
  evaluation path.

* **`reproducibility-considerations.md`** *(rev. 2)* — section for
  Chapter 6 of the thesis. Anchored on Goodman, Fanelli & Ioannidis
  (2016): claims **methods reproducibility** + **inferential
  reproducibility**, explains why **results reproducibility** in the
  byte-stable sense is unachievable for an LLM-driven system without
  invalidating the empirical claim. ~1.5 pages.

* **`goal-6-rewording.md`** *(rev. 2)* — three options (A/B/C) for
  Thesis Goal #6, in Greek and English, plus a one-paragraph pitch
  for the supervisor. **Recommendation: Option A** (methods +
  inferential reproducibility, with protocol specifics living in
  Chapter 6 rather than the proposal text).

* **`next-steps.md`** *(rev. 2)* — the post-council sequence: clean
  up master, send Option A to supervisor, then run autopilot live
  mode against the rev. 2 `compare-runs-design.md` to implement.

## Why this directory exists

After the autopilot's first end-to-end run on this project produced
12 commits that were vetoed for SPEC_MISMATCH, we paused
implementation and did a methodological review. The first draft of
these proposals went through a 3-agent council
(council-20260503-080229) and was revised into rev. 2. All four docs
read together: each addresses a different angle of the same
underlying issue — what should "reproducible evaluation" actually
mean for an LLM-driven system?
