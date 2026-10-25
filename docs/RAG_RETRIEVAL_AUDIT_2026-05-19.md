# RAG Retrieval Audit — 2026-05-19 (Postmortem & Repair Plan)

*Manual audit conducted 2026-05-19 after the 16-cell cross-family matrix
(`qwen3 + qwen3.6 + gemma4 + mistral-small3.2` × `mastodon + mattermost +
bitwarden + keycloak`, all at N=10 paired runs) completed and the
empirical-thesis story crystallised. Triggered by the user's instinct
("I don't necessarily trust the stats") and made possible by the
Option B raw-outputs patch that landed 2026-05-17.*

---

## TL;DR — what the audit found

1. **Retrieval is 100% broken.** All 1,278 RAG queries across all 16
   cells returned the literal string `"No relevant security
   documentation found."` Not a single chunk was ever returned from the
   vectordb, despite ingestion having stored chunks correctly (10 to 548
   chunks per project, all confirmed by ingest logs).
2. **The "RAG" condition was effectively zero-shot with a different
   prompt.** The whiff string was passed into the prompt template, so
   the LLM saw a slightly different prompt under "RAG" than under
   baseline. That prompt difference is what produced the measured F1
   deltas — *not* retrieved document content.
3. **The 0.85 semantic similarity threshold hides correct answers.**
   Clearly-equivalent generations (e.g., "Enforce PKCE for Public
   Clients" vs GT "Authorization Code PKCE Enforcement") scored
   similarity ≈ 0.806 and were classified as "partial" (between 0.70 and
   0.85), counting as zero true positives in F1.
4. **All 16 cell verdicts are real statistical signals but measure the
   wrong thing.** They measure whether the "no docs found" notice in
   the RAG prompt nudges the LLM toward generations that randomly cross
   the 0.85 threshold more often than baseline. They do *not* measure
   retrieval-augmented generation benefit.

---

## Timeline

| Date | Event |
|---|---|
| Pre-2026-05-17 | All baseline empirical work (qwen3 N=20 baseline on 10 projects, qwen3.6 N=10 sub-test on 10 projects) was conducted on this same pipeline, presumably with the same retrieval bug. |
| 2026-05-17 | Option B patch landed: `serialize_individual_result()` writes per-run JSON with full LLM outputs + retrieval context. This is what made today's audit possible. |
| 2026-05-17 → 2026-05-19 | Cross-family extension ran: gemma4 + mistral-small3.2 on 4-project subset, then qwen3 + qwen3.6 N=10 matched on the same 4 projects. 16 cells, all with `raw_runs/`. |
| 2026-05-19 morning | Cross-family matrix complete. Verdict claims drafted (e.g., "keycloak anchor evaporates outside qwen3"). |
| 2026-05-19 afternoon | User asks for manual qualitative audit of raw outputs. Aggregation discovers `avg_chars = 41` for every `retrieval_context` across all 16 cells — the exact length of `"No relevant security documentation found."` |
| 2026-05-19 afternoon | Individual-file inspection on 5 raw_runs from 3 projects × 3 models confirms: every single retrieval returns the whiff string verbatim. No actual retrieval has ever occurred in any pilot. |
| 2026-05-19 evening | This document started; retriever code investigation begins. |

---

## Evidence

### 1. Retrieval whiff (verbatim from individual raw_runs)

Read directly from individual files (not aggregated):

```
keycloak / qwen3_n10 / run_001
  story 1 [KC-US-001] 'OIDC Authorization Code Flow'
    retrieval_context = 'No relevant security documentation found.'
  story 2 [KC-US-002] 'Redirect URI Configuration'
    retrieval_context = 'No relevant security documentation found.'
  story 3-8 (same)

keycloak / qwen3_n10 / run_005             — same string, all 8 stories
keycloak / mistral_small_32_n10 / run_001  — same string, all 8 stories
bitwarden / gemma4_n10 / run_001           — same string, all 8 stories
mastodon  / mistral_small_32_n10 / run_001 — same string, all 8 stories
```

Aggregate: **1,278 of 1,278 RAG retrievals across the entire matrix
returned the whiff string.** Average `retrieval_context` length = 41
characters, which is exactly the length of `"No relevant security
documentation found."` (40 chars + period).

### 2. Threshold-hiding evidence (single representative story)

`keycloak / qwen3_n10 / run_001 / story 1` (OIDC Authorization Code
Flow):

- Ground truth: `"Authorization Code PKCE Enforcement"` (KC-GT-001)
- Baseline generated: `"Enforce PKCE for Public Clients"`
- Cosine similarity: **0.806**
- Match threshold: 0.85
- Classified as: **partial** → counts as 0 true positives in F1
- Both baseline and RAG got F1 = 0.000 on this story across every run

Same pattern repeats throughout: clearly correct PKCE / CSRF / token
binding generations sit at sim 0.70-0.84 and are silently dropped from
the F1 calculation.

### 3. Threshold-crossing-luck evidence (single story across 10 runs)

`keycloak / qwen3_n10 / story 8 (Session Management)`, all 10 paired runs:

| Run | baseline F1 | RAG F1 | baseline match sims (only those that crossed 0.85) | RAG match sims |
|---|---|---|---|---|
| 001 | 0.50 | 0.33 | [0.8704] | [0.8730] |
| 002 | 0.00 | 0.50 | [] | [0.8730, 0.8548] |
| 003 | 0.00 | 0.25 | [] | [0.8730] |
| 004 | 0.00 | 0.33 | [] | [0.8730] |
| 005 | 0.00 | 0.50 | [] | [0.8730, 0.8594] |
| 006 | 0.00 | 0.33 | [] | [0.8730] |
| 007 | 0.00 | 0.33 | [] | [0.8730] |
| 008 | 0.33 | 0.67 | [0.8666] | [0.8730, 0.8563] |
| 009 | 0.00 | 0.50 | [] | [0.8730] |
| 010 | 0.00 | 0.50 | [] | [0.8730, 0.8531] |

**RAG's first-generation crosses 0.85 in 10 of 10 runs** (always at
similarity 0.8730224370956421 — identical to many decimal places,
indicating the LLM produces the exact same text each time).
**Baseline's first generation crosses 0.85 in only 2 of 10 runs.**

