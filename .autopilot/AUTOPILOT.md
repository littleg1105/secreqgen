# Autopilot project context

> **You are reading this because you were spawned by the agent-council
> autopilot to implement a single leaf goal. This document is regenerated
> by the orchestrator before each spawn — it reflects the CURRENT state of
> the project, not the state when the project started.**

## The original user goal

```
# Proposal: `secreqgen evaluate compare-runs` subcommand

**Status:** revised after council validation (council-20260503-080229).
**Author:** drafted with Claude as a thesis-methodology aid; revised
incorporating unanimous and strongest-divergent council recommendations.
**Date:** 2026-05-03 (rev. 2).

---

## 0. Council-driven revisions (rev. 2)

The first draft was reviewed by a 3-agent council on 2026-05-03
(council-20260503-080229). Five revisions follow from that review;
each is annotated **[council]** at the point where it appears in the
text.

1. **Per-story pairing as the primary axis** — not per-run pairing.
   All three agents agreed; the existing `EvaluationPipeline` already
   exposes `StoryEvaluationResult.baseline_metrics` /
   `rag_metrics` per story, so this is an integration choice, not new
   plumbing.
2. **N=20 by default** (Codex) — N=10 is too small for a stability
   claim if the effect is modest. N=10 stays as the development /
   pilot setting.
3. **Wilcoxon as the *primary* test, paired t-test as a *sensitivity*
   check** (Codex) — not co-equal primaries. A single primary keeps the
   inferential claim crisp; the sensitivity test confirms robustness.
4. **`d_z` (paired Cohen's d) is the named effect size**, not generic
   "Cohen's d" (Codex) — writing "Cohen's d" without specifying the
   paired variant is a methodology gotcha. We use `d_z` throughout.
5. **Anchor on Goodman et al. (2016)** for the reproducibility framing
   in the companion `reproducibility-considerations.md` (Claude) —
   this proposal references the framework rather than restating it.

## 1. Why

The thesis evaluates an LLM-driven RAG pipeline against a baseline. LLM
sampling, embedding noise, and floating-point hardware variation mean
that running `secreqgen evaluate run` twice on the same inputs produces
**different F1 scores**. A single run cannot tell you whether RAG is
genuinely better than baseline, or whether it just drew a lucky sample.

The empirical-research answer is not "make the runs byte-identical"
(numerical reproducibility — what the autopilot's vetoed work attempted).
The right answer is:

1. Run the experiment **N times** under each condition.
2. Report **mean ± standard deviation** instead of a single F1 number.
3. Apply a **paired statistical test** with a **per-story pairing
   axis** so the claim is "RAG is better than baseline on this dataset
   with p < 0.05" instead of "RAG is better in this run." **[council]**
4. Report a **paired-sample effect size — `d_z`** — so reviewers see
   how *much* better, not just whether the difference is significant.
   **[council]**

`compare-runs` is the subcommand that implements (1)–(4). It does not
re-implement evaluation; it calls the existing `EvaluationPipeline.run()`
N times and aggregates over the per-story results that pipeline already
produces.

## 2. Integration constraint (the lesson from the veto)

The autopilot's earlier attempt was vetoed because it built a parallel
evaluation path (`evaluation/reproduce.py` + `mock_llm.py`) that
sidestepped the real pipeline. This proposal explicitly does the
opposite:

- It **calls** `EvaluationPipeline.run(stories_path, ground_truth_path)`
  unchanged.
- It does **not** introduce a mock LLM, a frozen corpus, or a parallel
  matcher. Real Ollama, real retriever, real `sentence-transformers`,
  real `MetricsCalculator`.
- It reads the pipeline's existing `StoryEvaluationResult` per-story
  outputs to do per-story pairing — no pipeline changes required.
- Variance is the *signal*, not a problem to be eliminated.

## 3. CLI surface

```
secreqgen evaluate compare-runs \
    <stories.json> <ground_truth.json> \
    --runs 20 \
    --output docs/evaluation_results/run_comparison \
    [--match-threshold 0.85] \
    [--partial-threshold 0.70] \
    [--context system_context.yaml] \
    [--model qwen3:30b] \
    [--seed-offset 0] \
    [--pilot]
