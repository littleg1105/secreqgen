# RAG Improvement Research — 2026-05-24

Goal: push the SecReqGen pipeline to a state where generated security
requirements are demonstrably more project-specific than baseline,
strengthening the chapter-7 claim that "RAG over project docs produces
higher-quality requirements grounded in project-specific knowledge."

## Diagnostic findings

On the 5 adversarial-probe stories (qwen3.6 / N=5), the
retrieval-to-generation utilization analysis shows:

| story | project-specific tokens in retrieved context | tokens cited in generated reqs | utilization |
|---|---|---|---|
| MT-ADV-001 (CAS SSO) | 24 CAS_* vars | 8 | **33%** |
| MT-ADV-002 (limited federation) | 3 mode tokens | 1 | **33%** |
| MT-ADV-003 (S3 with alias host) | 13 S3_/AWS_ tokens | 5 | **38%** |
| MT-ADV-004 (safe re-attachment) | 1 token | 1 | 100% |
| MT-ADV-005 (followers sync) | 4 endpoint tokens | 2 | **50%** |

**Conclusion**: retrieval recall is excellent. The bottleneck is the
generator — it has the project-specific tokens in front of it but
chooses to paraphrase them into generic OWASP language instead of
citing them by name.

Concrete example from MT-ADV-001:

> Context contains: `CAS_UID_KEY`, `CAS_DISPLAYNAME_KEY`,
> `CAS_EMAIL_KEY`, `CAS_IMAGE_KEY`, `CAS_NICKNAME_KEY`,
> `CAS_FIRST_NAME_KEY`, `CAS_LAST_NAME_KEY`, `CAS_LOCATION_KEY`,
> `CAS_PHONE_KEY`, `CAS_NAME_KEY`, `CAS_VALIDATE_URL`,
> `CAS_LOGIN_URL`, `CAS_LOGOUT_URL`, `CAS_CALLBACK_URL`, `CAS_URL`,
> `CAS_HOST`, `CAS_PORT`, `CAS_SSL`, `CAS_CA_PATH`,
> `CAS_DISABLE_SSL_VERIFICATION`,
> `CAS_SECURITY_ASSUME_EMAIL_IS_VERIFIED`, `CAS_UID_FIELD`,
> `CAS_ENABLED`, `CAS_DISPLAY_NAME` (24 vars)

> qwen3.6 RAG req BL-2 (verbatim):
> *"The user identifier MUST be derived from a specific, pre-configured
> CAS attribute (e.g., 'uid' or 'mail') rather than the CAS principal
> string. The attribute value MUST be validated for format..."*

The retrieved docs contain `CAS_UID_KEY` (which IS the configurable
attribute name) but the requirement abstracts it into "a specific,
pre-configured CAS attribute". This is the utilization gap.

## Improvement levers, ranked by expected impact

### Lever 1 — Two-stage extract-then-cite generation (HIGH impact)

**Mechanism**: split generation into two LLM calls.

```
Stage A (extraction):
  Prompt: "From the retrieved project security documentation below, list
  every project-specific configuration key, environment variable, API
  endpoint, class name, or feature name that is relevant to this user
  story. Output a JSON array of token strings."
  
Stage B (grounded generation):
  Prompt: standard security-requirements generation + the few-shot
  exemplars + Stage A's extracted token list + "Each requirement MUST
  reference at least one token from the extracted list. Generic
  paraphrasing of project-specific tokens is NOT acceptable."
```

**Expected effect**: token utilization should jump from 33-50% to
80%+. The model is forced to surface the specific terms because
they're in front of it as a checklist, not just buried in context.

**Cost**: doubles the LLM calls per requirement; ~2× latency.

**Novelty for thesis**: this is the "extract-then-write" pattern,
similar to skeleton-of-thought and atomic-reference-grounded generation
but applied specifically to security-requirements engineering. Worth a
methodology subsection in Chapter 7.

### Lever 2 — Few-shot exemplars rewritten to cite project tokens (MEDIUM impact)

**Mechanism**: current exemplars (Argon2id 19MiB, AES-256-GCM) show
parameter-level specificity but no project-specific token citations
because the exemplars are deliberately generic (so they don't
contaminate the evaluation by referencing actual project content).
Replace with structurally similar exemplars that demonstrate citing
a hypothetical project's config keys:

```
EXAMPLE STORY: Configure SSO with provider X
EXPECTED OUTPUT:
{
  "requirements": [{
    "title": "Identifier mapping via SSO_USER_ATTRIBUTE",
    "description": "Server MUST use the SSO attribute named in
      SSO_USER_ATTRIBUTE (default 'uid') as the local user
      identifier. Server MUST NOT fall back to the principal name
      if this attribute is missing.",
    ...
  }]
}
```

**Expected effect**: shows the LLM that the right output structure
includes specific token citations, not abstract references.

**Cost**: free, just prompt template edit.

**Novelty for thesis**: small. Mostly a defensive change to make the
existing pipeline behave correctly.

### Lever 3 — Hard system-prompt constraint on grounded citation (LOW-MEDIUM impact)

**Mechanism**: add an unambiguous line to the system prompt:

> "Every generated requirement MUST cite at least one specific
> configuration key, environment variable, endpoint, class name, or
> feature name from the Project Security Documentation. Requirements
> that paraphrase project-specific tokens into generic OWASP language
> instead of citing them by name will be rejected."

**Expected effect**: modest improvement; LLMs sometimes ignore
soft constraints, but a hard line raises the floor.

**Cost**: free.

**Novelty for thesis**: zero — it's just prompt engineering.

### Lever 4 — Self-critique citation check (MEDIUM-HIGH impact)

**Mechanism**: after generation, ask a critique LLM:

```
"For each requirement below, identify the doc passage it grounds in.
If a requirement does not ground in any specific doc passage, mark it
as 'ungrounded'. Return JSON: [{'req_id': N, 'grounded_in':
'<verbatim quote from docs>' or null}, ...]"
```

Then regenerate ungrounded requirements with explicit doc passages
attached.

**Expected effect**: high precision improvement on grounded citations.

**Cost**: triples LLM calls per requirement (initial + critique +
regenerate). Worth the cost only on the final pass, not in the
high-volume cross-family experiment.

**Novelty for thesis**: medium. This is essentially the Self-RAG /
CRAG pattern from 2024 literature, adapted to citation grounding.

### Lever 5 — Retrieval precision filtering (LOW impact)

**Mechanism**: post-process retrieved chunks to drop those that don't
contain any project-specific tokens (env vars, function names,
endpoint names). This reduces noise in the context window.

**Expected effect**: low — the LLM is already failing to utilize the
relevant chunks; reducing noise doesn't help if the model isn't
attending to the relevant ones.

**Cost**: low.

**Novelty for thesis**: small.

## Recommended path

**Combine Levers 1 + 2 + 3.** Together they directly address the
diagnostic finding without massively increasing infrastructure cost:

- Lever 1 (extract-then-cite) is the headline methodology change and
  the most defensible Chapter-7 contribution.
- Lever 2 (citation-aware exemplars) is a low-cost change that aligns
  the few-shot signal with the system-prompt constraint.
- Lever 3 (hard citation constraint) is a free reinforcement.

Skip Lever 4 (self-critique) for the main experiment but reserve it
for the showcase examples that go in the chapter prose — those few
demos can afford the 3× LLM cost.

Skip Lever 5 — diagnostic shows retrieval is already good enough.

## Proposed implementation — idea 21: grounded-citation generation

New script: `scripts/run_idea21_grounded_citation.py`

Per (project, story):

```
1. Run idea-19 hybrid retrieval (doc CE-rerank + code grep agent)
   → retrieved_context (5070 chars on average for adversarial probes)

2. Extract project-specific tokens (NEW):
   prompt: "From the security docs below, list every config key,
           env var, endpoint, class, or feature name relevant to
           this user story. Output JSON: {tokens: [...]}"
   input: retrieved_context + user_story
   output: token_list (typically 10-30 items per story)

3. Grounded generation (NEW prompt template):
   - Reuse idea-15 few-shot but with citation-aware exemplars
   - Hard constraint in system prompt: "every requirement MUST cite
     at least one token from the extracted token_list"
   - Input: retrieved_context + user_story + token_list + few-shot
   - Output: 4-6 security requirements with explicit token citations

4. Optional verification (for showcase examples only):
   prompt: "for each requirement, identify the verbatim doc passage
            it grounds in"
   regenerate any ungrounded reqs with that passage attached
```

Output shape stays compatible with the existing pipeline so the F1
matcher and content-quality judge can score it.

## Expected outcomes

If the diagnostic is right, idea 21 should produce:

- Token utilization rising from ~35% to ~80% on adversarial probes
- F1 on adversarial probes improving (more requirement texts contain
  GT terminology verbatim)
- Citation-distinct-from-prompt rising from 5-7 to 15-20 per cell
- Content-quality judge ratings unchanged at ~97-100% (already
  ceiling)
- Cost: ~2× the current generation time

## What to validate

1. Re-run adversarial probe on qwen3 + qwen3.6 under idea 21
2. Compare token utilization (idea-15 vs idea-21)
3. Manual qualitative read of the new outputs
4. If qwen3 utilization jumps to ≥70%, run idea 21 on a subset of the
   main cross-family experiment (mastodon × all 4 models, N=5) to
   confirm the improvement generalizes

## Open methodology questions

1. **Token-extraction prompt sensitivity**: what counts as a "project-
   specific token"? My definition is configuration keys, environment
   variables, named endpoints, class/file names, feature names. Need
   to make sure the extraction prompt produces consistent outputs.

2. **Citation grounding vs over-citation**: an LLM forced to cite
   could over-cite (every requirement crammed with token references)
   in a way that hurts readability. Need to add a guard: "cite tokens
   only when materially relevant; do not enumerate".

3. **Generalization beyond Mastodon**: the adversarial probe and the
   diagnostic are mastodon-only so far. Need to verify the
   utilization-gap finding on keycloak (Vault syntax,
   `realm-management` role) and gitlab (RBAC role names, `[MASKED]`).