This is what drives the "STRONG +" verdict for keycloak/qwen3. It is
not RAG benefit — it is prompt-structure-induced determinism in the
"RAG" path because the LLM gets a slightly different (longer) prompt
that happens to lock it into a high-similarity-to-GT first generation.

### 4. Per-story signal sparsity

For keycloak/qwen3 across 10 paired runs × 8 stories:

- Stories 1, 2, 4, 7: F1 = 0.00 in every single run, both conditions. No signal.
- Story 3: RAG mean F1 = 0.275, baseline = 0.000
- Story 5: baseline mean F1 = 0.271, RAG = 0.000
- Story 6: RAG mean F1 = 0.13, baseline = 0.000
- Story 8: RAG mean F1 = 0.425, baseline = 0.083

**Half the stories produce zero signal in either condition** because no
generation crosses the 0.85 threshold. The aggregate verdict is driven
by 3-4 stories per project where threshold luck differs between
conditions.

---

## Why this wasn't caught earlier

1. **`multi_run_report.json` didn't store `retrieval_context`.** Until
   the Option B patch landed (2026-05-17), only aggregate F1 / Wilcoxon
   / d_z statistics were persisted. Reviewing these gave the impression
   of "RAG benefit ±, conditional on project + model" because the
   numbers were real signals. Without seeing the raw retrieval payload,
   there was no way to tell the retrieval was always returning the
   whiff string.
2. **The whiff string is a graceful fallback, not an error.** The
   retriever returns `"No relevant security documentation found."`
   silently when no chunks pass its internal threshold. No exception
   raised; no warning logged; no metric anomaly. From the pipeline's
   perspective, every query "succeeded".
3. **The verdicts were internally consistent across re-runs** (qwen3
   N=20 baseline and qwen3 N=10 matched gave the same direction), so
   they looked like real findings. They are real signals — just of the
   wrong thing.

The user's "I don't necessarily trust the stats" intuition was correct.
The Option B raw-outputs patch they asked for two days ago is what made
the bug catchable. Without it, this audit would have been impossible.

