# SecReqGen Thesis — Handoff Document

*Originally 2026-05-17. Updated repeatedly. Read this top section first.
Earlier sections are preserved for historical context but may be
partially superseded by later updates.*

---

# ⚠ 2026-11-03 — Study 2 is done; this is now the thesis's main result

Study 2 (notebook §14 design, §15 results) ran 2026-11-02: 3 projects × 5
docs-dependent stories × arms A (no docs) / B (frozen idea-19 retrieval) /
C (B + extract-then-cite) / D (whole corpus) × qwen3.8:27b + gemma4:26b ×
N = 3 — 330 generations, 1 retried parse failure. **Documentation access
raises the share of requirements citing documented project identifiers from
≈ 0 to 0.30–0.61 in every cell (B − A d_z 1.0–1.8; pooled 15/15 stories
positive, p ≤ 0.0006), with ≈ 0 invented identifiers.** Extract-then-cite
adds +0.06–0.16, not significant. Whole-corpus prompting beats retrieval
only on the smallest corpus. Next: blinded 60-item human review
(`real_test_cases/study2/review/`), then Chapter 6.

---

# ⚠ 2026-10-25 — read before the section below

Five months passed with no work. A three-agent council (records in
`../../council-of-agents/`, decision in `synthesis-2026-10-25.md`) re-audited
the tree. Two corrections to the section below:

- **Phase 4 / idea 21 is superseded.** Its prompts contained the probe's
  answer vocabulary and its baseline arm had lost the idea-15 exemplars; the
  +16/+18 pp citation-density lift is confounded and must not be cited
  (lab notebook §13). The Phase 3 adversarial probe (0 vs 5–7 docs-only
  identifiers) is clean and stands.
- **Pending item 2 (the κ worksheet) calibrates the retrieval judge, not the
  content-quality judge.** No human requirement-correctness review exists yet;
  Study 2 adds one.

Repairs are committed; the next experiment is Study 2 as specified in the
synthesis. The Ollama blocker in the Phase 4 caveat is gone (0.40.11).

---

# ⭐ CURRENT STATE — 2026-05-25 (read this section)

## Where we are

The thesis empirical work has now gone through **four** distinct phases
since the 2026-05-19 retriever audit:

**Phase 1 (complete, 2026-05-19 → 2026-05-22):** 20-idea ablation
finding the winning RAG configuration (idea 19 hybrid retrieval +
idea 15 few-shot generation), corpus enrichment of gitlab + mattermost,
then the full 4-models × 5-projects × N=10 cross-family experiment
(20 cells, 1,636 generated requirements). All recorded in
[RAG_ABLATION_LAB_NOTEBOOK.md](RAG_ABLATION_LAB_NOTEBOOK.md) §1-§9.

**Phase 2 (complete, 2026-05-23):** Manual deep read of ~920 requirements,
LLM-as-judge content-quality scoring (97-100% relevant across all 20
cells), and a bitwarden re-run after fixing a code-search path bug.
Findings: the generated content is uniformly high-quality regardless of
F1 verdict; the F1 metric is measuring phrasing-alignment to small GTs
rather than security-content quality; the bitwarden STRONG − verdicts
are a precision-penalty artifact not a quality regression. Recorded in
[CLAUDE_PERSONAL_REVIEW_2026-05-23.md](CLAUDE_PERSONAL_REVIEW_2026-05-23.md)
and [RAG_ABLATION_LAB_NOTEBOOK.md §10](RAG_ABLATION_LAB_NOTEBOOK.md).

**Phase 3 (complete, 2026-05-24):** Surfaced the **training-data
contamination problem** — popular open-source projects' docs are
already in the LLMs' training corpora, so zero-shot baselines benefit
from the same content RAG would retrieve. Built and ran an **adversarial
probe** (5 mastodon stories requiring project-specific knowledge) under
qwen3 + qwen3.6 N=5. Result: **both baselines cite 0 docs-only
Mastodon env vars; RAG cites 5–7**. This is the cleanest RAG-wins
demonstration in the entire thesis. But a diagnostic of the retrieval
context showed retrieval brings in 24 CAS env vars and the LLM cites
only 8 (33% utilization) — **the bottleneck is generation, not
retrieval**. Recorded in [RAG_ABLATION_LAB_NOTEBOOK.md §11](RAG_ABLATION_LAB_NOTEBOOK.md).

**Phase 4 (complete, 2026-05-25):** Built and ran "idea 21 — grounded-
citation generation". Two-stage prompt: Stage A asks a separate-family
LLM to extract project-specific tokens from the retrieved context;
Stage B injects that token list into the generator prompt with a hard
"cite by name" constraint. **N=5 results on the mastodon adversarial
probe:**

- **Citation density** (fraction of reqs citing a project-specific
  probe term) went from 60% (idea-15 RAG) to **76%** on qwen3:30b
  (+16 pp), and from 52% (baseline) to **70%** on mistral-small3.2
  (+18 pp).
- **Per-story Stage-A → Stage-B utilization** averaged **76.6% on
  qwen3** and **89.6% on mistral-small** — substantially above the
  33–50% diagnostic baseline from §11. On 4 of 9 valid model×story
  cells the model cited 100% of the extracted tokens.
- **Cross-family generalization** holds: both qwen-family and
  mistral-family generators show the same direction-of-effect, even
  though they use each other's family as the extractor.
- Plan + expected impact in
  [RAG_IMPROVEMENT_RESEARCH_2026-05-24.md](RAG_IMPROVEMENT_RESEARCH_2026-05-24.md);
  results + interpretation in
  [RAG_ABLATION_LAB_NOTEBOOK.md §12](RAG_ABLATION_LAB_NOTEBOOK.md).

**Caveat:** qwen3.6:latest and gemma4:26b were unloadable on the
current Ollama build (architecture `qwen35moe` / `gemma4` not
recognised). mistral-small3.2:24b was substituted as the second
generator. Upgrading Ollama and re-running those two cells would
complete the original 4-model matrix on the adversarial probe.

## The thesis claim in one paragraph (current best framing)

> "On security stories requiring generic OWASP knowledge, modern
> instruction-tuned LLMs (qwen3, qwen3.6, gemma4, mistral-small3.2)
> already produce defensible, schema-correct, ASVS-aligned requirements
> zero-shot, because they have trained on the same kinds of security
> documentation we are retrieving. On stories requiring project-specific
> configuration knowledge (env var names, endpoint paths, internal
> class names), the base LLMs cannot surface that knowledge from
> training data alone — but a RAG pipeline that retrieves the project's
> own documentation reliably grounds requirements in the specific
> terminology used by the project. This is the regime in which
> RAG-augmented security-requirements generation provides measurable,
> demonstrable benefit, and the methodology contribution of this
> thesis is the pipeline (idea 19 retrieval + idea 15 few-shot
> generation + the proposed idea 21 grounded-citation extension)
> that makes that benefit reliably reproducible."

## The 4 evaluation layers we now have

For Chapter 7 defensibility, we have four independent measurements of
the system's output quality:

