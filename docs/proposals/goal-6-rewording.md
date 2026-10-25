# Proposal: Reword Thesis Goal #6 (Greek + English)

*Date: 2026-05-03 (rev. 2 — recommendation flipped to Option A per council).*
*Author: drafted with Claude as a thesis-methodology aid.*
*Audience: George (decision), then Prof. Kotzanikolaou for sign-off.*

---

## 0. Council-driven revision (rev. 2)

The first draft recommended **Option B** (a specific commitment to
N=10, paired Wilcoxon, and Cohen's d in the proposal text). The
3-agent council on 2026-05-03 (council-20260503-080229) flagged this
as politically risky:

* **Codex:** "Option B for Goal #6 is academically stronger but
  politically riskier before supervisor sign-off. A hybrid is safer:
  commit to repeated-run statistical comparison of the actual
  pipeline, but leave exact `N` and the final paired test as
  approved protocol details."
* **Claude:** "Get supervisor sign-off on Goal #6 specifics *before*
  committing Option B in writing — use Option A as the negotiating
  position if sign-off lags."
* **Gemini:** "Adopt Goal Option A (flexible) until you can validate
  the local execution time constraints."

All three agents converge on **Option A as the right starting
position**, with the protocol specifics (N, paired test choice,
`d_z`) committed in the methodology chapter (Chapter 6) rather than
in the proposal. This rev. 2 reflects that change.

---

## 1. Why this proposal exists

Thesis Goal #6 in the Greek proposal (`Thesis_Plan_GGeorgopoulos_MPKED24005_3-1-2026.pdf`,
*Ενότητα 2 — Στόχοι*) is, as currently worded, about
"τεκμηρίωση μιας reproducible μεθοδολογίας αξιολόγησης" — documentation
of a reproducible evaluation methodology.

When the autopilot decomposed this goal into sub-tasks, it interpreted
"reproducible" as **numerical reproducibility** (byte-stable run
outputs, frozen mock-LLM corpus, deterministic harness). The
implementation that followed was vetoed by the post-implementation
council on grounds of **SPEC_MISMATCH**: it bypassed the real
evaluation pipeline and replaced the LLM with a mock, which would
have made the thesis's empirical claim untestable.

The interpretation was wrong, but the *wording* of Goal #6 left the
door open to it. This document proposes language that closes the door
without committing the supervisor to a specific protocol prematurely.

> **Action item before editing the PDF:** copy the exact current Greek
> wording of Goal #6 into the placeholder block in §2 below. I do not
> have the PDF source text accessible in this draft.

## 2. Original wording (insert here)

**Greek (current):**

```
Στόχος 6: <PASTE EXACT TEXT HERE FROM PDF, Ενότητα 2>
```

**English working translation (best guess from context — please verify):**

> Goal 6: Documentation of a reproducible evaluation methodology.

## 3. The problem

"Reproducible" is overloaded. To a software engineer, it sounds like
"running it again gives the same numbers." To a research methodologist
following Goodman, Fanelli & Ioannidis (2016), it can mean any of
three distinct claims (methods reproducibility, results reproducibility,
inferential reproducibility). For an LLM-driven system, **results
reproducibility in the strict numerical sense is unachievable** without
replacing the LLM with a mock — which invalidates the thesis's
empirical claim. The thesis claims **methods + inferential
reproducibility**, not numerical results-reproducibility.

Goal #6 should explicitly commit to that, but should leave the exact
*protocol* (N, choice of paired test, choice of effect-size variant)
to the methodology chapter, not the proposal.

## 4. Reworded options

### Option A — Methods + inferential reproducibility (RECOMMENDED, per council)

**Greek:**

```
Στόχος 6: Τεκμηρίωση μιας μεθοδολογικά αναπαράξιμης διαδικασίας
αξιολόγησης — δηλαδή πλήρης καταγραφή του πειραματικού πρωτοκόλλου,
των δεδομένων, των εκδόσεων εργαλείων και των παραμέτρων, ώστε ένας
άλλος ερευνητής να μπορεί να επανεκτελέσει το πείραμα και να φτάσει
στο ίδιο επιστημονικό συμπέρασμα. Οι ποσοτικοί δείκτες αναφέρονται με
χαρακτηρισμό μεταβλητότητας πάνω από επαναλαμβανόμενες εκτελέσεις
και με στατιστικό έλεγχο, με τις λεπτομέρειες πρωτοκόλλου να
ορίζονται στο Κεφάλαιο 6.
```

**English:**

> Goal 6: Document a *methodologically* reproducible evaluation
> procedure — that is, fully record the experimental protocol, the
> data, the tool versions, and the configuration parameters, such
> that another investigator can re-execute the experiment and reach
> the same scientific conclusion. Quantitative metrics are reported
> with variance characterisation across repeated executions and with
> a paired statistical test, with the protocol details specified in
> Chapter 6.

**Why this is the recommended option:**

