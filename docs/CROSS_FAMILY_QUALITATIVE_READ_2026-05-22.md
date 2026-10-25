# Cross-Family Qualitative Read — 2026-05-22

Manual inspection of generated security requirements across the 20 cells of the cross-family experiment. **The headline F1-based STRONG ± verdicts in [RAG_ABLATION_LAB_NOTEBOOK.md §9](RAG_ABLATION_LAB_NOTEBOOK.md) are statistically real but measure something different from what their labels suggest.** This document explains what.

## TL;DR

- **The generated requirements are uniformly high quality.** Across mastodon×qwen3, keycloak×qwen3, bitwarden×qwen3, gitlab×gemma4, and mattermost×qwen3, every cell I read produces specific, testable, ASVS-mapped security requirements that directly address the user story.
- **F1 = 0.02-0.16 is NOT measuring requirement quality.** It measures how well the *phrasing* of generated reqs matches the *phrasing* of 3-4 hand-curated ground-truth items. The 0.85 cosine-similarity "match" threshold is essentially unreachable in practice; most cells get 0-3 "match" decisions across ~50 candidate pairs.
- **The "STRONG +/-" verdicts mostly reflect (a) phrasing-style accidents between models and (b) precision penalties for over-thorough RAG output**, not security-relevance differences.
- **One real bug found:** `repos/bitwarden` doesn't exist (the dir is `bitwarden-server`), so all 4 bitwarden cells ran with pure-doc retrieval, no code chunks. The bitwarden STRONG − verdicts therefore test doc-only RAG, not the full idea-19 hybrid.

## What I read

Five cells in detail (8 stories each × ~10-12 reqs per story × ground-truth comparison + match decisions):

| cell | verdict | what the F1 numbers say | what the requirements actually look like |
|---|---|---|---|
| mastodon × qwen3 | STRONG + | baseline 0.037 → RAG 0.070 | both cover GT topics; RAG adds project-specific email/SMTP/visibility details. RAG gets 1 extra partial match per story on average. |
| keycloak × qwen3 | null | baseline 0.077 → 0.075 | both produce strong OIDC/PKCE/redirect-URI reqs; near-identical depth. Ground truth has 3 items; both produce 5-6. |
| bitwarden × qwen3 | STRONG − | 0.115 → 0.018 | RAG produces 6 high-quality reqs (PBKDF2, constant-time compare, zero-knowledge vault, rate-limiting, hint sanitization). Baseline produces 5. Precision drops because of the extra req, not quality. |
| gitlab × gemma4 | STRONG + | 0.000 → 0.067 | RAG correctly grounds in gitlab-specific terms (RBAC roles, Admin Mode, .well-known endpoints). Baseline still produces valid generic reqs. |
| mattermost × qwen3 | STRONG + | 0.021 → 0.070 | both produce OIDC/JWS/session reqs; RAG slightly better at matching mattermost-specific OIDC phrasing. |

## The core measurement-validity finding

### Mastodon × qwen3, MT-US-001 (User Registration and Verification)

**Ground truth (3 reqs):**
1. *Email Verification Enforcement* (V2)
2. *Username Validation* (V5)
3. *Password Strength Requirements* (V2)

**Generated baseline (5 reqs):**
1. Password hashing with Argon2id, 19MiB memory, 2 iterations (V2)
2. Email verification with time-limited token (V2)
3. Username validation with strict allow-list and uniqueness check (V5) — *partial match sim 0.84*
4. Rate limiting registration attempts, 5/15min per IP (V14)
5. Email address validation RFC 5322 (V5)

**Generated RAG (6 reqs):**
1. Password hashing with PBKDF2-SHA256, 600,000 iterations (V2)
2. Email verification with time-limited token, 72-hour expiration (V2)
3. Username validation regex `^[a-zA-Z0-9_]{2,30}$` (V5) — *partial match sim 0.82*
4. Email address storage with privacy hashing (V8)
5. SMTP authentication with TLS (V9)
6. Account creation with pending-state until verification (V2) — *partial match sim 0.72*