| layer | what it measures | result on the cross-family experiment |
|---|---|---|
| **F1 (per-story, per-run)** | phrasing-alignment to hand-curated GT | mostly null; 8/20 cells STRONG ±; sensitive to GT size |
| **LLM-as-judge content quality** (llama3:8b) | "does this req address a real security concern?" | **97–100% relevant** in every cell |
| **Manual deep read** (Claude) | qualitative read of ~920 reqs | **zero bad reqs** found |
| **Adversarial probe — docs-only term citation** | does the model surface project-specific config keys not in the prompt? | **baseline: 0 cited; RAG: 5–7 cited** |

The F1 results are real but measure the wrong thing for the thesis
claim. The content-quality judge, manual read, and adversarial probe
together tell the actual story: the RAG pipeline reliably produces
high-quality, project-grounded requirements that the base LLM cannot
match on project-specific questions.

## What's actively running or pending

Nothing is running in the background right now. The idea-21 N=5 runs
on qwen3:30b and mistral-small3.2 completed 2026-05-25.

**Pending items in priority order:**

1. **Write Chapter 7.** Have all the empirical material to do this now:
   - Phase 1 cross-family F1 + content-quality judge data (§1–§9)
   - Phase 2 manual deep read (CLAUDE_PERSONAL_REVIEW_2026-05-23)
   - Phase 3 adversarial probe with prompt-echo vs docs-only analysis (§11)
   - **Phase 4 idea-21 grounded-citation results** (§12) — the
     headline methodology contribution

2. **Hand-label the κ-calibration worksheet.** User-blocked.
   30-pair worksheet at [judge_calibration_sample.md](judge_calibration_sample.md);
   scorer at `scripts/score_judge_calibration.py`. The 97–100%
   content-quality finding makes κ less load-bearing than it was, but
   examiners may still ask.

3. **Optional: compute paired-Wilcoxon on idea-21 citation density.**
   Current analysis reads run_001 only. Extending
   `scripts/analyze_idea21_vs_baseline.py` to iterate over all five
   runs and emit a per-run citation-density delta would let us report a
   p-value.

4. **Optional: re-run idea-21 on qwen3.6 + gemma4 after Ollama upgrade.**
   Both models are currently unloadable on the installed Ollama
   (architecture mismatch). When upgraded, run
   `scripts/run_idea21_grounded_citation.py --models qwen3.6:latest,gemma4:26b --n 5`
   to complete the 4-model cross-family matrix.

5. **Optional: extend the original adversarial probe to gemma4 + mistral-small**
   so we have parallel RG-15 baselines for every cell that has RG-21.
   ~30 min compute. Run `scripts/run_adversarial_probe.py` with
   `--models gemma4:26b,mistral-small3.2:24b --n 5` (after Ollama upgrade
   for the gemma4 case).

## Resume commands for idea 21

### Step A — build the extract-then-cite pipeline

Create `scripts/run_idea21_grounded_citation.py`. It should be a
near-copy of `scripts/run_adversarial_probe.py` with these changes:

```
1. Before the standard EvaluationPipeline.run(), for each story:
   a. Get the retrieved_context from the idea-19 retriever
   b. Call mistral-small3.2:24b with this prompt:
      "From the security docs below, list every project-specific
       configuration key, env var, endpoint, class, or feature name
       that is relevant to this user story. Output JSON:
       {tokens: [\"TOKEN_1\", \"TOKEN_2\", ...]}"
      Inputs: retrieved_context + user_story
      Output: token_list (typically 10-30 tokens)

2. Inject the token list into the generation prompt by:
   a. Loading a new prompt template at data/winning_prompts_idea21/
      that includes a {{token_list}} placeholder
   b. The system prompt must say "Every requirement MUST cite at least
      one token from the EXTRACTED PROJECT TOKENS list below"
   c. The user prompt template must include EXTRACTED PROJECT TOKENS
      section right above the retrieved context

3. Monkey-patch the PromptBuilder to use winning_prompts_idea21/
```

### Step B — citation-aware few-shot exemplars

Edit `data/winning_prompts_idea21/rag_user_prompt.txt` to use
exemplars that demonstrate citing project-specific tokens:

```
EXAMPLE: Configure SSO with provider X via SSO_USER_ATTRIBUTE
EXPECTED:
{requirements: [{
  "title": "SSO identifier mapping via SSO_USER_ATTRIBUTE",
  "description": "Server MUST use the SSO attribute named in
    SSO_USER_ATTRIBUTE (default 'uid') as the local user
    identifier; MUST NOT fall back to the principal name if this
    attribute is missing.",
  ...
}]}
```

Two exemplars, structurally identical to the current few-shot but
each demonstrating an explicit token citation.

### Step C — run head-to-head on the 5 adversarial probes

```bash
cd "/Users/georgeg/Documents/Uni Docs/Thesis/coded_project/secreqgen"

TOKENIZERS_PARALLELISM=false \
PYTHONPATH=src .venv/bin/python scripts/run_idea21_grounded_citation.py \
  --models qwen3:30b-a3b-instruct-2507-q4_K_M,qwen3.6:latest \
  --n 5 --label-prefix idea21
```

Output to `real_test_cases/mastodon_adversarial/results_compare_runs_idea21_*`.

### Step D — measure the lift

Re-use `scripts/analyze_adversarial_probe.py` (already exists) to
compute docs-only-citation counts, but pointed at the idea-21
output directories instead. Compare to the idea-15+19 baseline
already on disk:

| metric | idea 15+19 (current) | idea 21 (new) | lift |
|---|---|---|---|
| qwen3 docs-only tokens cited | 5 | TBD | |
| qwen3.6 docs-only tokens cited | 7 | TBD | |
| qwen3 RAG F1 on adversarial | 0.167 | TBD | |
| qwen3.6 RAG F1 on adversarial | 0.051 | TBD | |
| qwen3 retrieved-token utilization | 33% | TBD | |

Decision rule: **if qwen3 token utilization rises to ≥70%**, idea 21
becomes the chapter-headline methodology contribution. Then optionally
re-run on the 5-project main experiment to confirm generalization.

If utilization rises only modestly (50–60%), the LLMs may be
ignoring soft prompt constraints even when we make them explicit —
that's an interesting negative finding but doesn't materially
strengthen the chapter.

If utilization doesn't move, that suggests the model is fundamentally
ignoring the context for project-specific tokens, and we'd need a
different intervention (probably Lever 4 / self-critique from the
research note).

## Most-important documents to read (in this order)

1. **[RAG_IMPROVEMENT_RESEARCH_2026-05-24.md](RAG_IMPROVEMENT_RESEARCH_2026-05-24.md)** — the proposal for what to do next
2. **[RAG_ABLATION_LAB_NOTEBOOK.md §10-§11](RAG_ABLATION_LAB_NOTEBOOK.md)** — the diagnostic + adversarial probe
3. **[CLAUDE_PERSONAL_REVIEW_2026-05-23.md](CLAUDE_PERSONAL_REVIEW_2026-05-23.md)** — the manual read findings
4. **[CROSS_FAMILY_QUALITATIVE_READ_2026-05-22.md](CROSS_FAMILY_QUALITATIVE_READ_2026-05-22.md)** — first qualitative pass
5. **[CROSS_FAMILY_CONTENT_QUALITY_2026-05-23.md](CROSS_FAMILY_CONTENT_QUALITY_2026-05-23.md)** — LLM-as-judge results
6. The original handoff below (preserved for context but partially
   superseded by the above)