---

## Impact on thesis

**The headline claim — "RAG benefit is conditional on project and base
model" — cannot be supported by the data on disk.** No RAG retrieval
ever occurred; the F1 differences measure prompt-structure variation
and threshold-crossing luck.

What the data on disk *can* support:

> *"When a 'no documents found' notice is added to the LLM prompt, the
> resulting generations differ from zero-shot generations at the
> per-story F1 level, and the direction/magnitude varies with base
> model and project. This effect is mediated entirely by
> prompt-structure changes, not by retrieved document content, because
> no retrieval occurred in any cell of the experimental matrix."*

That is a real measurement but not the thesis question.

To recover the thesis claim, the retriever must be fixed and the
full cross-family experiment must be re-run.

---

## Diagnosis

Hypothesis 2 was correct: **the similarity threshold was set too high
for the embedding model in use.** Hypothesis 1 (collection mismatch),
3 (embedding mismatch), 4 (eager fallback handler), and 5 (query
construction) were all eliminated empirically.

### What I checked

**Code path**: `SimpleRetriever.retrieve()` (retriever.py:124) embeds
the query, calls `vectorstore.query(top_k=5)`, then filters results
by `result.similarity >= self.similarity_threshold`. If everything is
filtered out, `format_context([])` returns the whiff string
(retriever.py:300-301).

**Default config** (`config.py:81-86`, pre-fix):
- `top_k = 5`
- `similarity_threshold = 0.7`

**Embedding model** (`embedder.py:46`):
- `sentence-transformers/all-mpnet-base-v2`
- 768-dimensional, L2-normalized output vectors

**Chroma collection space** (`vectorstore.py:134`):
- `{"hnsw:space": "l2"}` → returns **squared** L2 distance

**Similarity formula** (`vectorstore.py:48`, pre-fix):
```python
return max(0.0, 1.0 - (self.distance / 2.0))
```

For unit-normalized vectors with squared L2 distance, the math is:
```
||a - b||² = 2(1 - cos(a, b))
∴ cos(a, b) = 1 - ||a - b||² / 2
            = 1 - distance / 2          ← chroma returns squared L2
```

So the formula was **mathematically correct**. The comment in the
code was misleading ("L2 distance ranges from 0 to 2"), but the
formula itself computes the true cosine similarity for normalized
vectors when given chroma's squared-L2 distance.

### Empirical verification

Manual reproducer against the existing persistent vectordb (which
still had keycloak's 548 chunks loaded from the last cross-family
run):

```
Query: 'OIDC Authorization Code Flow with PKCE'
  Top raw retrieval results:
    sim=0.586  "Authorization Code flow ..."   ← highly relevant!
    sim=0.586  "Authorization Code flow ..."   ← duplicate, same content
    sim=0.544  "Implicit grant flow ..."
    sim=0.544  "Implicit grant flow ..."
    sim=0.532  "Client Initiated Backchannel ..."

  With threshold = 0.7:   above_threshold = 0  →  returns whiff string
  With threshold = 0.4:   above_threshold = 5  →  returns the 5 chunks
```

Cross-checked the formula by computing manual cosine on the raw
embeddings extracted from chroma:

```
chroma_distance   reported_sim    manual_cos    L2_dist    L2²/2
   0.828            0.586          0.586         0.910      0.414
```

`reported_sim == manual_cos` to 4 decimal places → formula is correct.

### Root cause

The `all-mpnet-base-v2` embedding model produces cosine similarities
in roughly these bands for this corpus + query shape:

| Content relation to query | Typical cosine |
|---|---|
| Identical paraphrase | 0.85 - 0.95 |
| Strongly related | 0.55 - 0.80 |
| Topically related | 0.30 - 0.55 |
| Unrelated | < 0.20 |

The original threshold (0.7) sits at the upper end of the "strongly
related" band. In practice, for sentence-to-paragraph RAG queries
against ~10-548 chunk corpora, the highest similarities top out
around 0.55-0.60 — never crossing 0.7.

**Result**: every RAG query had `above_threshold = 0`, the retriever
returned `[]`, `format_context([])` returned the whiff string, and
the LLM saw "No relevant security documentation found." in every
"RAG" generation. All 1,278 RAG calls in the matrix. No exception
raised, no warning logged.