* Closes the byte-stability misreading (the autopilot's failure mode)
  by explicitly committing to "the same scientific conclusion" rather
  than "the same numbers."
* Commits to *some* form of variance characterisation and statistical
  testing without locking N, paired-test choice, or effect-size
  variant. Those decisions live in Chapter 6, where they can be
  refined as the implementation work progresses.
* Politically safe to send to the supervisor: nothing here is so
  specific that a reasonable supervisor would push back; nothing here
  contradicts a reviewer's expectations.

### Option B — Same as A but with protocol specifics committed (NOT RECOMMENDED at this stage)

**Greek:**

```
Στόχος 6: ...όπως στο Option A, με την επιπλέον δέσμευση ότι κάθε
σύγκριση RAG-vs-baseline εκτελείται σε N=20 επαναλήψεις, με κύριο
στατιστικό έλεγχο paired Wilcoxon signed-rank σε per-story F1
διαφορές και αναφορά μεγέθους επίδρασης Cohen's d_z.
```

**English:**

> Goal 6: ...as Option A, with the additional commitment that every
> RAG-vs-baseline comparison is run with N=20 repetitions, with a
> primary paired Wilcoxon signed-rank test on per-story F1
> differences, and reporting Cohen's `d_z` effect size.

**Why this is NOT recommended at this stage:**

* The protocol specifics belong in the methodology chapter, where they
  can be defended in detail and adjusted if the empirical work reveals
  problems.
* Locking N=20 in the proposal makes the proposal harder to amend if
  the dataset turns out to support only a smaller N (e.g. due to
  Ollama latency on the available hardware).
* If the supervisor signs off on the supervisor pitch (§6) and is
  comfortable with the protocol specifics, Option B can be adopted as
  a follow-up amendment with low cost. The reverse — going from B
  back to A after the supervisor has signed B — is more awkward.

### Option C — Option A with explicit Goodman et al. anchor (alternative, slightly stronger framing)

**Greek (last sentence replaces the corresponding sentence in Option A):**

```
...στο ίδιο επιστημονικό συμπέρασμα (methods + inferential
reproducibility κατά Goodman, Fanelli & Ioannidis 2016).
```

**English (same):**

> ...the same scientific conclusion (methods + inferential
> reproducibility in the sense of Goodman, Fanelli & Ioannidis, 2016).

**Use this if:** the supervisor expects citations even at the
proposal level. Most Greek MSc CS proposals don't, but yours might —
adjust accordingly.

## 5. Recommendation

**Adopt Option A.** It is the council-validated starting position.
The protocol specifics (N=20, per-story Wilcoxon primary, paired
t-test sensitivity, `d_z`) are documented in `compare-runs-design.md`
and `reproducibility-considerations.md` and become part of Chapter 6,
not the proposal text.

If the supervisor wants more specificity in the proposal itself, fall
back to Option C (cited anchor) before considering Option B (full
protocol commitment).

## 6. Supervisor pitch (1 paragraph, English, ready to send)

> "Καθηγητά, after a closer review of evaluation methodology for
> LLM-driven systems, I'd like to refine the wording of Goal 6.
> The current phrasing leaves 'reproducible' open to interpretation,
> and an early implementation attempt confirmed the risk: the team
> read 'reproducible' as 'byte-identical' and built a parallel
> evaluation harness with a mocked LLM, which would have invalidated
> the empirical claim. For a stochastic RAG+LLM pipeline, the
> appropriate claim — following Goodman, Fanelli & Ioannidis (2016) —
> is methods reproducibility plus inferential reproducibility: pin
> the protocol, the data, and the versions; report metrics with
> variance characterisation across repeated runs and with a paired
> statistical test. The protocol specifics (number of runs, choice of
> paired test, effect size) live in Chapter 6 of the thesis. I have
> drafted both a revised wording for Goal 6 (attached, Option A) and
> the corresponding `Reproducibility Considerations` section for
> Chapter 6 that operationalises it. May I send them over for your
> review?"

If approved, the wording change to the Greek proposal PDF is
mechanical (one paragraph) and does not require any re-submission of
the proposal — only a documented amendment in the thesis itself.

## 7. Greek translation note

If you adopt Option A, no special technical terminology is needed
in the proposal text — the technical vocabulary (paired Wilcoxon,
`d_z`, etc.) lives in Chapter 6. Should you adopt Option B or C, the
same convention applies as elsewhere in the proposal: keep
"Wilcoxon", "Cohen's d_z", and similar in English, per the existing
norm of retaining "Recall", "Precision", and "F1-score" in English
in the Greek proposal.

## 8. What changes in the rest of the project after Goal #6 is reworded

If Option A is adopted, three other artefacts should be brought into
alignment, with light edits:

1. **`docs/initial_thesis_goals.md` §7.1** — add "with statistical
   reproducibility (repeated-run variance characterisation and a
   paired test)" to the "Methodology" contribution bullet.
2. **`docs/initial_thesis_goals.md` §5.1** — add a sentence noting
   that all aggregate metrics in Chapter 7 are reported as
   mean ± std over repeated runs.
3. **`docs/initial_thesis_goals.md` §7.4 Deliverables table** — the
   "Results Data" row should explicitly include the per-run results
   (i.e., the `multi_run_report.json` artefacts) and a Markdown
   summary, not only "Raw experimental data."

These edits are small (a few sentences each) and can be made after
the supervisor signs off on the Goal #6 wording itself.
