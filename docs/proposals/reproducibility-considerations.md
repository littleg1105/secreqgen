# Reproducibility Considerations

*Draft — intended as a section of Chapter 6 (Evaluation Methodology). ~1.5 pages.*
*Date: 2026-05-03 (rev. 2 — anchored on Goodman et al. 2016 per council).*

---

## 0. Council-driven revision (rev. 2)

The first draft used a self-coined three-tier taxonomy
(numerical / methodological / statistical reproducibility). The
3-agent council on 2026-05-03 (council-20260503-080229) flagged this
as attackable: a hostile examiner will read a self-coined taxonomy as
relabeling. Both Claude and Codex recommended anchoring on cited
literature instead.

This rev. 2 replaces the self-coined taxonomy with the framework of
**Goodman, Fanelli & Ioannidis (2016)** — *"What does research
reproducibility mean?"* (Science Translational Medicine, 8 (341)) —
which distinguishes **methods reproducibility**, **results
reproducibility**, and **inferential reproducibility**. This framework
is widely cited in the empirical-ML reproducibility literature
(it underpins much of the ACM/NeurIPS reproducibility-checklist
machinery) and is the right anchor for an MSc thesis defence.

---

## 6.X Reproducibility

A core obligation of empirical research is that another investigator,
given the same artefacts, should be able to reach the same scientific
conclusions. For systems that incorporate a Large Language Model
(LLM), "the same conclusions" rarely means "the same numbers." It
means that the *protocol*, the *data*, and the *statistical evidence*
are recoverable.

This section adopts the taxonomy of Goodman, Fanelli & Ioannidis
(2016), names the sources of variation in the SecReqGen evaluation
pipeline, declares what is pinned, and describes the procedure under
which our headline result — a measurable F1 improvement of RAG over a
zero-shot baseline — should be considered reproducible.

### 6.X.1 Three forms of reproducibility (Goodman et al., 2016)

Goodman et al. distinguish three orthogonal claims [1]:

* **Methods reproducibility** — sufficient detail is provided about
  the experimental procedures, data, and analytical code that
  another investigator could exactly repeat the experiment.
* **Results reproducibility** — independent re-execution of the same
  procedure produces the same numerical result. In the original
  framing this is also called "replicability" by some authors; the
  distinction is unimportant for our purposes.
* **Inferential reproducibility** — independent investigators draw
  the same scientific conclusion from either the same data or
  independently-collected data analogous to it.

For systems that invoke a stochastic LLM at inference time, **results
reproducibility in the strict numerical sense is not achievable**
without replacing the LLM with a mock, which would invalidate the
empirical claim. We claim **methods reproducibility** and
**inferential reproducibility** for this thesis. The remainder of
this section justifies that scope.

### 6.X.2 Sources of variation

The SecReqGen pipeline has four identifiable sources of run-to-run
variation:

1. **LLM sampling.** Generation passes through Ollama with
   `temperature = T_LLM` (Section 3.X). Even at T=0, modern LLMs are
   not byte-stable across runs because (a) the underlying serving
   stack batches requests together and floating-point reductions in
   batched matmul are non-associative, and (b) hardware-level
   non-determinism (CUDA/Metal kernels, thread scheduling) can perturb
   logits.
2. **Embedding-model floating-point variation.** The
   `sentence-transformers` model used for semantic matching is
   deterministic in pure-CPU mode for a fixed model version, but
   produces slightly different vectors on different hardware (Apple
   Silicon vs. x86_64 vs. CUDA).
3. **Library versions.** Bumping the major or minor version of
   `langchain`, `chromadb`, `sentence-transformers`, or the embedding
   model itself can change retrieval behaviour and therefore the
   downstream RAG generations.
4. **Vector-store seed and document order.** ChromaDB collection
   construction is order-sensitive in some failure modes; for the
   purposes of reproducibility we pin both the corpus and its
   ingestion order.

### 6.X.3 What is pinned (methods reproducibility)