### Why the formula was *not* the bug (despite suspicious-looking math)

I initially suspected the linear `1 - distance/2` formula was wrong
and ought to be `1 - distance²/2`. After empirical verification, the
formula is correct *because chroma's `l2` space returns squared L2
distance, not plain L2*. The misleading docstring obscured this. The
docstring is now corrected (see fix below) but the formula is
unchanged.

---

## Repair

### The fix (applied 2026-05-19)

1. **`src/secreqgen/config.py:80-101`** — lowered the default
   `RetrievalConfig.similarity_threshold` from `0.7` to `0.4`,
   with a long inline comment explaining the empirical justification
   and pointing to this audit document. The value is still
   configurable via the `SECREQGEN_RETRIEVAL_SIMILARITY_THRESHOLD`
   environment variable.

2. **`src/secreqgen/retrieval/vectorstore.py:40-60`** — rewrote
   the docstring for `QueryResult.similarity`. The formula is
   unchanged; the new docstring spells out that chroma returns
   squared L2 distance, that `cos = 1 - d/2` is the correct cosine
   recovery for normalized vectors, and lists the expected similarity
   ranges for this embedding model + corpus.

3. **`tests/unit/test_retriever.py`** — added two regression test
   classes:

   - `TestSimilarityFormula` — five parametrized cases pinning the
     formula's outputs for known inputs (identical → 1.0, orthogonal
     → 0.0, the empirical 0.828 → 0.586, etc.). Catches any future
     edit to the formula that breaks the math.

   - `TestRetrievalThresholdRegression.test_relevant_query_does_not_whiff`
     — marked `@pytest.mark.slow` because it loads real
     sentence-transformers. Builds a small in-memory corpus of
     authentication-related chunks, queries "authentication and
     password security", and asserts the result does **not** contain
     the whiff string. This is the test that would have caught the
     2026-05-19 bug end-to-end.

### Empirical validation of the fix

Same set of queries against the same vectordb, threshold 0.4:

| query | before (threshold 0.7) | after (threshold 0.4) |
|---|---|---|
| "OIDC Authorization Code Flow with PKCE" | 0 chunks → whiff | **5 chunks**, top sim 0.586 |
| "Session Management security" | 0 chunks → whiff | **5 chunks**, top sim 0.489 |
| "authentication" | 0 chunks → whiff | **5 chunks**, top sim 0.451 |
| "password hashing PBKDF2 Argon2" | 0 chunks → whiff | still whiff (docs lack those terms — correct behavior) |
| "totally unrelated quantum chromodynamics" | whiff | still whiff (correct filtering) |

The fix recovers retrieval for legitimate queries while still
correctly returning the whiff string for off-topic queries.

### Validation pipeline

#### 1. Mastodon × qwen3 × N=2 (completed 2026-05-19 20:42)

End-to-end pilot, sparse corpus (10 chunks):

| Run | Real retrievals | Whiffs | Note |
|---|---|---|---|
| run_001 | **1/8** (MT-US-007 Federation: 1288 chars context) | 7/8 | other 7 stories have no matching doc content above 0.4 |
| run_002 | **1/8** (same story, same context) | 7/8 | confirms determinism + matches run_001 |

This is what an honest retriever on a sparse corpus looks like —
before the fix it was 0/8 every run on every project. The 7/8 whiffs
are not a retriever bug but a **corpus-inadequacy signal**: Mastodon's
README-derived chunks don't have content about LDAP authentication,
OIDC integration, registration flows, media upload security, etc.
(the topics 7 of 8 stories cover).

This becomes its own Chapter 7 finding: **RAG benefit on a project is
bounded above by the richness of the project's ingested documentation.**
A project whose docs cover only 1 of 8 evaluated security topics
cannot demonstrate RAG benefit on the other 7 — the retriever
correctly returns the whiff string, the LLM falls back on priors, and
baseline and RAG outputs converge.

#### 2. Keycloak × qwen3 × N=2 (completed 2026-05-19 21:07)

Targeted validation against the 548-chunk corpus (the richest in the
dataset).