```

Flags identical to `evaluate run` except:

- `--runs N` — how many times to repeat the full pipeline (default
  **20**, per council). **[council]**
- `--pilot` — convenience flag that sets `--runs 10`. Marks the
  resulting report file as a pilot/debug artefact, not a thesis-grade
  result.
- `--seed-offset K` — for reviewers who want to reproduce the *same set
  of N runs*, they re-run with the same offset; combined with
  `OLLAMA_SEED` semantics this gives **methodological reproducibility**
  (the protocol is documented; same protocol → same statistical
  conclusion, even if the byte-level outputs differ).

## 4. File layout

New code lives in **one** new file plus a **few-line addition** to
`cli.py` and `evaluation/__init__.py`:

```
src/secreqgen/evaluation/
├── pipeline.py              # unchanged
├── metrics.py               # unchanged
├── semantic_matcher.py      # unchanged
├── report.py                # unchanged
└── multi_run.py             # NEW — ~180 LOC (was 150; +30 for per-story
                             #         aggregation and paired t-test)

src/secreqgen/cli.py         # +1 command, ~70 LOC

src/secreqgen/evaluation/__init__.py  # +1 line, export the new aggregator
```

No mock LLM, no parallel pipeline, no frozen corpus, no rewriting of
existing modules. Total surface area is one new module plus a CLI
entry.

## 5. `multi_run.py` — function signatures (no implementation yet)

```python
"""Multi-run aggregation for variance characterization.

Runs `EvaluationPipeline.run()` N times under identical configuration,
then aggregates per-run + per-story metrics into mean ± std, paired
statistics, and the d_z effect size. The run-level pairing is reported
as a sensitivity result; the per-story pairing is the primary axis.

Byte-level run outputs will differ; the protocol and the aggregate
statistics are stable.
"""

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from secreqgen.evaluation.pipeline import (
    EvaluationPipeline,
    EvaluationPipelineResult,
    StoryEvaluationResult,
)


@dataclass
class RunStatistics:
    """Mean ± std for one metric across N runs of one condition."""
    metric_name: str           # "precision" | "recall" | "f1_score" | "hallucination_rate"
    mean: float
    std: float                 # sample stdev (n-1), reported alongside mean
    min: float
    max: float
    values: list[float]        # raw per-run values, retained for plots


@dataclass
class PairedTestResult:
    """Paired test result. Used for both run-level and story-level pairing."""
    pairing_axis: str          # "per-run" | "per-story"
    primary_test: str          # "wilcoxon-signed-rank"
    primary_statistic: float
    primary_p_value: float
    sensitivity_test: str      # "paired-t-test"
    sensitivity_statistic: float
    sensitivity_p_value: float
    shapiro_wilk_p: float      # normality check on the differences
    d_z: float                 # paired Cohen's d_z (NOT generic d)
    d_z_interpretation: str    # "small" | "medium" | "large" (Cohen 1988)
    n_pairs: int


@dataclass
class MultiRunReport:
    """Aggregate of N runs of `EvaluationPipeline.run()`."""
    n_runs: int
    individual_results: list[EvaluationPipelineResult]
    baseline_aggregate: dict[str, RunStatistics]   # keyed by metric name
    rag_aggregate: dict[str, RunStatistics]
    # Per-story pairing — PRIMARY result
    paired_test_per_story_f1: PairedTestResult
    paired_test_per_story_recall: PairedTestResult
    paired_test_per_story_precision: PairedTestResult
    # Run-level pairing — sensitivity result
    paired_test_per_run_f1: PairedTestResult
    config: dict
    run_timestamp: datetime = field(default_factory=datetime.now)


# ─────────────────────────────────────────────────────────────────────
# Aggregation
# ─────────────────────────────────────────────────────────────────────

def aggregate_runs(
    results: list[EvaluationPipelineResult],
) -> tuple[dict[str, RunStatistics], dict[str, RunStatistics]]:
    """Mean/std/min/max for precision, recall, F1, hallucination across
    runs, separately for baseline and RAG. Pure function; safe to unit-
    test with hand-built `MetricsBreakdown` fixtures.
    """
    ...


def collect_per_story_f1(
    results: list[EvaluationPipelineResult],
    condition: str,            # "baseline" | "rag"
) -> dict[str, list[float]]:
    """For per-story pairing — the PRIMARY axis.

    Returns: {story_id: [f1_run_1, f1_run_2, ..., f1_run_N]}.

    For each story, takes the mean F1 across the N runs as the
    representative value of that story under that condition; the paired
    test then runs over story_count pairs (one per story), not over N
    pairs. This dramatically increases statistical power vs. run-level
    pairing where N is the only sample.

    Reads from `EvaluationPipelineResult.per_story_results`, which
    already exposes `baseline_metrics.f1_score` and
    `rag_metrics.f1_score` per story.
    """
    ...