The following are recorded in the experiment configuration that
accompanies every reported run:

| Component | Pinned value | Recording location |
|-----------|--------------|--------------------|
| LLM model and tag | `qwen3:30b` (or whichever was used) | `pyproject.toml`, run config |
| Ollama version | recorded at run start | run report |
| `sentence-transformers` version | `^2.2.0` | `pyproject.toml` |
| Embedding model | `all-MiniLM-L6-v2` | `secreqgen.config` |
| Embedding device | `cpu` (for cross-machine determinism) | `secreqgen.config` |
| Match threshold | `0.85` (default) | run report |
| Partial threshold | `0.70` (default) | run report |
| Top-K retrieval | `5` (default) | run report |
| Retrieval similarity threshold | `0.0` | run report |
| User stories dataset | `tests/evaluation/data/...` (versioned) | git SHA |
| Ground-truth dataset | `tests/evaluation/data/...` (versioned) | git SHA |
| Generation prompt template | `secreqgen.generation.prompts` | git SHA |
| Code revision | git commit SHA | run report |

The reproducibility report emitted by `secreqgen evaluate compare-runs`
(Section 4.X.X) includes all of the above plus the per-run results.
This satisfies the **methods reproducibility** claim of Goodman et al.
[1]: another investigator has the procedure, the data, and the code,
sufficient to repeat the experiment.

### 6.X.4 What is not pinned, and why

We deliberately do not attempt:

* **Pinning a CUDA/Metal RNG seed inside Ollama.** Even if it were
  exposed, batching-level non-determinism would still apply, and the
  resulting "byte-stability" would be a property of the serving stack,
  not of the experiment.
* **Mocking the LLM with a deterministic stand-in.** A previous
  prototype of the harness took this route and was rejected on
  review: a mocked LLM does not test the system whose performance the
  thesis claims to measure. Numerical results-reproducibility
  purchased at the cost of ecological validity is not reproducibility
  of the result.
* **Disabling Ollama's batched generation.** This would change the
  performance profile of the system the thesis evaluates and thereby
  invalidate runtime measurements.

### 6.X.5 The variance-characterisation protocol (inferential reproducibility)

For every headline metric reported in Chapter 7, we run the full
`EvaluationPipeline` N times under each condition (baseline vs. RAG-
enhanced) on the same dataset and report the result as **mean ±
standard deviation**. The default N for thesis-grade tables is N=20
(N=10 is used during development and is flagged as `--pilot` in the
output). Runs are sequential and use distinct LLM seeds across runs
but identical prompt, retriever state, and configuration.

The **primary inferential test** is a paired Wilcoxon signed-rank
test on **per-story F1 differences** (one pair per story in the
dataset; the per-story F1 used for each story is the mean F1 of that
story over the N runs under each condition). The choice of per-story
pairing as the primary axis maximises statistical power: with a
benchmark of ~40 user stories the test runs over ~40 paired
observations, rather than the N=20 paired observations available
under run-level pairing.

A **paired t-test** is reported as a sensitivity check, alongside a
Shapiro-Wilk test of the paired differences for normality. Reporting
both eliminates the parametric/non-parametric attack: if the t-test
and Wilcoxon agree, the conclusion is robust to the choice of
inferential machinery; if they disagree, the Shapiro-Wilk result
indicates which to trust.

The paired-sample effect size is reported as **Cohen's `d_z`** —

`d_z = mean(rag − baseline) / std(rag − baseline)`

— with Cohen's (1988) interpretation thresholds (small ≈ 0.2,
medium ≈ 0.5, large ≈ 0.8). The `z` subscript distinguishes this from
`d_av` and `d_rm`, the other paired variants reviewers may expect; we
name it explicitly to avoid the "which Cohen's d?" attack.

A run-level paired analysis (N=20 pairs of aggregate F1) is reported
as a secondary sensitivity result. Its agreement with the per-story
primary analysis confirms that the conclusion is robust to the
pairing axis.