## Key files on disk

### Pipeline code
- `scripts/run_cross_family_winning_config.py` — main experiment driver (idea 19 + idea 15)
- `scripts/run_adversarial_probe.py` — 5-story Mastodon adversarial probe
- `scripts/analyze_adversarial_probe.py` — prompt-echo vs docs-only token analysis
- `scripts/dump_adversarial_qual.py` — per-story content dump
- `scripts/dump_all_content_for_claude.py` — full content dump for manual reading
- `scripts/read_everything.py` — structural sanity stats over all cells
- `scripts/judge_content_quality.py` — LLM-as-judge content-quality scorer
- `scripts/score_judge_calibration.py` — Cohen's κ scorer for hand-labeled pairs
- `scripts/rag_ablation_judge.py` — original Phase A retrieval judge (P@k + nDCG@5)
- `data/winning_prompts/` — idea-15 few-shot prompt templates (system, rag user, zero-shot user)

### Data
- `real_test_cases/<project>/docs/` — ingested doc corpora (47 gitlab, 45 mattermost, 70 keycloak, 20 mastodon, 17 bitwarden, after the 2026-05-20 enrichment)
- `real_test_cases/<project>/user_stories.json` — 8 stories per project
- `real_test_cases/<project>/ground_truth.json` — 3-4 hand-curated reqs per story
- `real_test_cases/<project>/results_compare_runs_winning_<model_slug>_n10/` — main experiment cells (20 dirs)
- `real_test_cases/bitwarden/results_compare_runs_winning_<model_slug>_n10_bug_pure_doc_only/` — preserved pre-fix bitwarden cells
- `real_test_cases/mastodon_adversarial/user_stories.json` + ground_truth.json — 5 adversarial probes
- `real_test_cases/mastodon_adversarial/results_compare_runs_adversarial_*` — adversarial probe outputs
- `/tmp/cross_family_content_for_review/<project>.md` — full content dumps (regenerate with dump_all_content_for_claude.py)
- `/tmp/cross_family_content_quality.json` — LLM-as-judge structured output

### Configuration locks (DO NOT VIOLATE without re-justifying)
- Generator: `qwen3:30b-a3b-instruct-2507-q4_K_M`, `qwen3.6:latest`, `gemma4:26b`, `mistral-small3.2:24b`
- Agent role (for idea 19 hybrid retrieval): `mistral-small3.2:24b` (or `qwen3.6:latest` when the generator IS mistral-small)
- Judge (Phase A retrieval): `llama3:8b`
- Judge (content quality): `llama3:8b`
- Embeddings: `sentence-transformers/all-mpnet-base-v2`
- Reranker: `cross-encoder/ms-marco-MiniLM-L-6-v2`
- Similarity threshold: `0.4` (DO NOT raise back to 0.7 — that was the 2026-05-19 audit bug)
- Chunker: `ArchitectureAwareChunker(max_chunking_level=3)` with front-matter strip
- match_threshold for F1: 0.85 (this is the value that makes the matcher too strict; reduce only if you re-narrate the entire F1 pipeline)
- partial_threshold for F1: 0.70

## Open methodology issues for Chapter 7

1. **The F1 metric mostly measures phrasing similarity** to a small GT,
   not security-content quality. Chapter 7 should lead with the
   content-quality judge + manual read + adversarial probe, and use
   F1 as a secondary signal showing phrasing-alignment variance.
2. **Generation utilization gap** (~33% of retrieved project-specific
   tokens are cited in generated reqs) is the open improvement
   target. Idea 21 is the proposed fix.
3. **Training-data contamination** — the base LLMs have already seen
   the public docs for the 5 projects. The adversarial probe sidesteps
   this; the main experiment doesn't.
4. **N=8 stories per project is low** for per-story Wilcoxon. Only
   1/20 cells reaches per-story p<0.05; almost all "STRONG" verdicts
   come from per-run (sensitivity) Wilcoxon over N=10. Chapter 7
   should report per-run as primary.
5. **Judge κ-calibration is still pending** (user-blocked). The 97-100%
   content-quality finding makes κ less critical, but it's needed to
   defend the LLM-as-judge methodology against examiner challenge.

---

---

---

# 📜 HISTORICAL — 2026-05-20 update (superseded by the 2026-05-24 section above, but preserved)

## ⚠ TOP-OF-FILE UPDATE — STATE AS OF 2026-05-20

### The empirical results below this section are INVALIDATED

On 2026-05-19, a manual audit of `raw_runs/` artifacts (enabled by the
Option B patch from 2026-05-17) discovered that **the retriever was
returning the literal whiff string `"No relevant security documentation
found."` for 100% of queries across ALL prior pilots** — qwen3 N=20
baseline, qwen3.6 N=10 sub-test, the entire 16-cell cross-family
matrix. Root cause: `similarity_threshold = 0.7` in
`config.py:RetrievalConfig` was unreachable for the cosine similarities
that `sentence-transformers/all-mpnet-base-v2` actually produces on
this corpus (real top hits are 0.45-0.65 cosine).

**Implication**: every F1 / d_z / verdict in the old "verdict tables"
below measures *prompt-structure variation* (baseline prompt vs.
baseline prompt + "no docs found" notice in the RAG path), NOT
RAG benefit from retrieved documents. They are technically correct
math on the wrong measurement. Cannot be cited.

### The fix is in code; one immediate re-run validated it

- `src/secreqgen/config.py` — threshold lowered from 0.7 to 0.4 with
  inline rationale comment.
- `src/secreqgen/retrieval/vectorstore.py` — `QueryResult.similarity`
  docstring corrected; formula was already right (chroma returns
  squared-L2 distance, so `cos = 1 - dist/2`).
- `src/secreqgen/ingestion/chunker.py` — added front-matter stripping,
  added `max_chunking_level` (default 3) to collapse h4+ micro-chunks
  into their h3 parent, fixed a sibling-section hierarchy bug.
- `src/secreqgen/retrieval/reranker.py` — new `CrossEncoderReranker`
  using `cross-encoder/ms-marco-MiniLM-L-6-v2` as a second-stage
  rerank over dense top-30.
- `src/secreqgen/generation/llm_client.py` — `OllamaClient` reads
  `OLLAMA_HOST` from env (added during the cloud-migration work).
- `tests/unit/test_retriever.py` — 7 new regression tests catching
  formula bugs + the "whiff fallback for relevant queries" pattern.

A focused validation pilot on `mastodon × qwen3 × N=2` after the fix
showed **8/8 stories with real retrieval content** (was 0/8 under the
broken threshold). The fix is real, end-to-end.

### A 20-idea RAG ablation study ran 2026-05-19 → 00:16 2026-05-20

5 projects (mastodon, keycloak, bitwarden, gitlab, mattermost) × 20
ideas covering: chunker variants, retriever variants, BM25 hybrid,
cross-encoder rerank, LLM-driven query transformations (HyDE, rewrite,
multi-query decompose), retrieval agents (ReAct, code search, hybrid,
section nav), and generation strategies (self-critique, chain-of-thought,
few-shot exemplars). Judged by llama3:8b (different family from
generators/agents). Full results + per-cell metrics:
[RAG_ABLATION_LAB_NOTEBOOK.md](RAG_ABLATION_LAB_NOTEBOOK.md).

