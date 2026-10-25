"""LLM-as-judge for retrieval quality (2026-05-19 audit follow-up).

Reads a raw_runs/run_NNN.json produced by the Option B patch, splits the
RAG `retrieval_context` back into individual chunks, and asks a judge LLM
whether each chunk is relevant / partial / irrelevant to the user story
it was retrieved for. Computes per-story precision@k and a per-cell
aggregate, so we can compare retrieval-pipeline variants quantitatively
without hand-labeling thousands of (story, chunk) pairs.

The judge model and the generator model SHOULD be from different
families (avoid same-model bias). The cross-family pilot generated with
Qwen / Gemma / Mistral; this script defaults to llama3:8b (Meta family)
to keep judge bias away from any of them.

Usage:
    PYTHONPATH=src python scripts/judge_retrieval_quality.py \\
        real_test_cases/mastodon/results_compare_runs_mastodon_enriched_n2/run_comparison_*/raw_runs/run_001.json

Optional flags:
    --judge llama3:8b           # judge model (must be available in Ollama)
    --max-stories 8             # cap for quick smoke runs
    --output report.json        # write structured report alongside stdout
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

from secreqgen.generation.llm_client import OllamaClient


JUDGE_SYSTEM_PROMPT = """You are an expert software security reviewer evaluating whether a retrieved documentation chunk is useful for an engineer writing security requirements for a user story.

You will receive a USER STORY (with acceptance criteria) and one RETRIEVED CHUNK from a documentation corpus. Your job is to classify the chunk's usefulness for generating security requirements about that user story.

Output STRICTLY a single JSON object with this exact shape:
{"verdict": "relevant" | "partial" | "irrelevant", "reason": "<one short sentence>"}

Definitions:
- "relevant": the chunk directly addresses security-relevant content for this user story (e.g., authentication mechanism, validation rule, encryption requirement, attack mitigation, configuration that affects security posture). An engineer reading this chunk could cite it when writing requirements.
- "partial": the chunk is on-topic but mostly descriptive / general (e.g., UI workflow, marketing copy, metadata, configuration unrelated to security, tangential parameters). It may contain a useful phrase but isn't sufficient on its own.
- "irrelevant": the chunk is about a different topic, is empty/metadata-only (e.g., YAML front-matter with title and description fields), or would not help write a security requirement.

Be strict. Front-matter blocks containing only `title: X`, `description: Y`, `menu: ...` are IRRELEVANT no matter how security-sounding the title is — they have no substantive content.

Output ONLY the JSON object. No prose, no markdown fences."""


JUDGE_USER_PROMPT_TEMPLATE = """USER STORY [{story_id}] {story_title}
Story: {story_text}
Acceptance criteria:
{acceptance_criteria}

RETRIEVED CHUNK (from {chunk_source}, relevance score {chunk_score:.2f}):
{chunk_content}

Classify this chunk's usefulness for generating security requirements about the user story."""


# ─────────────────────────────────────────────────────────────────────
# Data shapes
# ─────────────────────────────────────────────────────────────────────


@dataclass
class ChunkVerdict:
    chunk_index: int
    source: str
    relevance_score: float
    content_preview: str
    verdict: str  # "relevant" | "partial" | "irrelevant" | "parse_error"
    reason: str


@dataclass
class StoryReport:
    story_id: str
    story_title: str
    n_chunks: int
    chunk_verdicts: list[ChunkVerdict] = field(default_factory=list)

    @property
    def n_relevant(self) -> int:
        return sum(1 for v in self.chunk_verdicts if v.verdict == "relevant")

    @property
    def n_partial(self) -> int:
        return sum(1 for v in self.chunk_verdicts if v.verdict == "partial")

    @property
    def n_irrelevant(self) -> int:
        return sum(1 for v in self.chunk_verdicts if v.verdict == "irrelevant")

    @property
    def precision_at_k(self) -> float:
        """Strict precision: relevant / total."""
        if not self.chunk_verdicts:
            return 0.0
        return self.n_relevant / len(self.chunk_verdicts)

    @property
    def precision_at_k_lenient(self) -> float:
        """Lenient precision: (relevant + 0.5*partial) / total."""
        if not self.chunk_verdicts:
            return 0.0
        return (self.n_relevant + 0.5 * self.n_partial) / len(self.chunk_verdicts)

    @property
    def top1_relevant(self) -> bool:
        return bool(self.chunk_verdicts) and self.chunk_verdicts[0].verdict == "relevant"


# ─────────────────────────────────────────────────────────────────────
# Chunk parsing — split retrieval_context back into individual chunks
# ─────────────────────────────────────────────────────────────────────