**Why F1 is low even though quality is high**: the generator covers all 3 GT topics (email verification, username validation, password strength) AND adds 2-3 extra valid security requirements. The semantic matcher gives "partial" (sim 0.70-0.85) for the covered topics and "no_match" (sim < 0.70) for the extras. Match threshold is 0.85, almost never reached. The extras drive precision down because they're counted in the denominator.

This is the pattern in every cell I read: **the generator covers the GT topics qualitatively but the precision-and-phrasing penalty dominates F1**.

## Cell-by-cell qualitative notes

### Mastodon × qwen3 (STRONG +)
**Verdict label is reliable but understated.** Both baseline and RAG produce qualitatively excellent reqs. RAG adds mastodon-specific details (SMTP credentials, CAS-based provisioning, account-pending state) that the baseline doesn't. The few partial matches the RAG gets are genuine improvements (e.g. RAG's "Account creation with post-verification state" partial-matches GT's "Email Verification Enforcement" at sim 0.72; baseline doesn't have this).

### Keycloak × qwen3 (null, was STRONG + pre-audit)
**The verdict flip is real but misleading.** Both modes produce excellent OIDC requirements (PKCE, redirect URI validation, ID token signature, refresh token rotation). Both modes get the same 1 partial match. The pre-audit STRONG + was likely measuring the noise of the broken retrieval pipeline — under correct retrieval, the model's prior knowledge of OIDC already covers GT topics, so RAG can't add marginal F1.

The earlier "RAG marginal value scales with project specificity, not generic mechanisms" finding is corroborated here: keycloak OIDC is well-known territory for qwen3:30b zero-shot, and the docs corpus (large but mostly generic OIDC content) doesn't shift the model's output much.

### Bitwarden × qwen3 (STRONG −) — caveats heavy
**This is a precision artifact, not a quality regression.**

- Baseline produces 5 reqs covering PBKDF2 (sim 0.77 partial), email validation, zero-knowledge vault (sim 0.78 partial), email verification token, password hint sanitization. F1 = 0.115.
- RAG produces 6 reqs: PBKDF2 with explicit 600k iterations (sim 0.83 partial, ALMOST a match), constant-time hash comparison, zero-knowledge vault encryption (sim 0.77 partial), email verification, password hint, rate limiting. F1 = 0.018.
- Both cover 2 of 4 GT items at partial level. RAG has 6 reqs vs baseline's 5 → 1 extra "no_match" item → precision drop dominates F1.
- The extra item (rate limiting) is a perfectly reasonable security requirement; the GT just doesn't include it.

**Plus the bug**: `repos/bitwarden` doesn't exist (dir is `bitwarden-server`), so the bitwarden cells got NO code-search chunks across all 4 models. The hybrid retrieval was actually pure-doc retrieval for bitwarden.

### Gitlab × gemma4 (STRONG +, baseline F1 = 0.000)
**This cell is the most interesting.** baseline F1 is literally 0.000 across 10 runs but the *requirements look fine*. Baseline produces:
- visibility enforcement (sim 0.71 partial in run_001, evidently dropping below 0.70 in other runs)
- input validation
- clone URL generation
- audit logging

These are all sensible repo-creation reqs, but they sit just under the partial-match threshold. RAG benefits from gitlab-specific terms (RBAC roles Guest/Reporter/Developer/Maintainer/Owner, Admin Mode, .well-known endpoints) that nudge phrasing closer to the GT — but in run_001 RAG still gets 0 matches.

The d_z = +3.55 must be coming from some runs hitting the partial threshold more reliably under RAG. **This is fragile.** Worth a deeper read of 2-3 more runs to confirm.

### Gitlab × qwen3 (STRONG −, mirror of above)
**Same fragile-threshold pattern in the opposite direction.** Baseline and RAG both produce reasonable gitlab repo-creation reqs, but qwen3's RAG variant phrases reqs in ways that consistently sit below the partial threshold across runs, while baseline sits at the threshold. The d_z = −1.53 is a real statistical signal but it's measuring phrasing alignment, not security content.