This protocol satisfies **inferential reproducibility** in the sense
of Goodman et al. [1]: an independent investigator who follows the
protocol and obtains an analogous dataset of LLM generations should
draw the same scientific conclusion (RAG > baseline on F1 with a
paired-test p-value supporting the claim and an effect size of
non-trivial magnitude).

### 6.X.6 Tooling

The protocol is implemented as `secreqgen evaluate compare-runs`
(Section 4.X.X), which reuses the existing `EvaluationPipeline`
unchanged. It accepts the same flags as `secreqgen evaluate run` plus
`--runs N` and `--pilot`. It emits a JSON report
(`multi_run_report.json`), a CSV table for plotting
(`per_run_metrics.csv`), and a Markdown summary
(`multi_run_summary.md`) that is the canonical artefact cited from
Chapter 7. The Markdown summary contains the mean ± std table, the
per-story primary test, the per-run sensitivity test, the
Shapiro-Wilk normality result, and `d_z`, in the form actually
reproduced in the thesis.

### 6.X.7 What another investigator can reproduce

Following Goodman et al. [1], an investigator who repeats this
thesis's experiment on a machine of comparable architecture (Apple
Silicon or recent x86_64 with the same Ollama tag installed) can
reproduce:

* **Methods:** the same datasets, the same prompts, the same
  configuration, the same `secreqgen evaluate compare-runs` invocation.
* **Results (in expectation, not byte-for-byte):** an aggregate F1
  improvement of RAG over baseline within a confidence interval
  consistent with the one we report in Chapter 7.
* **Inference:** the same scientific conclusion — RAG produces a
  higher-F1 distribution than baseline on this benchmark, supported
  by a paired Wilcoxon p-value and a `d_z` effect size of comparable
  magnitude.

What they will not reproduce is the byte-level text of any individual
generated requirement. That is the price of building on a stochastic
LLM, and it is the correct price to pay for a system that has to
perform realistically on real input.

---

## References

[1] Goodman, S. N., Fanelli, D., & Ioannidis, J. P. A. (2016). *What
    does research reproducibility mean?* Science Translational
    Medicine, 8(341), 341ps12.
    https://doi.org/10.1126/scitranslmed.aaf5027

[2] Cohen, J. (1988). *Statistical power analysis for the behavioral
    sciences* (2nd ed.). Lawrence Erlbaum Associates. (Effect-size
    interpretation thresholds.)

[3] Lakens, D. (2013). *Calculating and reporting effect sizes to
    facilitate cumulative science: A practical primer for t-tests and
    ANOVAs.* Frontiers in Psychology, 4, 863. (Defines `d_z` and the
    other paired variants.)

---

## Note for the supervisor

If the supervisor asks "but isn't reproducibility a hard requirement
of empirical research?" — yes, and that is exactly what this section
defends, using the most-cited recent framework for reproducibility in
the biomedical and computational-research literature [1]. We claim
**methods reproducibility** and **inferential reproducibility** in
Goodman et al.'s sense, and we explain why **results reproducibility**
in the strict numerical sense would require replacing the system
under test with a mock — which would invalidate the empirical claim.

If the supervisor wants a stronger numerical guarantee (e.g. "the
same generated text every time"), they are asking for a different
research question — one about *symbolic* requirement generation
rather than LLM-driven requirement generation — and we should have
that conversation before changing the methodology.

---

## TODO before this becomes Chapter 6.X

* [ ] Replace placeholder N=20 / N=10 with the values actually used
      in the final results.
* [ ] Replace `T_LLM` placeholder with the actual temperature in §6.X.2.
* [ ] Cross-reference correct subsections in §3.X and §4.X.X once the
      thesis chapter numbering settles.
* [ ] Translate to Greek for the Greek-language thesis body.
* [ ] Verify the Lakens 2013 citation matches the convention used
      elsewhere in the bibliography.
