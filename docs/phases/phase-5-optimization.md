# Phase 5: Retrieval Optimization (Optional)

## When to Use This Phase

**Only pursue Phase 5 if Phase 4 evaluation shows:**
1. RAG underperforms or marginally beats zero-shot, AND
2. Analysis suggests retrieval quality is the bottleneck (wrong/irrelevant context retrieved)

**Skip this phase if:**
- RAG significantly beats zero-shot (≥20% F1 improvement) → optimization is bonus
- The issue is generation quality, not retrieval → fix prompts instead

---

## Diagnostic: Is Retrieval the Problem?

Before optimizing, diagnose the issue:

```python
# In your evaluation analysis
for result in rag_results:
    print(f"Story: {result.user_story.title}")
    print(f"Retrieved context:")
    for i, chunk in enumerate(result.retrieved_chunks):
        print(f"  [{i+1}] {chunk[:100]}...")
    print(f"Generated requirements: {len(result.requirements)}")
    print(f"Matched ground truth: {result.matches}")
    print("---")
```

**Signs retrieval is the problem:**
- Retrieved chunks are off-topic or irrelevant
- Correct information exists in corpus but wasn't retrieved
- Similar queries retrieve very different content

**Signs retrieval is NOT the problem:**
- Retrieved chunks contain relevant information
- Generator fails to use the context effectively
- Requirements are generic despite specific context

---

## Optimization Options

### Option A: Cross-Encoder Re-ranking (Recommended First)

**What it does:** Uses a specialized model trained for relevance scoring to re-rank semantic search results.

**Pros:**
- Fast (~50ms for 10 chunks)
- No LLM dependency
- Well-established technique
- Already have `sentence-transformers` dependency

**Implementation:**

```python
# src/secreqgen/retrieval/reranker.py

from sentence_transformers import CrossEncoder

class CrossEncoderReranker:
    def __init__(self, model_name: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"):
        self.model = CrossEncoder(model_name)

    def rerank(
        self,
        query: str,
        chunks: list[str],
        top_k: int = 5
    ) -> list[tuple[str, float]]:
        """Re-rank chunks by relevance to query."""
        pairs = [(query, chunk) for chunk in chunks]
        scores = self.model.predict(pairs)

        ranked = sorted(zip(chunks, scores), key=lambda x: x[1], reverse=True)
        return ranked[:top_k]
```

**Integration with retriever:**

```python
class SecurityRetriever:
    def __init__(self, vectorstore, embedder, reranker=None):
        self.reranker = reranker  # Optional

    def retrieve(self, query: str, top_k: int = 5) -> RetrievalResult:
        # 1. Get more candidates than needed
        n_candidates = top_k * 2 if self.reranker else top_k
        embedding = self.embedder.embed_text(query)
        results = self.vectorstore.query(embedding, n_results=n_candidates)

        # 2. Re-rank if enabled
        if self.reranker:
            chunks = [r.content for r in results]
            reranked = self.reranker.rerank(query, chunks, top_k=top_k)
            # Map back to full results...

        return RetrievalResult(...)
```

---

### Option B: LLM Re-ranking (For Explainability)

**What it does:** Uses the LLM to score and explain relevance of retrieved chunks.

**Pros:**
- Provides reasoning (useful for thesis analysis)
- Can be prompted for security-specific relevance
- Uses existing LLM infrastructure

**Cons:**
- Slow (2-5 seconds per query)
- Less consistent than cross-encoder
- Uses LLM capacity that could go to generation

**Implementation:**

```python
# src/secreqgen/retrieval/llm_reranker.py

class LLMReranker:
    def __init__(self, llm_client):
        self.llm = llm_client

    def rerank(
        self,
        query: str,
        chunks: list[str],
        top_k: int = 5
    ) -> list[dict]:
        prompt = f"""
        Query: {query}

        Rank these document chunks by relevance for generating security requirements.
        Return JSON: [{{"chunk_index": 0, "score": 8, "reason": "..."}}]

        Chunks:
        {self._format_chunks(chunks)}
        """

        response = self.llm.generate(prompt, json_mode=True)
        rankings = json.loads(response.content)
        rankings.sort(key=lambda x: x["score"], reverse=True)
        return rankings[:top_k]
```