def per_story_paired_values(
    results: list[EvaluationPipelineResult],
    metric: str,               # "f1_score" | "precision" | "recall"
) -> tuple[list[float], list[float]]:
    """Returns (baseline_values, rag_values) where index i is the mean
    metric value for story i, averaged over the N runs under each
    condition. Lengths are equal and = number of stories in dataset.
    """
    ...


# ─────────────────────────────────────────────────────────────────────
# Statistical tests
# ─────────────────────────────────────────────────────────────────────

def paired_test_with_sensitivity(
    baseline_values: list[float],
    rag_values: list[float],
    pairing_axis: str,         # "per-run" | "per-story"
) -> PairedTestResult:
    """Run BOTH paired tests, with normality check, and produce a
    PairedTestResult that names the primary (Wilcoxon) and reports the
    sensitivity (paired t-test) alongside.

    Normality of differences is tested with Shapiro-Wilk; the result is
    recorded so reviewers can decide whether the t-test sensitivity
    check is interpretable. We do NOT switch primaries based on the
    Shapiro-Wilk result — the primary is fixed (Wilcoxon) for
    pre-registration discipline.
    """
    ...


def cohens_d_z_paired(
    baseline_values: list[float],
    rag_values: list[float],
) -> tuple[float, str]:
    """Paired-sample Cohen's d_z (NOT generic Cohen's d).

    d_z = mean(rag - baseline) / std(rag - baseline)

    The 'z' subscript distinguishes this from d_av and d_rm, the other
    common paired-sample variants. d_z is what most reviewers expect
    when a paper says 'paired effect size'. Report it by name to
    eliminate ambiguity.

    Interpretation thresholds (Cohen 1988): 0.2 small, 0.5 medium,
    0.8 large. Returns the numeric d_z and a human label.
    """
    ...


# ─────────────────────────────────────────────────────────────────────
# Orchestration
# ─────────────────────────────────────────────────────────────────────

def run_multi_evaluation(
    pipeline: EvaluationPipeline,
    stories_path: Path,
    ground_truth_path: Path,
    n_runs: int,
    progress_callback: Optional[Callable[[int, int, str], None]] = None,
) -> list[EvaluationPipelineResult]:
    """Drive `pipeline.run()` N times sequentially, returning the raw
    per-run results. Sequential because Ollama is single-threaded and
    the bottleneck is generation latency, not orchestration. Sequential
    runs also make timing data interpretable.
    """
    ...


def build_multi_run_report(
    results: list[EvaluationPipelineResult],
    config: dict,
) -> MultiRunReport:
    """Top-level orchestration: aggregate + per-story paired tests
    (primary) + per-run paired tests (sensitivity) + d_z."""
    ...


def serialize_report(report: MultiRunReport) -> str:
    """Pretty-print JSON for review and archival.

    Includes: N, mean ± std for every metric in both conditions, the
    full PairedTestResult for both pairing axes (so reviewers can
    inspect the primary AND the sensitivity result), Shapiro-Wilk
    p-value on differences, d_z with interpretation, full config, and a
    per-run × per-story F1 matrix so plots can be regenerated without
    re-running."""
    ...
```

## 6. CLI wiring — added command

```python
# src/secreqgen/cli.py

@evaluate_app.command("compare-runs")
def evaluate_compare_runs(
    stories: Annotated[Path, typer.Argument(help="User stories JSON file")],
    ground_truth: Annotated[Path, typer.Argument(help="Ground truth JSON file")],
    runs: Annotated[int, typer.Option("--runs", help="Number of repeated runs (default 20)")] = 20,
    pilot: Annotated[bool, typer.Option("--pilot", help="Run N=10 and mark output as pilot")] = False,
    output_dir: Annotated[Optional[Path], typer.Option("--output", "-o")] = None,
    match_threshold: Annotated[float, typer.Option("--match-threshold")] = 0.85,
    partial_threshold: Annotated[float, typer.Option("--partial-threshold")] = 0.70,
    context: Annotated[Optional[Path], typer.Option("--context", "-c")] = None,
    model: Annotated[Optional[str], typer.Option("--model", "-m")] = None,
) -> None:
    """Run the evaluation pipeline N times and report aggregate statistics.

    Default N=20 produces thesis-grade tables. Use --pilot for N=10
    during development; pilot reports are flagged as such and should
    not be cited as headline results.

    Reports two paired tests: per-story (primary) and per-run
    (sensitivity), with Wilcoxon as the primary inferential test and
    paired t-test as a sensitivity check. Effect size is paired
    Cohen's d_z. A Shapiro-Wilk normality check on the paired
    differences is reported alongside so reviewers can interpret the
    sensitivity result.
    """
    if pilot:
        runs = 10
    # Reuse the SAME initialization as `evaluate run`:
    #   load config, build OllamaClient, build retriever, build pipeline.
    # Then call run_multi_evaluation(pipeline, ..., n_runs=runs).
    # Then call build_multi_run_report() and serialize.
    # Print summary table with the per-story PRIMARY result on top and
    # per-run sensitivity below.
    ...