| Run | Real retrievals | Whiffs |
|---|---|---|
| run_001 | **8/8** | 0/8 |
| run_002 | **8/8** | 0/8 |

Each story matches a topically-relevant doc section. Selected examples:

| Story | Topic | Retrieved doc section | Similarity |
|---|---|---|---|
| KC-US-001 | OIDC Authorization Code Flow | `specifications.md > OAuth` | 0.58 |
| KC-US-002 | Redirect URI Configuration | `oidc-layers.md > Recommendations > Redirect URIs` | 0.59 |
| KC-US-003 | OAuth2 Token Management | `specifications.md > OAuth` | 0.62 |
| KC-US-006 | Client Registration | `client-registration.md > Authentication` | 0.56 |
| KC-US-007 | JWT Token Signing | `jwt-authorization-grant.md > Examples` | 0.62 |
| KC-US-008 | Session Management | `partials-terminology.md > Terminology` | 0.48 |

Each retrieval context is 5,985-10,002 characters of real document
content (chunks + section paths). Retrievals are deterministic across
the two paired runs at temperature=0.1 — same chunks selected each
time, same similarity scores.

Aggregate F1 (N=2, statistically toothless but directionally
informative):

- baseline F1 mean = 0.068
- RAG F1 mean = 0.097
- delta = +0.029

**Fix verdict: working as intended.** Retrieval correctly returns
topically-relevant content for queries it has docs for, and correctly
returns the whiff string when it doesn't (per the mastodon test
above).

---

## Status as of 2026-05-19 21:18

- ✅ Audit complete — root cause identified and documented
- ✅ Fix applied to `config.py` and `vectorstore.py`
- ✅ Regression tests in `tests/unit/test_retriever.py` (7 new tests,
  all passing — 5 parametrized formula checks + 1 slow end-to-end
  test + 1 sanity check on the config default)
- ✅ End-to-end validation on rich corpus (keycloak): 8/8 real
  retrievals per run
- ✅ End-to-end validation on sparse corpus (mastodon): 1/8 real
  retrievals per run, 7 honest whiffs (corpus inadequacy, not bug)

The pipeline is ready for re-running the full empirical work. Open
decision (logged for follow-up): whether to enrich mastodon's corpus
before re-running or to accept the asymmetry as a methodology finding.

---

## Final note for the methodology chapter

This audit is a teachable moment about reproducibility ≠ validity. The
broken pipeline was perfectly reproducible — every run for two weeks
produced consistent statistical signals across qwen3 N=20, qwen3.6
N=10, and the 16-cell cross-family matrix. Multiple "STRONG +" and
"STRONG −" verdicts emerged with sub-0.01 p-values. The math was
correct; the experiment was deterministic; the engineering held up.

What the math could not detect was that the experiment was measuring
prompt-structure variation instead of retrieval-augmented generation.
Only by archiving the **raw retrieval payload** and **inspecting it by
hand** did the bug surface.

For the methods chapter, this becomes:

> *"The methodology contribution of this thesis is not merely the
> per-story Wilcoxon framework, but the pairing of that framework with
> raw-output archival as an auditability layer. The two together
> caught a pipeline bug (default similarity threshold set too high for
> the embedding model in use) that aggregated F1 statistics could not
> reveal — a class of failure mode that, by definition, looks
> statistically significant and reproducible while being substantively
> invalid. Goodman, Fanelli, and Ioannidis (2016) distinguish
> reproducibility (an experiment re-runs to the same result) from
> validity (the result measures what it claims to). This work
> demonstrates a concrete instance where the former held but the
> latter did not, and it shows that surfacing the distinction requires
> infrastructure for inspecting raw artifacts beyond what aggregate
> reports provide."*

### Implications: not all four cross-family projects are equal

The 2026-05-19 audit + threshold fix reveals an asymmetry the
aggregated stats never surfaced:

| Project | Chunks ingested | Doc source | Expected RAG hit-rate per story |
|---|---|---|---|
| keycloak | 548 | full security docs | high (most stories covered) |
| bitwarden | 133 | test-data security docs | medium-high |
| mattermost | ~? | (varies) | medium-low |
| mastodon | **10** | README only | **low** (1 of 8 confirmed) |