**When to prefer LLM re-ranking:**
- You need to explain WHY certain chunks were selected
- Cross-encoder doesn't improve results
- You're writing up thesis analysis

---

### Option C: Query Expansion

**What it does:** Uses LLM to add technical synonyms to the query before retrieval.

**Why it might help:** Our Bitwarden testing showed queries with technical terms (e.g., "master key PBKDF2") performed better than natural language queries.

**Pros:**
- Can bridge vocabulary gap between user query and technical docs
- Single LLM call before retrieval

**Cons:**
- Risk of adding irrelevant terms
- May make results worse if expansion is poor
- Adds latency before any retrieval

**Implementation:**

```python
# src/secreqgen/retrieval/query_expander.py

class QueryExpander:
    def __init__(self, llm_client):
        self.llm = llm_client

    def expand(self, query: str) -> str:
        prompt = f"""
        Expand this security-related query with technical synonyms and related terms.
        Keep the original meaning. Add algorithm names, protocol names, security concepts.

        Query: {query}

        Return only the expanded query, no explanation.
        """

        response = self.llm.generate(prompt)
        return response.content.strip()
```

**Recommendation:** Try re-ranking first. Only add query expansion if re-ranking alone doesn't help.

---

### Option D: Hybrid Search (Advanced)

**What it does:** Combines semantic search (embeddings) with keyword search (BM25).

**Why it might help:** Catches exact keyword matches that semantic search might miss.

**Cons:**
- ChromaDB doesn't support BM25 natively
- Requires additional infrastructure
- May be overkill for this use case

**Implementation approach:**
1. Run semantic search (ChromaDB)
2. Run BM25 search separately (e.g., using `rank_bm25` library)
3. Combine results using Reciprocal Rank Fusion (RRF)

**Recommendation:** Only pursue if Options A-C don't help and you have time.

---

## Evaluation Strategy for Optimizations

For each optimization, measure impact:

```python
# Compare retrieval modes
modes = [
    ("baseline", retriever_no_rerank),
    ("cross_encoder", retriever_with_cross_encoder),
    ("llm_rerank", retriever_with_llm_rerank),
]

for mode_name, retriever in modes:
    generator = SecurityRequirementsGenerator(llm, retriever)
    results = generator.generate_batch(test_stories)
    metrics = evaluate(results, ground_truth)

    print(f"{mode_name}: P={metrics.precision:.2f} R={metrics.recall:.2f} F1={metrics.f1:.2f}")
```

**What to report in thesis:**
- Baseline (no re-ranking) metrics
- With re-ranking metrics
- Delta and whether it's statistically significant
- Qualitative examples of improved/degraded retrieval

---

## Implementation Order (If You Proceed)

1. **Cross-encoder re-ranking** (1 day)
   - Simplest, fastest, likely effective
   - Run evaluation, compare to baseline

2. **If cross-encoder helps:** Stop here, document findings

3. **If cross-encoder doesn't help:** Try LLM re-ranking (1 day)
   - May provide security-domain understanding
   - Analyze the explanations

4. **If neither helps:** Try query expansion (1 day)
   - Higher risk, but might address vocabulary mismatch

5. **Document everything** in log.md and thesis

---

## Completion Checklist (if pursued)

- [ ] Identified retrieval as the bottleneck (via Phase 4 analysis)
- [ ] Implemented chosen re-ranking approach
- [ ] Measured impact on P/R/F1
- [ ] Compared multiple approaches (if applicable)
- [ ] Documented findings in docs/log.md
- [ ] Updated thesis with optimization analysis

---

## Files Created (if pursued)

```
src/secreqgen/retrieval/
├── retriever.py           # Updated with optional reranker
├── reranker.py            # Cross-encoder implementation
├── llm_reranker.py        # LLM-based re-ranking (optional)
└── query_expander.py      # Query expansion (optional)
```

---

## Key Insight

The value of Phase 5 for the thesis is not just improving metrics, but **understanding the system**:

- Why does re-ranking help (or not)?
- What kinds of queries benefit most?
- Where does the vocabulary mismatch occur?

This analysis is often more valuable for the thesis than marginal metric improvements.
