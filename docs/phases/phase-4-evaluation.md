# Phase 4: Evaluation Framework (Steps 4.1-4.5)

## Purpose

This phase answers the core thesis question: **Does RAG improve security requirements generation?**

The evaluation framework enables:
1. Quantitative comparison (Precision, Recall, F1)
2. Qualitative analysis (reviewing generated requirements)
3. Data-driven decisions about optimization (→ Phase 5)

## Step 4.1: Semantic Similarity Matcher
**Create `src/secreqgen/evaluation/semantic_matcher.py`**

```python
@dataclass
class MatchResult:
    generated_id: str
    ground_truth_id: Optional[str]
    similarity_score: float
    match_type: str  # "match" | "partial" | "no_match"

class SemanticMatcher:
    def __init__(self, match_threshold=0.85, partial_threshold=0.70): ...
    
    def compute_similarity(self, text1: str, text2: str) -> float:
        # SBERT embeddings + cosine similarity
    
    def match_requirements(
        self, 
        generated: list[SecurityRequirement],
        ground_truth: list[dict]
    ) -> list[MatchResult]:
        # Greedy matching: each generated → best unmatched GT
        # > 0.85 = match (True Positive)
        # 0.70-0.85 = partial (flag for review)
        # < 0.70 = no_match (False Positive / hallucination)
```

Compare: `f"{req.title}. {req.description}"` for both generated and GT.

---

## Step 4.2: Metrics Calculator
**Create `src/secreqgen/evaluation/metrics.py`**

```python
@dataclass
class MetricsBreakdown:
    true_positives: int
    false_positives: int
    false_negatives: int
    partial_matches: int
    precision: float      # TP / (TP + FP)
    recall: float         # TP / (TP + FN)
    f1_score: float       # 2 * (P * R) / (P + R)
    hallucination_rate: float  # FP / total_generated

class MetricsCalculator:
    def calculate(self, matches: list[MatchResult], total_ground_truth: int) -> MetricsBreakdown: ...
    
    def compare_conditions(self, baseline: MetricsBreakdown, rag: MetricsBreakdown) -> dict:
        # Return deltas and percentage improvements
        # Flag if RAG F1 >= Baseline F1 * 1.20 (20% improvement target)
```

**Definitions:**
- True Positive: Generated matches GT (sim > 0.85)
- False Positive: Generated has no GT match (hallucination)
- False Negative: GT not matched by any generated

---

## Step 4.3: Baseline Runner
**Create `src/secreqgen/evaluation/baseline.py`**

```python
class BaselineRunner:
    def run_baseline(self, stories: list[UserStory]) -> list[GenerationResult]:
        # Generator with rag_enabled=False
    
    def run_comparative_evaluation(
        self, 
        stories: list[UserStory],
        rag_generator: SecurityRequirementsGenerator
    ) -> dict:
        # 1. Run baseline on all stories
        # 2. Run RAG-enhanced on all stories
        # 3. Return both result sets for comparison
```

---

## Step 4.4: Evaluation Pipeline
**Create `scripts/run_evaluation.py`** or integrate into CLI

Full pipeline:
1. Load test stories from `data/user_stories/`
2. Load ground truth from `data/ground_truth/`
3. Run baseline generation
4. Run RAG-enhanced generation
5. Match both against ground truth
6. Calculate metrics for both
7. Compare conditions
8. Generate report to `docs/evaluation_results/`

**Report includes:**
- Per-story breakdown
- Aggregate metrics (baseline vs RAG)
- Improvement delta
- Partial matches flagged for manual review
- Sample outputs for qualitative review

---

## Step 4.5: Wire Evaluation CLI
Update `cli.py`:
- `evaluate compare <generated> <ground_truth> --threshold 0.85`
- `evaluate report <results_dir>`

---

## Ground Truth Format
`data/ground_truth/juice_shop.json`:
```json
{
  "story_id": "US-001",
  "requirements": [
    {
      "id": "GT-001",
      "title": "File Upload Validation",
      "description": "Validate file type using magic bytes...",
      "asvs_category": "V12 - Files and Resources"
    }
  ]
}
```

---

## Completion Checklist
- [ ] Semantic matcher computing similarities correctly
- [ ] Metrics calculating P/R/F1 accurately
- [ ] Baseline runner producing results
- [ ] Full evaluation pipeline working end-to-end
- [ ] Comparison report generating
- [ ] Partial matches flagged correctly
- [ ] CLI evaluate commands functional
- [ ] Test with sample data
- [ ] docs/log.md updated

---

## Interpreting Results & Next Steps

After running evaluation, use this decision tree:

```
                    RAG F1 vs Zero-shot F1?
                           /          \
                  RAG wins by         RAG wins by
                   ≥20%                <20% or loses
                    /                       \
           ┌───────┴───────┐         ┌──────┴──────┐
           │ THESIS SUCCESS │         │ INVESTIGATE │
           │               │         │             │
           │ Document the  │         │ Is retrieval │
           │ improvement.  │         │ finding      │
           │ Phase 5 is    │         │ relevant     │
           │ optional bonus│         │ context?     │
           └───────────────┘         └──────┬──────┘
                                           /    \
                                        YES      NO
                                        /          \
                                   Generator    → Phase 5:
                                   issue        Retrieval
                                   (debug       Optimization
                                   prompts)
```

**Key metrics to analyze:**
- **Precision**: Are generated requirements valid? (Low = hallucinations)
- **Recall**: Are we finding all needed requirements? (Low = missing requirements)
- **Retrieval quality**: Were the right documents retrieved? (Check `retrieved_context` in results)

See **Phase 5: Retrieval Optimization (Optional)** if retrieval quality is the bottleneck.