Even with the retriever fixed, mastodon's corpus is too sparse to
test RAG meaningfully on most stories. Either:

1. **Accept the asymmetry** and frame the cross-family analysis as
   "RAG benefit when retrievable docs exist" with mastodon as a
   negative-control baseline showing what happens when they don't.
2. **Re-ingest mastodon** with richer security docs (Mastodon's
   developer wiki, security disclosures, OWASP authentication
   guidance referenced by its README) so the corpus actually covers
   the user-story topics. Adds ~1 day of dataset curation.

Either path is defensible. Option 1 turns the limitation into a
methodology contribution; option 2 strengthens the empirical claim.

---

## What this means for the prior empirical results

All of the following experiments ran under the 0.7 threshold and
therefore had **no real retrieval**:

| Result set | Files |
|---|---|
| qwen3 N=20 baseline (10 projects) | `real_test_cases/*/results_compare_runs_thesis_n20/` |
| qwen3.6 N=10 sub-test (10 projects) | `real_test_cases/*/results_compare_runs_qwen36_latest_n10/` |
| qwen3 N=10 cross-family matched (4 projects) | `real_test_cases/*/results_compare_runs_qwen3_n10/` |
| qwen3.6 N=10 cross-family matched (4 projects) | `real_test_cases/*/results_compare_runs_qwen36_n10/` |
| gemma4 N=10 cross-family (4 projects) | `real_test_cases/*/results_compare_runs_gemma4_n10/` |
| mistral_small3.2 N=10 cross-family (4 projects) | `real_test_cases/*/results_compare_runs_mistral_small_32_n10/` |

The F1 numbers in these are technically valid (the math is right)
but they measure **prompt-structure variation** (baseline prompt vs
baseline prompt + "no docs found" notice) rather than RAG benefit.

**These results must be re-run with the fixed retriever before any
thesis claim about RAG benefit can be made.**

### Re-run plan

1. **Smoke validation** (in flight): `mastodon × qwen3 × N=2` to
   confirm `raw_runs/` now show real retrieval.
2. **qwen3 N=20 baseline re-run** on the same 10 projects.
   Approximately 1.5-2 days on the local Mac at the previously
   observed ~50 min/project rate. This is the new "ground truth"
   baseline for the thesis.
3. **Cross-family N=10 re-run** on the 4-project subset. Approximately
   4-5 hours per model × 4 models = ~20 hours total.
4. **Optional**: lower the `match_threshold` from 0.85 to ~0.75 in
   `EvaluationConfig` as well, to stop hiding correct generations like
   "Enforce PKCE for Public Clients" (sim 0.806 to "Authorization Code
   PKCE Enforcement"). If kept at 0.85, that should be a documented
   methodology choice with a sensitivity analysis.

### Communication to supervisor

The cross-family results presented on 2026-05-19 morning (the 4×4
verdict matrix with the "keycloak anchor evaporates" headline) need
to be retracted in their current form. The retraction should
emphasize:

- The methodology contribution is strengthened, not weakened: the
  per-story Wilcoxon framework + raw-outputs archival caught a
  pipeline bug that the aggregate statistics could not.
- This is exactly why reproducibility-engineering matters and why the
  Goodman/Fanelli/Ioannidis (2016) framework distinguishes
  *reproducibility* (re-run produces same results, which the broken
  pipeline does) from *validity* (results measure what they claim to,
  which the broken pipeline does not).
- The fix is small (one config-default change + docstring fix +
  regression tests).
- Re-running the cross-family experiment will produce *new* findings —
  this time about actual RAG benefit, not about prompt structure.

---

## Note on the methodology contribution

The audit could only happen because:

- Per-story Wilcoxon framing surfaced the *signal sparsity* (half the
  stories contribute nothing)
- The Option B raw_runs/ archival surfaced the *retrieval failure*
- Combined, they show that the brittle-threshold + prompt-difference
  artifact is *reproducible* across qwen3 N=10 and N=20 — but not
  *valid* as a measure of RAG.

Reproducibility ≠ validity. That distinction, once we recover the
thesis, is itself a Chapter 6 methodology contribution.