```

## 7. Output

Three artefacts per invocation, written under `output_dir`:

```
docs/evaluation_results/run_comparison_2026-05-03_14-32-19/
├── multi_run_report.json       # full structured report
├── per_run_metrics.csv         # one row per (run_idx, story_id, condition); for plots
└── multi_run_summary.md        # human-readable; for thesis chapter 6
```

`multi_run_summary.md` shape — what the supervisor / examiner reads:

```
# Multi-Run Evaluation Report

**Configuration:** stories=…, ground_truth=…, model=…, threshold=0.85, N=20
**Status:** thesis-grade (N=20). Pilot runs (N=10) are flagged separately.

## Aggregate metrics (mean ± std across 20 runs)

| Metric             | Baseline     | RAG          | Δ (mean)  |
|--------------------|--------------|--------------|-----------|
| Precision          | 0.31 ± 0.04  | 0.42 ± 0.06  | +0.11     |
| Recall             | 0.28 ± 0.05  | 0.38 ± 0.04  | +0.10     |
| F1                 | 0.29 ± 0.04  | 0.40 ± 0.05  | +0.11     |
| Hallucination Rate | 0.40 ± 0.08  | 0.32 ± 0.06  | -0.08     |

## Primary inferential result (per-story pairing on F1)

- N pairs (stories): 42
- Wilcoxon signed-rank (PRIMARY): W = 73, p = 0.003
- Paired t-test (sensitivity): t = 4.18, p = 0.0001
- Shapiro-Wilk on paired differences: p = 0.21 (normality not rejected)
- Paired Cohen's d_z: 0.71 (medium-to-large)

## Sensitivity result (per-run pairing on aggregate F1)

- N pairs (runs): 20
- Wilcoxon signed-rank: W = 12, p = 0.001
- Paired t-test: t = 5.04, p < 0.001
- Paired Cohen's d_z: 1.84 (large)

## Interpretation

The primary per-story analysis (N=42 pairs) yields p = 0.003 (Wilcoxon)
with d_z = 0.71. The per-run sensitivity analysis (N=20 pairs)
agrees in direction and significance (p = 0.001). Combined, this
supports the claim that RAG produces a higher-F1 distribution of
per-story scores than baseline on this benchmark, beyond what is
explicable by run-to-run sampling noise.
```

## 8. Test plan

```
tests/evaluation/test_multi_run.py
├── test_aggregate_runs_computes_mean_std
├── test_collect_per_story_f1_aligns_indices
├── test_per_story_paired_values_returns_equal_lengths
├── test_paired_test_with_sensitivity_basic       # known input → known stat
├── test_cohens_d_z_paired_known_value            # textbook example
├── test_run_multi_evaluation_calls_pipeline_N_times  # mock pipeline.run, assert N calls
├── test_serialize_report_round_trip              # serialize → JSON → parse → equal
└── test_pilot_flag_sets_n_to_10                  # CLI integration
```

No Ollama, no embedder, no real network in unit tests. The integration
test (mark as `@pytest.mark.slow`) can do `--runs 2` against real
Ollama to confirm end-to-end wiring. Use `scipy.stats.wilcoxon`,
`scipy.stats.ttest_rel`, and `scipy.stats.shapiro` — all already
implicitly available via numpy/scipy in the existing dev environment.

## 9. Effort estimate

- `multi_run.py`:     ~180 lines (was 150; +30 for per-story pairing
                       and the t-test sensitivity branch)
- `cli.py` addition:  ~70 lines
- Tests:              ~150 lines
- Documentation:      ~50 lines (this proposal becomes the doc)

**Total:** ~450 lines. ~1–1.5 work-days if uninterrupted.

## 10. Open questions resolved by the council

| Question (rev. 1) | Council resolution |
|---|---|
| N=5 or N=10? | **N=20 default**, N=10 as `--pilot`. Codex's framing. |
| Wilcoxon vs paired t-test | Wilcoxon **primary**, paired t-test **sensitivity**, Shapiro-Wilk recorded. Codex's framing. |
| Per-run vs per-story pairing | **Per-story primary**, per-run sensitivity. Unanimous. |
| Embedding-model variance | Pinned by version + CPU mode; not a primary variance source for this experiment. Carried into reproducibility doc. |

## 11. New open questions (post-council)

1. **Story count.** If the benchmark has fewer than ~20 stories, the
   per-story pairing axis loses power. A pilot run on the actual
   dataset will reveal whether per-story is viable or whether per-run
   has to step up to primary. (Codex: "If you can expose per-story F1
   from the existing pipeline without rewriting its logic, that should
   be the primary comparison axis." — confirmed by reading
   `pipeline.py`.)
2. **Multiple-comparison correction.** We report paired tests for F1,
   precision, and recall. Strictly we should apply Bonferroni or
   Holm-Bonferroni to the family of three. To keep the headline simple,
   I propose: F1 is the pre-registered primary outcome; precision and
   recall are reported with adjusted p-values as secondary outcomes.
3. **Ollama seed handling.** If we can pass a per-run seed to Ollama,
   `--seed-offset K` can produce reproducible run sequences. If not,
   the methodological-reproducibility claim is weaker (same protocol →
   statistically equivalent result, but not the same run sequence).
   Worth investigating during implementation.
```