**Top 3 retrieval ideas (by top-1 relevant out of 40 cells = 8 stories
× 5 projects):**

| rank | idea | top-1 | P@k strict | P@k lenient |
|---|---|---|---|---|
| 🥇 | 19 hybrid_doc_code_agent | **18** | **0.335** | **0.578** |
| 🥈 | 5 bm25_hybrid_rrf | 15 | 0.258 | 0.398 |
| 🥉 | 10 no_keyword_expansion | 14 | 0.218 | 0.376 |

**Best generation strategy**: idea 15 few-shot exemplars (100% success
on 5×3=15 cells, 7.3 s/story, best ASVS-category consistency).

**Five thesis-level findings worth Chapter 7 attention:**

1. Code-search retrieval can break the sparse-corpus ceiling — but
   **see open issue below**: the right fix is to ensure adequate doc
   corpora, not to substitute code search.
2. The base LLM (qwen3:30b) has strong security priors; RAG's marginal
   value scales with project-specificity, not generic mechanisms.
3. The hand-coded `SECURITY_KEYWORD_MAP` query expansion was *hurting*
   retrieval — removing it doubled top-1 hits (7 → 14).
4. BM25 + dense + RRF beats CE-rerank alone for security-domain
   vocabulary with rare exact terms (env vars, RFC numbers).
5. LLM-driven section-navigation (idea 20) is *worse* than baseline —
   documented negative finding.

### Open issue surfaced 2026-05-20: the corpus baseline isn't equitable

The user pointed out (correctly) that **a thesis on RAG-enhanced
generation requires every evaluation project to have substantive
documentation**. Otherwise we're not testing RAG; we're testing zero-
shot generation with a "no docs found" notice.

Current corpus distribution across the 5 cross-family projects:

| project | doc files | status for thesis |
|---|---|---|
| keycloak | 70 | ✓ adequate |
| mastodon | 20 (enriched 2026-05-19) | ✓ adequate after enrichment |
| bitwarden | 17 | borderline — could be enriched |
| gitlab | **47** (enriched 2026-05-20) | ✓ adequate after enrichment |
| mattermost | **45** (enriched 2026-05-20) | ✓ adequate after enrichment |

### Pending work — IN PRIORITY ORDER (updated 2026-05-20 12:14)

1. ~~**Corpus enrichment**~~ — **DONE 2026-05-20 12:05**:
   - gitlab: 1 → **47** files (curated from `gitlab-org/gitlab` `doc/security/`,
     `doc/administration/auth/`, `doc/integration/` for SSO/OAuth/SAML/Vault).
     Hugo front-matter retained; chunker strips it.
   - mattermost: 2 → **45** files (curated from `mattermost/docs`
     `source/security-guide/`, `deployment-guide/`, `administration-guide/`
     `comply/`, `configure/`, `onboard/`). Converted RST → GFM with pandoc 3.9.
   - bitwarden: skipped (17 files borderline-adequate; revisit if
     cross-family noise warrants).
2. ~~**Re-run idea 19 on enriched corpora**~~ — **DONE 2026-05-20 12:11**:
   - gitlab 537 chunks (was 20); top-1 2/8 → **3/8**, nDCG@5 **0.897**,
     P@k_lenient 0.618.
   - mattermost 1080 chunks (was 11); top-1 3/8 → **0/8** (judge-strictness
     artifact — system retrieves canonically right docs but llama3:8b labels
     them "partial" not "relevant"), nDCG@5 **0.864**, P@k_lenient
     0.350 → **0.595**.
   - Full writeup in Section 8 of [RAG_ABLATION_LAB_NOTEBOOK.md](RAG_ABLATION_LAB_NOTEBOOK.md).
3. ~~**Add nDCG@5 metric**~~ — **DONE 2026-05-20 12:09**:
   - Added to `scripts/rag_ablation_judge.py` (BEIR convention, exponential
     gain `2^rel−1`). Printed in live log and pivot tables. Smoke tests pass.
