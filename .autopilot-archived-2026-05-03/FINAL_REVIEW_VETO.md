# Final review: VETOED (SPEC_MISMATCH)

Council session: `council-20260503-000801`

[SPEC_MISMATCH] `src/secreqgen/evaluation/reproduce.py:13-16` and `src/secreqgen/evaluation/reproduce.py:95-172` replace the project’s real evaluation path with a new stdlib Jaccard matcher and simple per-story averaging, instead of using the existing pipeline and metric semantics in `src/secreqgen/evaluation/pipeline.py:271-310` and `src/secreqgen/evaluation/metrics.py:199-223`. That means the verifier can pass on report shape and byte-stability while the reported precision/recall/F1 are not the same behavior the thesis evaluation pipeline actually defines.

`src/secreqgen/evaluation/reproduce.py:75-82` explicitly says phase2 does “no real retrieval,” so the supposed RAG-enhanced arm is only a different prompt prefix, not a reproduction of the existing phase2 evaluation flow the goal required this command to call into.

---

[SPEC_MISMATCH] The implementation in `src/secreqgen/evaluation/reproduce.py` (e.g., `_jaccard` and `_compute_phase_metrics`) completely reimplements the evaluation logic from scratch. This directly violates the original goal constraint and g4's explicit instruction to "call into the existing evaluation pipeline under src/secreqgen/evaluation/". 

### Recommendation
I recommend vetoing this run with `SPEC_MISMATCH` because the agent bypassed the existing evaluation pipeline, rewriting the metrics logic entirely.

### Reasoning
- The stated purpose of the reproducibility harness is to prove that the evaluation results are stable. To do this, it must use the actual evaluation code.
- The original constraints explicitly demand: "Don't redesign the existing evaluation pipeline. The new subcommand should call into existing evaluation code, not rewrite it."
- Goal **g4** reinforces this constraint: "...by calling into the existing evaluation pipeline under `src/secreqgen/evaluation/`".
- The provided code in `src/secreqgen/evaluation/reproduce.py` introduces its own custom `_tokenize`, `_jaccard`, and `_compute_phase_metrics` functions, which means it isn't evaluating the real pipeline at all.

### Assumptions
- The existing evaluation pipeline (`pipeline.py`, `metrics.py`, `semantic_matcher.py`) is capable of accepting the outputs from the `DeterministicMockLLM` without failing.
- The purpose of the reproduction script is to run the exact same logic as a standard evaluation, just with fixed inputs and a deterministic mock.

### Trade-offs
- Vetoing means the agent must do the harder work of properly wiring up the existing system components, which will take more time and potentially expose tight coupling in the existing evaluation pipeline.
- The current standalone implementation is completely self-contained and guarantees byte-stability, whereas integrating with the existing pipeline might uncover hidden sources of non-determinism.

### Confidence
High — the violation of the "do not rewrite the evaluation pipeline" constraint is explicit and unambiguous in the diff.

### What Would Change My Mind
If it were proven that the existing evaluation pipeline is so tightly coupled to the real Ollama network client that it is architecturally impossible to inject the mock LLM without rewriting the entire module, making a parallel pipeline the only viable technical path.

### Dissent Points
A dissenting engineer might argue that ensuring strict byte-stability is the primary goal, and reusing the main pipeline introduces too much risk of accidental drift (e.g., if a teammate adds a timestamp to a base metric class). They would argue the parallel, simplified pipeline in `reproduce.py` perfectly satisfies the spirit of a frozen reproducibility harness, even if it violates the letter of the instructions.