# Format observed in raw_runs/:
#   ### [1] path/to/file.md > Section > Subsection (Relevance: 0.58)
#   Section: ... > ...
#   [body content]
#
#   ### [2] ...
CHUNK_HEADER_RE = re.compile(
    r"^### \[(\d+)\]\s+(.+?)\s+\(Relevance:\s+(\d+\.\d+)\)\s*$",
    re.MULTILINE,
)


def parse_retrieval_context(ctx: str) -> list[dict]:
    """Split a retrieval_context string back into individual chunks.

    Returns a list of {index, source, relevance, content}.
    Returns [] for the whiff fallback string or empty contexts.
    """
    if not ctx or "No relevant security documentation found" in ctx:
        return []

    headers = list(CHUNK_HEADER_RE.finditer(ctx))
    if not headers:
        return []

    out = []
    for i, m in enumerate(headers):
        idx = int(m.group(1))
        source = m.group(2)
        score = float(m.group(3))
        start = m.end()
        end = headers[i + 1].start() if i + 1 < len(headers) else len(ctx)
        body = ctx[start:end].strip()
        out.append({
            "index": idx,
            "source": source,
            "relevance": score,
            "content": body,
        })
    return out


# ─────────────────────────────────────────────────────────────────────
# Judge
# ─────────────────────────────────────────────────────────────────────


class RetrievalQualityJudge:
    """LLM-as-judge for per-chunk retrieval quality.

    Uses a small / fast LLM (default llama3:8b) to classify each retrieved
    chunk as relevant / partial / irrelevant. Different model family from
    the cross-family generators (Qwen / Gemma / Mistral) to reduce
    judge-generator correlation.
    """

    def __init__(
        self,
        judge_model: str = "llama3:8b",
        verbose: bool = False,
    ) -> None:
        self.judge_model = judge_model
        self.verbose = verbose
        # Use the patched OllamaClient that reads OLLAMA_HOST from env
        self.client = OllamaClient(
            model_name=judge_model,
            temperature=0.0,    # deterministic judging
            max_tokens=256,     # JSON verdict is tiny
            timeout=120.0,
        )

    def judge_chunk(
        self,
        story: dict,
        chunk: dict,
    ) -> tuple[str, str]:
        """Return (verdict, reason) for a single chunk."""
        ac_lines = "\n".join(f"  - {ac}" for ac in story.get("acceptance_criteria", []))
        user_prompt = JUDGE_USER_PROMPT_TEMPLATE.format(
            story_id=story.get("id", "?"),
            story_title=story.get("title", "?"),
            story_text=story.get("story", "?"),
            acceptance_criteria=ac_lines or "  (none)",
            chunk_source=chunk["source"][:120],
            chunk_score=chunk["relevance"],
            chunk_content=chunk["content"][:1500],   # cap chunk text to keep prompts small
        )

        try:
            resp = self.client.generate(
                prompt=user_prompt,
                system_prompt=JUDGE_SYSTEM_PROMPT,
                format="json",
            )
            raw = resp.content.strip()
        except Exception as e:
            if self.verbose:
                print(f"    ! judge call failed: {e}", file=sys.stderr)
            return "parse_error", f"call failed: {e}"

        # Parse JSON response
        try:
            obj = json.loads(raw)
            verdict = str(obj.get("verdict", "")).strip().lower()
            reason = str(obj.get("reason", "")).strip()
            if verdict not in ("relevant", "partial", "irrelevant"):
                return "parse_error", f"unexpected verdict={verdict!r}; raw={raw[:200]}"
            return verdict, reason
        except json.JSONDecodeError as e:
            if self.verbose:
                print(f"    ! JSON parse error on: {raw[:200]}", file=sys.stderr)
            return "parse_error", f"json parse error: {e}; raw={raw[:200]}"


# ─────────────────────────────────────────────────────────────────────
# Main flow — judge a raw_runs file
# ─────────────────────────────────────────────────────────────────────