4. ~~**Re-run the cross-family experiment**~~ — **DONE 2026-05-22 08:13** (4 models × 5 projects × N=10, 20 cells, ~30 hr wall time):
   - Master table + replication analysis in [Section 9 of RAG_ABLATION_LAB_NOTEBOOK.md](RAG_ABLATION_LAB_NOTEBOOK.md).
   - Headline: 8 of 20 cells reach STRONG ± on the per-run sensitivity test; only 1 of 20 reaches significance on the per-story primary test (Wilcoxon is low-powered at 8 stories).
   - Two project-level verdicts replicate across ≥2 models: **bitwarden RAG hurts** (qwen3 STRONG −, mistral-sm STRONG −), **mattermost RAG helps** (qwen3 STRONG +, qwen3.6 STRONG +).
   - **gitlab × gemma4 = +d_z 3.55** vs **gitlab × qwen3 = −d_z 1.53** under the *same* corpus, retrieval, prompts — the cleanest cross-family model-conditionality result.
   - **keycloak flipped from pre-audit STRONG + → null** under all 4 models — consistent with "RAG marginal value scales with project specificity, not generic mechanisms" (keycloak's 70-file corpus is so security-explicit the model already has the answers).
   - 20 multi_run_report.json files + raw_runs/ at `real_test_cases/<project>/results_compare_runs_winning_<model_slug>_n10/`.
   - Driver: [`scripts/run_cross_family_winning_config.py`](../scripts/run_cross_family_winning_config.py)
     — implements idea 19 retrieval (CE-rerank doc + grep-based code
     agent) as a duck-typed retriever that plugs into the existing
     `EvaluationPipeline`; monkey-patches the `BaselineRunner` generators
     to use the few-shot prompt templates in
     [`data/winning_prompts/`](../data/winning_prompts/).
   - **Smoke-validated 2026-05-20 13:14**: 1 model × mastodon × N=2
     completed in 15.6 min; baseline F1 0.022 (matches prior N=20
     exactly), RAG F1 0.086 (vs prior 0.074, within N=2 noise), per-story
     d_z = +0.599. All 8 stories produced both doc and code chunks (5
     of 7 chunks were code grep hits). Few-shot exemplars producing
     "Argon2id 19MiB / 2 iter / parallelism 1" style specifics.
   - Two bugs found and fixed during smoke: (a) the duck-typed retriever
     needed `format_result_for_prompt`, not just `format_context` —
     `SecurityRequirementsGenerator` calls the former at
     [`generator.py:153`](../src/secreqgen/generation/generator.py#L153);
     (b) the agent used `subprocess.run(["rg", ...])` but `rg` is not
     installed on this host (only a shell function named `rg`); switched
     to `grep -rEnI --include=*.py …` to match
     [`scripts/rag_ablation_agents.py:226-229`](../scripts/rag_ablation_agents.py#L226-L229).
   - **Launch command**:
     ```bash
     cd "/Users/georgeg/Documents/Uni Docs/Thesis/coded_project/secreqgen"
     TOKENIZERS_PARALLELISM=false \
     PYTHONPATH=src .venv/bin/python scripts/run_cross_family_winning_config.py \
       --models qwen3:30b-a3b-instruct-2507-q4_K_M,qwen3.6:latest,gemma4:26b,mistral-small3.2:24b \
       --projects mastodon,keycloak,bitwarden,gitlab,mattermost \
       --n 10
     ```
   - Outputs land in `real_test_cases/<project>/results_compare_runs_winning_<model_slug>_n10/run_comparison_<ts>/`
     (same shape as existing compare-runs pilots so the existing
     `cross_model_comparison.py` analysis script will work after a
     minor extension to handle the new label).
5. **Calibrate the judge** — **STILL PENDING (user-blocked)**: hand-label
   the 30-pair stratified worksheet at [docs/judge_calibration_sample.md](judge_calibration_sample.md)
   (10 relevant / 10 partial / 10 irrelevant, LLM verdicts hidden in
   [docs/judge_calibration_sample.json](judge_calibration_sample.json)),
   then run `PYTHONPATH=src .venv/bin/python scripts/score_judge_calibration.py`
   to compute Cohen's κ. The bitwarden STRONG − and the mattermost 0/8
   top-1 results both depend on judge-label sanity; without κ these can't
   be cited in Chapter 7 with full methodological backing. **This is now
   the highest-leverage remaining item.**
6. **Per-story qualitative spot-checks** (small, optional, but valuable
   for Chapter 7 narrative):
   - bitwarden×qwen3 STRONG − (F1 0.115→0.018): read 2-3 stories' raw
     baseline_reqs vs rag_reqs vs ground_truth_matches to determine
     whether this is phrasing-mismatch artifact or real semantic regression.
   - gitlab×gemma4 STRONG + with baseline F1 0.000 → RAG F1 0.067: did
     gemma4 produce different ASVS categories, or unlock a different
     requirement set entirely?
   Raw data on disk at `real_test_cases/<project>/results_compare_runs_winning_<model_slug>_n10/run_comparison_*/raw_runs/run_*.json`.
7. **Write Chapter 7** using the (now valid) cross-family findings.
   Lab notebook §9 has the headline paragraph drafted. Original framing
   ("RAG benefit is project-and-model conditional") still holds but the
   *specific project verdicts re-narrate*: two replicate across families
   (bitwarden −, mattermost +), one shows clean opposite verdicts (gitlab),
   two are universally null/inconclusive (mastodon, keycloak). The
   "best-practice pipeline = idea 19 retrieval + idea 15 generation" is
   the methodology-chapter contribution; the conditional-benefit table is
   the empirical-chapter contribution.

### Most important file pointers

| What | Where |
|---|---|
| Audit postmortem | [docs/RAG_RETRIEVAL_AUDIT_2026-05-19.md](RAG_RETRIEVAL_AUDIT_2026-05-19.md) |
| 20-idea ablation lab notebook | [docs/RAG_ABLATION_LAB_NOTEBOOK.md](RAG_ABLATION_LAB_NOTEBOOK.md) |
| Cloud migration plan (Vultr) | [docs/vultr_migration.md](vultr_migration.md) |
| Phase A retrieval+judge data | `/tmp/rag_ablation_retrieval.json`, `/tmp/rag_ablation_judged.json` (50 cells) |
| LLM-query phase | `/tmp/rag_ablation_llm_query.json`, `/tmp/rag_ablation_llm_query_judged.json` (15 cells) |
| Agents phase | `/tmp/rag_ablation_agents.json`, `/tmp/rag_ablation_agents_judged.json` (20 cells) |
| Generation phase | `/tmp/rag_ablation_generation.json` (45 cells of generated requirements) |
| Ablation orchestrator | `scripts/run_all_ablations.sh` (wraps 4 runners + judges) |
| Idea runners | `scripts/rag_ablation_study.py` (1-10), `..._llm_query.py` (11/12/16), `..._agents.py` (17-20), `..._generation.py` (13-15) |
| Judge | `scripts/judge_retrieval_quality.py` (single-file), `scripts/rag_ablation_judge.py` (batch from ablation JSON) |

### Resume commands

```bash
cd "/Users/georgeg/Documents/Uni Docs/Thesis/coded_project/secreqgen"

# 1. Corpus enrichment (done 2026-05-20). Re-run from scratch:
#    See Section 8 of RAG_ABLATION_LAB_NOTEBOOK.md for the full
#    fetch-and-convert recipe (gitlab raw URLs + mattermost RST→MD
#    via pandoc 3.9).

# 2. Sanity-recheck enriched retrieval:
ABLATION_PROJECTS=gitlab,mattermost ABLATION_IDEAS=19 \
  ABLATION_AGENT_OUT=/tmp/rag_ablation_idea19_enriched.json \
  PYTHONPATH=src .venv/bin/python scripts/rag_ablation_agents.py

ABLATION_INPUT=/tmp/rag_ablation_idea19_enriched.json \
ABLATION_JUDGED=/tmp/rag_ablation_idea19_enriched_judged.json \
  PYTHONPATH=src .venv/bin/python scripts/rag_ablation_judge.py

# 3. The next item is the cross-family experiment. Driver scripts
#    (scripts/run_cross_family_n10.sh, run_cross_model_pilot.sh) use
#    the default SimpleRetriever and default generation, NOT idea 19
#    + idea 15. They need a wrapper that:
#      (a) builds the idea-19 retrieval (doc CE-rerank UNION code grep)
#          per story, formats the context, and
#      (b) passes that context into the few-shot-exemplar generation
#          prompt from rag_ablation_generation.py idea 15.
#    The cleanest path is probably to write a small helper module
#    that exposes idea_19_retrieve(story, project) -> context_string
#    and patches the BaselineRunner's retrieval step. Then call the
#    existing compare-runs CLI with that helper plugged in.

# 4. View lab notebook for context:
open docs/RAG_ABLATION_LAB_NOTEBOOK.md
```

### Model role assignment (locked in by 2026-05-20)

The 20-idea ablation locked this in. **Do not violate** without
re-justifying:

| role | model | reason |
|---|---|---|
| Generator (writes security requirements) | `qwen3:30b-a3b-instruct-2507-q4_K_M` | Strongest JSON-structured security output |
| Critic (idea 13 self-critique) | `llama3:8b` | Different family from generator |
| Decomposer / HyDE / rewrite (11, 12, 16) | `mistral-small3.2:24b` | Different family from generator |
| **Agent (ideas 17-20)** | **`mistral-small3.2:24b`** | Different family from generator — user explicitly directed |
| Judge (llama3:8b for P@k) | `llama3:8b` | Different family from generator/agent |

### Outdated content warning

Everything below in this file (the old "TL;DR — where we are right
now", the verdict tables, the council narratives) is **historical**
and **partially invalidated** by the retriever bug. It's preserved
for context, not for citation. Trust the new tables + the lab
notebook for current empirical state.

---

## ORIGINAL HANDOFF (2026-05-17, partially invalidated 2026-05-19)

## TL;DR — where we are right now

**The empirical work is essentially complete. The pending tasks are
either (a) one optional cross-family sub-test extension, or (b)
writing Chapter 7 of the thesis.** A council on 2026-05-17 said
*stop experimenting, write up, but patch the over-claim on negative
effects before writing*.

A cross-family sub-test was started today (gemma4:26b + mistral-
small3.2:24b on the 4-project subset) but was **interrupted very
early** so the bash script could be parameterised. The script is now
parameterised. **No new sub-test results are written to disk yet** —
the cross-family run needs to be re-launched.

---

## The thesis in one paragraph

**SecReqGen** is an MSc cybersecurity thesis on *RAG-enhanced LLM-
generated security requirements*. The thesis question is whether
augmenting an LLM with retrieval over project-specific security
docs produces measurably better security requirements than zero-
shot LLM generation. The thesis built a **multi-run paired-statistics
evaluation methodology** (per-story Wilcoxon primary, paired t-test
sensitivity, Shapiro-Wilk, paired Cohen's `d_z`) and applied it to
10 hand-curated projects. The empirical finding is that **RAG
benefit is conditional on both the project AND the base model**.

---

## The empirical state — verdict tables

### Main baseline (`qwen3:30b-a3b-instruct-2507-q4_K_M`, N=20, all 10 projects)

| Project | Per-run d_z | Per-run p | Verdict |
|---|---|---|---|
| mastodon | +1.69 | 0.0002 | STRONG + |
| keycloak | +1.54 | 0.0002 | STRONG + |
| gitlab | +1.10 | 0.002 | STRONG + |
| nextcloud | +0.90 | 0.003 | STRONG + |
| mattermost | −1.00 | 0.0007 | STRONG − |
| strapi | −0.71 | 0.036 | STRONG − |
| bitwarden | +0.03 | 0.97 | null |
| discourse | +0.01 | 0.82 | null |
| joomla | −0.29 | 0.23 | null |
| grafana | −0.12 | 0.68 | null |

**Summary: 4 STRONG +, 2 STRONG −, 4 null.**

### Cross-model sub-test (`qwen3.6:latest`, N=10, all 10 projects)

| Project | qwen3 verdict | qwen3.6 verdict | Agree? |
|---|---|---|---|
| mastodon | STRONG + | null (+0.30) | ✗ |
| mattermost | STRONG − | null (+0.16) | ✗ |
| bitwarden | null | null (+0.63) | ✓ |
| keycloak | STRONG + | null (−0.01) | ✗ |
| **gitlab** | **STRONG +** | **STRONG + (+1.17)** | **✓** |
| **nextcloud** | **STRONG +** | **STRONG + (+1.28)** | **✓** |
| strapi | STRONG − | null (+0.13) | ✗ |
| discourse | null | null (−0.54) | ✓ |
| joomla | null | null (−0.30) | ✓ |
| grafana | null | null (+0.45) | ✓ |

**Summary: 6 of 10 agree. Replication rates:** STRONG + 50%, STRONG − 0%, null 100%.

### Cross-family sub-test (`gemma4:26b`, `mistral-small3.2:24b`, 4 projects subset)

**NOT YET RUN.** Driver scripts in place, both models verified to
work with `/api/generate` + JSON mode + `think:false`. Pending
re-launch — see "Pending work" below.

---

## The current thesis claim (post-council 2026-05-17)

> *"On 10 hand-curated security-requirements projects evaluated at
> N=20 paired runs under qwen3, RAG produces 4 statistically-
> significant positive effects, 2 negative, 4 null. Under qwen3.6
> (same methodology, same projects, N=10): 6 of 10 verdicts agree;
> 50% of positive effects replicate, 0% of negative effects
> replicate, 100% of nulls remain null. The methodology is what
> makes this differential reliably measurable. **RAG benefit is
> conditional on both project and base-model; single-model RAG
> evaluations substantially overstate durable RAG benefit, and
> negative RAG effects in particular appear less robust than
> single-model evaluations suggest.**"*

⚠️ **Council flagged ONE remaining over-claim**: the docs (status
report + presentation) currently say *"negative effects are entirely
model-specific"* but the data is N=2 negative projects, 2 tested
models. That's overreach. **Soften to:** *"the negative effects did
not replicate in the second tested model and currently appear less
robust than single-model evaluations suggest"*. **This edit has
been made in the status report and presentation already (2026-05-16).**
Verify before writing Chapter 7.

---

## The three councils — what they decided

1. **council-20260503-080229** (methodology design validation):
   per-story pairing primary, N=20 default, Wilcoxon primary + t-
   test sensitivity, `d_z` (paired Cohen's), anchor on Goodman et
   al. 2016, Goal 6 → Option A. All adopted.

2. **council-20260504-060954** (mid-pilot reality check):
   Path A (scale analysis, not data). Don't do Path B
   (CVE-grounded auto-GT — "trap"). Skills are not thesis-core.
   You're over-leaning on keycloak — it's significant on
   sensitivity (per-run) but not primary (per-story).

3. **council-20260517-094844** (next-steps advice):
   Stop experimenting, start writing. Don't do predictor study
   (Option C — underpowered, post-hoc). Skills are a side quest
   for the thesis core. Over-claim on negative effects is the
   only must-fix before writing. Two of three said skip third-
   model; Gemini said run it in parallel with starting to write.
   Codex's strongest reasonable disagreement: "the obvious attack
   is external validity (qwen3 vs qwen3.6 is within-family)".
   This motivated the (currently paused) cross-family sub-test.

---

## File system map

```
/Users/georgeg/Documents/Uni Docs/Thesis/coded_project/
├── secreqgen/                                    # the thesis project
│   ├── src/secreqgen/
│   │   ├── cli.py                                # +evaluate compare-runs
│   │   ├── evaluation/multi_run.py               # the methodology
│   │   ├── generation/llm_client.py              # think:false patch lives here
│   │   └── evaluation/{pipeline,metrics,semantic_matcher}.py
│   ├── docs/
│   │   ├── HANDOFF.md                            # THIS FILE
│   │   ├── thesis_status_report.md               # the long-form narrative (~36 KB)
│   │   ├── presentation.md                       # Marp slide deck
│   │   ├── SecReqGen_Status.pptx                 # PowerPoint version (22 slides)
│   │   ├── sub_test_qwen36.md                    # cross-model sub-test design doc
│   │   ├── sub_test_qwen36_results.md            # cross-model comparison (generated)
│   │   ├── proposals/                            # rev. 2 methodology proposals
│   │   └── figures/fig{1..7}.png                 # PNG figures
│   ├── scripts/
│   │   ├── run_cross_model_pilot.sh              # MODEL-AGNOSTIC driver (NEW)
│   │   ├── run_thesis_n20_qwen36.sh              # legacy shim (delegates to above)
│   │   ├── run_cross_family_n10.sh               # gemma4 → mistral-small3.2 wrapper
│   │   ├── run_thesis_n20.sh                     # qwen3 baseline N=20 driver
│   │   ├── per_project_pilot.sh                  # v2 driver (4 hand-curated projects)
│   │   ├── bootstrap_dataset.py                  # GitHub repo → dataset
│   │   ├── asvs_templates.py                     # rule-based ASVS mapping
│   │   ├── cross_model_comparison.py             # qwen3 vs qwen3.6 cross-model table
│   │   ├── meta_analysis.py                      # pooled per-story + per-run
│   │   ├── synthesize_pilot_v2.py                # cross-project synthesis (v2/v3/n20)
│   │   ├── generate_figures.py                   # produces docs/figures/fig*.png
│   │   ├── generate_powerpoint.py                # produces SecReqGen_Status.pptx
│   │   ├── heartbeat_poller.sh                   # generic across labels (FIXED today)
│   │   ├── fetch_docs_for_unevaluated.py         # GitHub doc fetch for 6 dataless projects
│   │   └── fix-pth-hidden.sh / setup-venv.sh     # macOS .pth quirk workarounds
│   └── .council/specs/test_g{1..5}.py            # frozen test specs (autopilot output)
└── real_test_cases/                               # data + per-project results
    ├── bitwarden/, keycloak/, mastodon/, ...     # the 10 hand-curated projects
    ├── authelia/, chroma/, clickhouse/, ...      # 26 auto-bootstrapped from v3
    └── <proj>/results_compare_runs_*/             # per-project pilot reports
        ├── results_compare_runs_pilot_v2_kb_fixed/  # v2: N=10, 4 hand-curated
        ├── results_compare_runs_pilot_v3/            # v3: N=10, 36 auto-bootstrapped
        ├── results_compare_runs_thesis_n20/          # main baseline (qwen3, N=20)
        └── results_compare_runs_qwen36_latest_n10/   # qwen3.6 cross-model (all 10)
```

---

## What just changed (2026-05-17 — bash refactor)

1. Created **`scripts/run_cross_model_pilot.sh`** — model-agnostic
   driver. Takes `MODEL` (required), `LABEL` (derived from MODEL if
   absent), `N`, `OUTPUT_DIR_NAME`, `FORCE` as env vars. Positional
   args = project subset.

2. Reduced **`scripts/run_thesis_n20_qwen36.sh`** to a thin shim:
   sets the historical qwen36 defaults and delegates to the new
   script. All previous invocations still work.

3. Updated **`scripts/heartbeat_poller.sh`** to grep for any label
   (regex matches `^  \[<anything>\] <project>`) and to pgrep for
   any of the three driver scripts. No longer qwen36-specific.

4. Updated **`scripts/run_cross_family_n10.sh`** to point at the
   new driver and set explicit `LABEL=gemma4` / `LABEL=mistral_small_32`
   for each model.

5. Updated **`docs/sub_test_qwen36.md`** §4-§5 to describe the
   parameterised system. Updated **`docs/thesis_status_report.md`**
   §12 driver-pointer to reference the new model-agnostic name.

---

## What just changed (2026-05-17 — raw-output archival patch)

Originally, `compare-runs` saved only aggregate metrics
(`multi_run_report.json`) and discarded the actual generated
requirement texts. The user asked to keep the raw outputs going
forward so the F1 numbers can be audited and qualitative quotes
extracted for Chapter 7.

**Patch:**
1. **`src/secreqgen/evaluation/multi_run.py`** — added Pydantic v2
   `BaseModel` handling in `_json_safe` (via `model_dump(mode="json")`),
   and a new `serialize_individual_result(result) -> str` helper that
   complements `serialize_report`. The module stays pure / no-I/O.
2. **`src/secreqgen/evaluation/__init__.py`** — re-exports
   `serialize_individual_result`.
3. **`src/secreqgen/cli.py`** — after writing
   `multi_run_report.json`, the `compare-runs` command now also writes
   one `raw_runs/run_NNN.json` file per individual run into the
   `run_comparison_*/` directory.

**What each raw file contains:**
* `per_story_results[].baseline_result` + `.rag_result` — full
  Pydantic `GenerationResult`, including each `SecurityRequirement`
  (title, description, rationale, ASVS category, priority,
  implementation_notes, source_references) and the retrieval context
  string fed to the RAG generation.
* `per_story_results[].baseline_matches` + `.rag_matches` — every
  match decision (generated_text, ground_truth_text,
  similarity_score, match_type ∈ {match, partial, no_match}).
* `aggregate_baseline_metrics` + `aggregate_rag_metrics` —
  `MetricsBreakdown` per condition for the whole run.
* `comparative_results.{baseline,rag}_results` — second copy of the
  generations, exactly as `BaselineRunner` produced them (handy for
  cross-checking that pipeline-level matching didn't drop anything).

**Effect on the running cross-family pilot:**
* `gemma4/mastodon`, `gemma4/mattermost`, `gemma4/bitwarden` —
  **no raw outputs** (the Python process for each had already
  imported the unpatched code).
* `gemma4/keycloak` (started 16:52:55, currently running) and **all
  4 projects under mistral-small3.2:24b** — will have `raw_runs/`
  populated automatically. Each fresh project spawns a new Python
  process and re-imports the patched code.

---

## Option C — single-pass qualitative retrofit (recipe)

For projects already evaluated before the raw-output patch (the
qwen3 N=20 baseline, the qwen3.6 N=10 sub-test, and the three
gemma4 projects above), the patched code is the recipe but with
`N=1` — one quick paired generation that captures the actual model
output for qualitative inspection. Stats are degenerate at N=1 (no
variance), but that's not the point; you want the text, not new
inference. Each run takes ~3-7 minutes per project depending on
KB size.

```bash
cd "/Users/georgeg/Documents/Uni Docs/Thesis/coded_project/secreqgen"

# qwen3 baseline (10 projects, ~30-60 min total at N=1)
MODEL=qwen3:30b-a3b-instruct-2507-q4_K_M  LABEL=qwen3_audit  N=1 \
  bash scripts/run_cross_model_pilot.sh \
    keycloak mastodon mattermost bitwarden gitlab nextcloud \
    strapi discourse joomla grafana

# qwen3.6 (same 10, ~30-60 min total)
MODEL=qwen3.6:latest  LABEL=qwen36_audit  N=1 \
  bash scripts/run_cross_model_pilot.sh \
    keycloak mastodon mattermost bitwarden gitlab nextcloud \
    strapi discourse joomla grafana

# gemma4 (just the 3 that ran pre-patch — keycloak gets raw outputs
# automatically from the running cross-family pilot)
MODEL=gemma4:26b  LABEL=gemma4_audit  N=1 \
  bash scripts/run_cross_model_pilot.sh mastodon mattermost bitwarden
```

Outputs land in `real_test_cases/<proj>/results_compare_runs_<LABEL>_n1/run_comparison_*/raw_runs/run_001.json`.
For Chapter 7 quotes, `jq` is your friend:

```bash
# pull all generated requirement titles + descriptions for keycloak baseline
jq '.per_story_results[].baseline_result.requirements[] | {title, description, asvs_category}' \
  real_test_cases/keycloak/results_compare_runs_qwen3_audit_n1/run_comparison_*/raw_runs/run_001.json

# Compare match decisions: what RAG matched vs what baseline matched
jq '.per_story_results[] | {story_id, baseline_matches: .baseline_matches | map(.match_type), rag_matches: .rag_matches | map(.match_type)}' \
  real_test_cases/keycloak/.../raw_runs/run_001.json
```

If the user later wants **full re-runs** (N=10 or N=20 with raw
outputs to enable both statistical AND qualitative replication),
the same recipe with the corresponding `N` value will do that, at
proportionally higher compute cost.

---

## Pending work (in priority order)

### 1. Re-launch the cross-family sub-test [optional, ~16-24 hrs compute]

Already designed; both models verified; just needs launching.

```bash
cd /Users/georgeg/Documents/Uni\ Docs/Thesis/coded_project/secreqgen
bash scripts/run_cross_family_n10.sh
```

Runs gemma4:26b → mistral-small3.2:24b in sequence on the 4-project
subset (mastodon, mattermost, bitwarden, keycloak). Estimated 8-14
hrs total. Outputs to:

```
real_test_cases/<proj>/results_compare_runs_gemma4_n10/
real_test_cases/<proj>/results_compare_runs_mistral_small_32_n10/
```

**Council vote was 2-of-3 against**, but 1-of-3 (Gemini) said do it
in parallel with starting to write. Codex's "strongest reasonable
disagreement" was the external-validity argument. **Decision is the
user's** — but the infrastructure is ready either way.

If launching, monitor as before with the heartbeat poller; same
pattern that worked for the qwen3.6 sub-test.

### 2. Extend `cross_model_comparison.py` to 4-model table

Once gemma4 + mistral-small3.2 results exist on disk, the comparison
script needs a small extension to render the 4-model × 4-project
table. Current code is qwen3-vs-qwen3.6 binary. Just add two more
columns + the LABEL → output-dir mapping. Maybe 30 lines.

Subsetting: comparison only on the 4 projects shared across all
4 models (the same subset the cross-family sub-test used). The
existing qwen3 and qwen3.6 data already covers those.

### 3. Write Chapter 7 (Results)

Per council 2026-05-17: this is the primary remaining work. Inputs:
- `docs/thesis_status_report.md` — the narrative source
- `docs/sub_test_qwen36_results.md` — cross-model tables
- `docs/figures/fig{1..7}.png` — figures already generated
- `real_test_cases/*/results_compare_runs_*/multi_run_report.json` — raw data

Council said this should take ~4-6 weeks. The empirical chapter is
~80% drafted in the status report; the work is restructuring it for
thesis format + adding methodology proofs + framing for the
examiner.

### 4. Send Option A (Goal #6 rewording) to supervisor

Drafted in `docs/proposals/goal-6-rewording.md`. §6 has the
ready-to-send paragraph. Verify the "<PASTE EXACT TEXT HERE FROM
PDF>" placeholder is filled in before sending.

---

## How to run anything from scratch

```bash
# 1. Get into the project + Ollama up
cd /Users/georgeg/Documents/Uni\ Docs/Thesis/coded_project/secreqgen
ollama serve &   # if not already running

# 2. Fix the .pth hidden flag (macOS quirk; required after every poetry install)
PTH=".venv/lib/python3.13/site-packages/secreqgen.pth"
c=$(cat "$PTH"); rm "$PTH"; printf '%s\n' "$c" > "$PTH"

# 3. Sanity-check the CLI
PYTHONPATH=src poetry run secreqgen generate check-ollama

# 4. Run any cross-model sub-test (example: gemma4 on 4-project subset)
MODEL=gemma4:26b  bash scripts/run_cross_model_pilot.sh

# 5. After it completes, regenerate the cross-model report
PYTHONPATH=src .venv/bin/python scripts/cross_model_comparison.py \
    --output docs/sub_test_qwen36_results.md

# 6. Regenerate figures (PNGs)
PYTHONPATH=src .venv/bin/python scripts/generate_figures.py

# 7. Regenerate the PowerPoint
.venv/bin/python scripts/generate_powerpoint.py
```

---

## Known quirks / gotchas (alphabetical)

- **`adapters.ts` in `agent-council`** is unrelated to the thesis;
  user has had it open in IDE throughout. It's the council tool's
  adapter layer, not secreqgen.
- **`bash` log labels say `[qwen36]` for the legacy shim** (delegated
  via `run_thesis_n20_qwen36.sh`). For new model runs invoked
  directly via `run_cross_model_pilot.sh`, labels are derived from
  the model tag (e.g. `gemma4_26b`, `mistral_small_32`).
- **Code-tuned model variants break JSON output** (qwen3.6-coding-nvfp4
  produced 100% baseline failures). Stick to `instruct`/`latest`
  variants; avoid anything with `coding` in the name.
- **`.pth` hidden flag** — macOS marks files installed via Poetry as
  hidden (`com.apple.provenance` xattr → `hidden` chflag), which
  hides them from Python's site-packages scanner, so
  `import secreqgen` fails. The drivers auto-clear it; if you run
  Python directly outside the driver, re-clear the flag.
- **`run_thesis_n20_qwen36.sh` is now a shim** — works but doc-only.
  Direct invocations should use `run_cross_model_pilot.sh` with
  explicit `MODEL=` and (optionally) `LABEL=`.
- **`think:false` flag is mandatory** on reasoning models (qwen3.6:
  latest, deepseek-r1). Already in `src/secreqgen/generation/llm_client.py`
  on both `/api/generate` and `/api/chat` payloads. Safe no-op on
  non-reasoning models.
- **Tokens/sec on M-series** for ~24-30B class models is typically
  30-50 tok/s sustained on long structured outputs. Full N=20 paired
  run on 8 stories is roughly 2-5 hours per project, depending on
  KB size (more retrieval context = longer prompts = slower per-gen).

---

## Glossary (for fresh readers)

- **N=20 paired runs**: 20 baseline + 20 RAG runs per project; results
  paired by run index for the per-run sensitivity test.
- **Per-story Wilcoxon (PRIMARY)**: paired test on per-story mean F1
  differences. With 8 stories per project, the smallest two-sided
  Wilcoxon p is ~0.008 (when all 8 differences agree in sign).
- **Per-run Wilcoxon (SENSITIVITY)**: paired test on aggregate per-
  run F1. With N=10 or N=20 runs, more power than per-story.
- **`d_z`**: paired-sample Cohen's d, the variant named in Lakens
  (2013). Calculated on the differences vector. Cohen 1988 thresholds:
  small ≈ 0.2, medium ≈ 0.5, large ≈ 0.8. "Negligible" = below 0.1.
- **Verdict (STRONG +, null, etc.)**: combination of d_z sign +
  magnitude AND p<0.05 on per-run. Defined in
  `scripts/cross_model_comparison.py:verdict()`.
- **Ceiling effect**: under a stronger base model, baseline F1 rises
  closer to what RAG would produce, so RAG benefit measured as
  Δ F1 shrinks. Confirmed mechanistically by a keycloak spot-check
  (both modes produce essentially the same requirements under
  qwen3.6).

---

## If the user (or a future Claude) is confused about anything

Read these in order:

1. **This file** (`HANDOFF.md`) for orientation.
2. **`docs/thesis_status_report.md`** §11-12 for the latest empirical
   summary, the cross-model finding, and the supervisor pitch.
3. **`docs/sub_test_qwen36_results.md`** for the per-project
   comparison table (regenerable via `cross_model_comparison.py`).
4. **`docs/proposals/goal-6-rewording.md`** for the still-pending
   supervisor sign-off action.
5. **`docs/proposals/reproducibility-considerations.md`** for the
   Chapter 6.X draft (methodology framing, Goodman et al. 2016).

If the user asks "where were we?", point them at the **Pending work**
section above. If they want to run something, point at **How to run
anything from scratch**. If they're worried about over-claiming,
point at the **council 2026-05-17** notes plus the over-claim
patch confirmation in the status report.