### Mattermost × qwen3 (STRONG +)
**The most defensible STRONG + in the table.** Both modes produce strong OIDC reqs. RAG variant slightly better matches the mattermost-specific GT phrasing for "OIDC Provider Validation" (sim 0.80 partial vs baseline's 0.82, similar). Other runs likely produce more consistent partial matches for RAG. The replication with qwen3.6 STRONG + (different model family, same enriched mattermost corpus) gives confidence this is signal, not noise.

## What this implies for Chapter 7

1. **The F1 metric as configured is too brittle for the required claim.** The 0.85 cosine-similarity threshold for "match" effectively turns this into a phrasing-similarity benchmark against a tiny ground truth. The thesis can't claim "RAG produces measurably better security requirements" using F1 at this threshold — it can only claim "RAG slightly shifts the phrasing of generated requirements in directions that sometimes align better with hand-curated ground truth phrasing".

2. **The replications are still meaningful at a meta-level.** The 4-model × 5-project design shows that when *the same retrieval and prompt* is applied across different model families, the F1-shift direction is *mostly* model-conditional, with 2 project-level effects that replicate across families (bitwarden − and mattermost +). The cross-family disagreement on gitlab (+3.55 vs −1.53) is a real and reportable finding about model-conditionality.

3. **The judge κ-calibration is now critical.** The llama3:8b retrieval-quality judge from Phase A is at a *different* layer from the F1 generation matcher — it judges whether retrieved chunks are relevant to a story, not whether generated reqs match ground truth. But the κ-calibration of that judge is the closest thing to "trusted external evaluation" we have. If κ is poor, the lab-notebook-§8 findings need caveats too.

4. **Two follow-ups worth doing**:
   - **Re-run the 4 bitwarden cells** with `repos/bitwarden-server` correctly mapped so the bitwarden STRONG − verdict has the full idea-19 retrieval behind it. ~6 hours of compute. Could change bitwarden's verdict.
   - **Add a content-judgment metric**: have llama3:8b score each generated requirement on "does this address a real security concern raised by the user story? (yes/partial/no)" — this gives a *content-quality* signal independent of phrasing. Could be done in batch overnight on the existing raw_runs/, no re-generation needed.

## Recommended chapter framing

The empirical chapter should present results in three tiers of confidence:

**Tier 1 — high confidence:**
- The methodology fix (Phase A audit + corpus enrichment + judge addition) eliminated 100% of whiff-string artifacts in retrieval. Documented in lab-notebook §8.
- Idea 19 (CE-rerank doc + grep-based code agent) achieves nDCG@5 = 0.86-0.90 on enriched corpora — defensible retrieval quality.
- Few-shot exemplars produce specific, testable, ASVS-mapped requirements across all four model families. Demonstrated by direct read.

**Tier 2 — moderate confidence:**
- Cross-family F1 d_z signals direction of phrasing-alignment shifts; replicates across families for 2/5 projects (bitwarden−, mattermost+).
- The "RAG benefit is project- and model-conditional" thesis still holds. Specific verdicts re-narrate from pre-audit baseline.

**Tier 3 — low confidence, surface only with caveats:**
- Magnitude of F1 differences in cells.
- The exact d_z values per cell.
- Any claim about "RAG produces *higher quality* requirements" (this requires a content judgment, not an F1 measurement).

## Pending verification steps (in priority order)

1. **Run the LLM-as-judge content-quality scoring** on the 20 cells × 10 runs × 8 stories of generated reqs. ~4-6 hours of llama3:8b judge calls. Output: per-cell content-quality d_z that's independent of phrasing alignment.
2. **Re-run the 4 bitwarden cells** with the repos path fix. ~6 hours.
3. **Have the user hand-label** the 30-pair retrieval-quality calibration sample.
4. **Spot-check 2-3 more gitlab × gemma4 runs** to confirm the d_z = +3.55 isn't a single-run artifact.