## Project state (snapshot)

- Started: 2026-05-03T09:00:28.963Z
- Total leaves: 5
- Completed: 4
- Failed: 0
- Remaining: 1
- Last green commit: f22b8d3ff19716a68a47e458e164d05c602639f1
- Mode: LIVE
- **Your goal: `g5`** — see `.autopilot/goals/g5.md`

## All leaf goals (status)

- [x] **g1**: Add multi_run dataclasses and paired statistical primitives
- [x] **g2**: Add per-run and per-story aggregation functions
- [x] **g3**: Add sequential N-run pipeline orchestrator
- [x] **g4**: Add report builder and JSON serializer
- [~] **g5**: Wire compare-runs CLI command and export aggregator ← YOU ARE HERE

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

### g1: Add multi_run dataclasses and paired statistical primitives

Create src/secreqgen/evaluation/multi_run.py with the RunStatistics and PairedTestResult dataclasses and the two pure statistical functions: cohens_d_z_paired (returns numeric d_z plus a Cohen-1988 label) and paired_test_with_sensitivity (Wilcoxon as primary, paired t-test as sensitivity, Shapiro-Wilk on the differences for normality reporting). No I/O, no pipeline imports. Foundation for every subsequent leaf.

Implemented in commit: `192ae7e8f591737dead98c8462f11741b0a32e3c`

### g2: Add per-run and per-story aggregation functions

Extend multi_run.py with three aggregation helpers that read the existing pipeline output shape: aggregate_runs (mean/std/min/max per metric across N runs, separately for baseline and RAG), collect_per_story_f1 (story_id -> list of per-run F1 values), and per_story_paired_values (returns equal-length baseline/rag lists for any named metric, one entry per story, averaged across runs). Pure functions. Tests build real EvaluationPipelineResult fixtures so we exercise the actual data path the proposal commits to.

Implemented in commit: `e99b51328ebc9eadf7af0e7974f8498c820d0add`

### g3: Add sequential N-run pipeline orchestrator

Add run_multi_evaluation to multi_run.py: invoke pipeline.run(stories_path, ground_truth_path) sequentially N times, collect the per-run EvaluationPipelineResult objects in order, and forward an optional progress callback (current, total, phase). Sequential by design (Ollama is single-threaded). The pipeline is mocked in unit tests via MagicMock; an end-to-end test against real Ollama would be a separate manual smoke step.

Implemented in commit: `c4992be71c32930187a8bff70fb85ee3d6fc84d2`

### g4: Add report builder and JSON serializer

Add the MultiRunReport dataclass plus build_multi_run_report (orchestrates aggregate_runs, runs paired tests on BOTH per-story (PRIMARY) and per-run (sensitivity) axes for F1, computes d_z) and serialize_report (pretty JSON for archival). The per-story result must have n_pairs == story count; the per-run result must have n_pairs == run count. JSON output must round-trip through json.loads without error.

Implemented in commit: `f22b8d3ff19716a68a47e458e164d05c602639f1`
