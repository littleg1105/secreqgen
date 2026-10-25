# Next steps

*Date: 2026-05-03 (rev. 2 — post-council).*

## Status

The three proposals were validated by a 3-agent council on 2026-05-03
(council-20260503-080229) and revised into rev. 2 incorporating the
unanimous and strongest-divergent recommendations. Specifically:

* **Per-story pairing** is now the primary statistical axis (was per-run).
* **N=20** is the default, N=10 is `--pilot` only.
* **Wilcoxon primary, paired t-test sensitivity** (was either-or).
* **`d_z`** named explicitly (was generic "Cohen's d").
* **Goodman, Fanelli & Ioannidis (2016)** anchors the
  reproducibility framing (was self-coined three-tier taxonomy).
* **Goal #6 → Option A** as the recommendation (was Option B).

These are all in the rev. 2 versions of the proposal docs in this
directory. The next steps below assume those revisions are accepted.

## Order of operations

1. **Clean up the autopilot's vetoed work on master** (manual — needs
   user direction; see §1 below). The 12 commits are preserved on the
   `autopilot/reproducibility-harness` branch, so this is reversible.
2. **Send Goal #6 rewording (Option A) to supervisor** using
   `goal-6-rewording.md` §6 as the pitch text. Wait for sign-off
   before treating the proposal text as committed.
3. **Implement `secreqgen evaluate compare-runs`** per the rev. 2
   `compare-runs-design.md` — ~1–1.5 work-days. This is now safe to
   run via autopilot live mode (see §3 below) because the design has
   been council-validated.
4. **Run the harness for real:** `--runs 20` baseline + RAG, capture
   `multi_run_report.json`. (Use `--pilot` first to verify wiring on
   your hardware before committing to a 20-run wall-clock.)
5. **Write Chapter 6.X** using `reproducibility-considerations.md`
   rev. 2 as the draft, plus the actual numbers from step 4.

## §1 — Manual cleanup commands (run yourself)

Run from `/Users/georgeg/Documents/Uni Docs/Thesis/coded_project/secreqgen`.
The autopilot's 12 commits are safely preserved on the
`autopilot/reproducibility-harness` branch (already created), so
nothing is lost.

```bash
# Optional pre-check — you should see the autopilot branch + your
# uncommitted manual analysis in stash:
git branch                                    # should list autopilot/reproducibility-harness
git stash list                                # should show: manual-10-project-eval-analysis-PRESERVE

# Reset master back to the published state. Destroys the 12
# autopilot commits FROM MASTER ONLY (they remain reachable via the
# preservation branch). Read the warning carefully:
git reset --hard origin/master

# Restore your manual 10-project evaluation analysis to docs/log.md:
git stash pop

# Remove the autopilot's pre-commit hook (it's keyed off
# .autopilot/state.json and would block edits to .autopilot/ specs;
# we don't want it active on master):
rm .git/hooks/pre-commit

# Optional: archive the autopilot's run artefacts so they don't
# confuse you in `git status`. They're gitignored already, but
# moving them keeps the directory tidier:
mv .autopilot .autopilot-archived-2026-05-03

# Sanity check:
git status                                    # docs/log.md should show as modified
git log --oneline -3                          # should be the published origin/master tip
```

## §2 — Council command (already executed; preserved for reference)

The validation council that produced the rev. 2 revisions:

```bash
cd "/Users/georgeg/Documents/Uni Docs/Thesis/coded_project/secreqgen"

bun run /Users/georgeg/Local_Documents/Dev/yogirk_agent_council/agent-council/src/council.ts \
  --question-file /tmp/thesis-methodology-q.txt \
  --context docs/proposals/compare-runs-design.md,docs/proposals/reproducibility-considerations.md,docs/proposals/goal-6-rewording.md \
  --skip-preflight
```

Session: `council-20260503-080229`
Storage: `/Users/georgeg/.council/agent-council/council-20260503-080229/`
Viewer: `/Users/georgeg/.council/agent-council/council-20260503-080229/viewer.html`

If you re-run the council on the rev. 2 docs (e.g. to validate the
revisions themselves), update `/tmp/thesis-methodology-q.txt` to ask
"these are the revised proposals — any remaining holes?" rather than
the original question.

## §3 — Autopilot live mode (implementation)

This is the step that actually writes the `compare-runs` code. It is
safe to run NOW because:

* The design has been council-validated (no SPEC_MISMATCH veto pattern).
* The design is a thin wrapper over the existing `EvaluationPipeline`,
  which means the autopilot can't accidentally rewrite evaluation
  logic.
* The frozen spec is `docs/proposals/compare-runs-design.md` rev. 2,
  which the autopilot's pre-commit hook will pin against modification.

Recommended invocation (run from the secreqgen repo root):

```bash
cd "/Users/georgeg/Documents/Uni Docs/Thesis/coded_project/secreqgen"

bun run /Users/georgeg/Local_Documents/Dev/yogirk_agent_council/agent-council/bin/autopilot \
  --live \
  --goal-doc docs/proposals/compare-runs-design.md \
  --profile python-poetry
```

Adjust the autopilot's exact CLI per its current `--help` output;
the conceptual shape is "point it at the rev. 2 design doc as the
goal spec, and tell it to use the python-poetry profile."

The autopilot will:

1. Run a bootstrap council to decompose the design into leaf goals
   (each tied to a frozen test spec).
2. Spawn a fresh `claude -p` per leaf goal, sandboxed with the
   permission allowlist for the python-poetry profile.
3. Verify each leaf in a clean git worktree with the project's
   `pytest` test command.
4. Advance on green; invoke a stuck-rescue council on stuck.
5. After all leaves green, run a final-review council with VETO-only
   authority. The veto codes (SPEC_MISMATCH, PLACEHOLDER_LOGIC,
   TEST_ONLY_CHEAT, SCOPE_BREACH) cover the failure modes that bit
   the previous run.

Expected wall-clock: 1–3 hours depending on test runtime and how
many iterations the implementing claude needs per leaf. The autopilot
emits heartbeat lines every 30s so you can see whether it's still
making progress.

## §4 — What "council recommended now" means here (historical note)

This is the **right** moment to have run the validation council
(already done, council-20260503-080229) because:

1. **The cost of being wrong is high.** Implementing the wrong
   `compare-runs` design = another day of work that gets vetoed.
2. **The proposals are concrete enough to argue about.** A council on
   "should I build a thing for reproducibility?" would be a waste; a
   council on "is THIS specific design defensible?" is actionable.
3. **The agents disagreed productively.** Codex pushed on stats
   (N=20, `d_z`, primary/sensitivity test split); Claude pushed on
   citations (Goodman et al. 2016); Gemini pushed on the pairing
   axis. That's the variance you want pre-implementation.

The council did its job. The remaining unresolved decisions —
supervisor sign-off on Goal #6 wording, exact `--runs` value for the
final thesis tables, multiple-comparison correction — are decisions
that humans should own, not council ones.