def judge_raw_runs_file(
    raw_runs_path: Path,
    judge_model: str = "llama3:8b",
    max_stories: Optional[int] = None,
    verbose: bool = True,
) -> dict:
    """Run the judge across all stories in a raw_runs/run_NNN.json file."""
    data = json.loads(raw_runs_path.read_text())
    stories = data["per_story_results"]
    if max_stories:
        stories = stories[:max_stories]

    judge = RetrievalQualityJudge(judge_model=judge_model, verbose=verbose)

    if verbose:
        print(f"Judge model: {judge_model}")
        print(f"File:        {raw_runs_path}")
        print(f"Stories:     {len(stories)}")
        print()

    reports: list[StoryReport] = []
    start = time.perf_counter()

    for story_eval in stories:
        # Story metadata for the prompt; rebuild from the per-story shape.
        # Note: user_story is nested inside baseline_result and rag_result
        # (it's the same object on both sides).
        us = (story_eval.get("baseline_result") or {}).get("user_story") or {}
        story_meta = {
            "id": story_eval["story_id"],
            "title": story_eval["story_title"],
            "story": us.get("story", ""),
            "acceptance_criteria": us.get("acceptance_criteria", []),
        }

        ctx = (story_eval.get("rag_result") or {}).get("retrieval_context") or ""
        chunks = parse_retrieval_context(ctx)

        if verbose:
            print(f"[{story_meta['id']}] {story_meta['title']}")
            print(f"  chunks retrieved: {len(chunks)}")

        report = StoryReport(
            story_id=story_meta["id"],
            story_title=story_meta["title"],
            n_chunks=len(chunks),
        )

        for ch in chunks:
            t0 = time.perf_counter()
            verdict, reason = judge.judge_chunk(story_meta, ch)
            dt = time.perf_counter() - t0
            cv = ChunkVerdict(
                chunk_index=ch["index"],
                source=ch["source"][:160],
                relevance_score=ch["relevance"],
                content_preview=ch["content"][:120].replace("\n", " "),
                verdict=verdict,
                reason=reason,
            )
            report.chunk_verdicts.append(cv)
            if verbose:
                badge = {"relevant": "[REL]", "partial": "[PART]", "irrelevant": "[IRR]",
                         "parse_error": "[ERR]"}.get(verdict, "[??]")
                print(f"  {badge:<7} #{cv.chunk_index} sim={cv.relevance_score:.2f} "
                      f"({dt:.1f}s)  {cv.source[:80]}")
                if reason and verbose:
                    print(f"          reason: {reason[:120]}")

        if verbose:
            print(f"  → precision@k = {report.precision_at_k:.2f}  "
                  f"(lenient: {report.precision_at_k_lenient:.2f})  "
                  f"top1_relevant: {report.top1_relevant}")
            print()

        reports.append(report)

    total_dt = time.perf_counter() - start

    # Aggregate
    n_stories = len(reports)
    total_chunks = sum(r.n_chunks for r in reports)
    total_rel = sum(r.n_relevant for r in reports)
    total_part = sum(r.n_partial for r in reports)
    total_irr = sum(r.n_irrelevant for r in reports)
    mean_p_at_k = sum(r.precision_at_k for r in reports) / n_stories if n_stories else 0.0
    mean_p_at_k_lenient = sum(r.precision_at_k_lenient for r in reports) / n_stories if n_stories else 0.0
    n_top1_rel = sum(1 for r in reports if r.top1_relevant)

    summary = {
        "file": str(raw_runs_path),
        "judge_model": judge_model,
        "n_stories": n_stories,
        "total_chunks": total_chunks,
        "n_relevant": total_rel,
        "n_partial": total_part,
        "n_irrelevant": total_irr,
        "n_parse_error": total_chunks - total_rel - total_part - total_irr,
        "mean_precision_at_k": mean_p_at_k,
        "mean_precision_at_k_lenient": mean_p_at_k_lenient,
        "n_stories_with_top1_relevant": n_top1_rel,
        "elapsed_sec": total_dt,
        "stories": [asdict(r) for r in reports],
    }

    if verbose:
        print("─" * 60)
        print(f"SUMMARY ({judge_model})")
        print(f"  stories evaluated:        {n_stories}")
        print(f"  total chunks:             {total_chunks}")
        print(f"  relevant:                 {total_rel}")
        print(f"  partial:                  {total_part}")
        print(f"  irrelevant:               {total_irr}")
        print(f"  parse errors:             {total_chunks - total_rel - total_part - total_irr}")
        print(f"  mean precision@k strict:  {mean_p_at_k:.3f}")
        print(f"  mean precision@k lenient: {mean_p_at_k_lenient:.3f}")
        print(f"  top-1 relevant:           {n_top1_rel}/{n_stories}")
        print(f"  elapsed:                  {total_dt:.1f}s")

    return summary


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("raw_runs_file", type=Path)
    p.add_argument("--judge", default="llama3:8b",
                   help="Ollama model to use as judge (default: llama3:8b)")
    p.add_argument("--max-stories", type=int, default=None,
                   help="Cap to first N stories for fast smoke runs")
    p.add_argument("--output", type=Path, default=None,
                   help="Write structured JSON summary to this path")
    p.add_argument("--quiet", action="store_true",
                   help="Suppress per-chunk progress output")
    args = p.parse_args()

    if not args.raw_runs_file.exists():
        print(f"Error: raw_runs file not found: {args.raw_runs_file}", file=sys.stderr)
        return 1

    summary = judge_raw_runs_file(
        args.raw_runs_file,
        judge_model=args.judge,
        max_stories=args.max_stories,
        verbose=not args.quiet,
    )

    if args.output:
        args.output.write_text(json.dumps(summary, indent=2))
        print(f"\nWrote {args.output}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
