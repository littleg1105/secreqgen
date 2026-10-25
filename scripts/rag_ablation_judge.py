"""Judge all (idea × project) cells from rag_ablation_study.py output.

Reads /tmp/rag_ablation_retrieval.json (produced by rag_ablation_study.py),
runs llama3:8b judge on every chunk across every cell, computes per-cell
P@k metrics, and emits both per-cell results and pivot tables for
analysis.

Output:
- /tmp/rag_ablation_judged.json   — full structured results
- /tmp/rag_ablation_pivot.csv     — flat CSV: idea, project, metric, value
- stdout: pivot tables (idea × project for each metric)
"""
from __future__ import annotations

import csv
import json
import os
import sys
import time
from pathlib import Path

from secreqgen.generation.llm_client import OllamaClient

# Reuse the judge prompt + parser from judge_retrieval_quality.py
sys.path.insert(0, str(Path(__file__).parent))
from judge_retrieval_quality import (
    JUDGE_SYSTEM_PROMPT, JUDGE_USER_PROMPT_TEMPLATE,
    parse_retrieval_context,
)


def judge_one_pair(client, story: dict, chunk: dict):
    """Returns (verdict, reason)."""
    ac = "\n".join(f"  - {x}" for x in story.get("acceptance_criteria", []))
    user_prompt = JUDGE_USER_PROMPT_TEMPLATE.format(
        story_id=story.get("id", "?"),
        story_title=story.get("title", "?"),
        story_text=story.get("story", "?"),
        acceptance_criteria=ac or "  (none)",
        chunk_source=chunk["source"][:120],
        chunk_score=chunk["relevance"],
        chunk_content=chunk["content"][:1500],
    )
    try:
        resp = client.generate(prompt=user_prompt, system_prompt=JUDGE_SYSTEM_PROMPT, format="json")
        raw = resp.content.strip()
        obj = json.loads(raw)
        v = str(obj.get("verdict", "")).strip().lower()
        if v not in ("relevant", "partial", "irrelevant"):
            return "parse_error", f"bad verdict {v!r}"
        return v, str(obj.get("reason", ""))
    except Exception as e:
        return "parse_error", f"{e}"


import math

# Exponential-gain mapping for nDCG (Järvelin & Kekäläinen 2002, BEIR convention):
# gain = 2^rel - 1, where rel ∈ {0,1,2} for {irrelevant, partial, relevant}.
_GAIN = {"relevant": 3, "partial": 1, "irrelevant": 0, "parse_error": 0}


def _dcg(gains: list[int]) -> float:
    return sum(g / math.log2(i + 2) for i, g in enumerate(gains))


def ndcg_at_k(verdicts: list[dict], k: int = 5) -> float:
    """nDCG@k over a ranked list of chunk verdicts. Returns 0 when no relevance."""
    gains = [_GAIN.get(cv["verdict"], 0) for cv in verdicts[:k]]
    if not gains:
        return 0.0
    ideal = sorted(gains, reverse=True)
    idcg = _dcg(ideal)
    return _dcg(gains) / idcg if idcg > 0 else 0.0


def metrics_for_cell(cell_results: list[dict]) -> dict:
    """Compute precision@k + nDCG@5 stats over per-story results."""
    n_stories = len(cell_results)
    if n_stories == 0:
        return {"n_stories": 0, "mean_p_strict": 0.0, "mean_p_lenient": 0.0,
                "mean_ndcg_at_5": 0.0, "n_top1_relevant": 0,
                "total_chunks": 0, "n_relevant": 0, "n_partial": 0, "n_irrelevant": 0, "n_parse_error": 0}
    strict, lenient, ndcg_sum, n_top1 = 0.0, 0.0, 0.0, 0
    n_rel = n_par = n_irr = n_err = n_chk = 0
    for s in cell_results:
        cvs = s.get("chunk_verdicts", [])
        n_chk += len(cvs)
        rel = sum(1 for cv in cvs if cv["verdict"] == "relevant")
        par = sum(1 for cv in cvs if cv["verdict"] == "partial")
        irr = sum(1 for cv in cvs if cv["verdict"] == "irrelevant")
        err = sum(1 for cv in cvs if cv["verdict"] == "parse_error")
        n_rel += rel; n_par += par; n_irr += irr; n_err += err
        if cvs:
            strict += rel / len(cvs)
            lenient += (rel + 0.5 * par) / len(cvs)
            ndcg_sum += ndcg_at_k(cvs, k=5)
            if cvs[0]["verdict"] == "relevant":
                n_top1 += 1
    return {
        "n_stories": n_stories,
        "mean_p_strict": strict / n_stories,
        "mean_p_lenient": lenient / n_stories,
        "mean_ndcg_at_5": ndcg_sum / n_stories,
        "n_top1_relevant": n_top1,
        "total_chunks": n_chk,
        "n_relevant": n_rel,
        "n_partial": n_par,
        "n_irrelevant": n_irr,
        "n_parse_error": n_err,
    }


def main():
    in_path = Path(os.environ.get("ABLATION_INPUT", "/tmp/rag_ablation_retrieval.json"))
    out_json = Path(os.environ.get("ABLATION_JUDGED", "/tmp/rag_ablation_judged.json"))
    out_csv = Path(os.environ.get("ABLATION_PIVOT_CSV", "/tmp/rag_ablation_pivot.csv"))
    judge_model = os.environ.get("ABLATION_JUDGE", "llama3:8b")

    cells = json.loads(in_path.read_text())
    print(f"Loaded {len(cells)} (idea × project) cells from {in_path}")
    print(f"Judge model: {judge_model}")

    client = OllamaClient(model_name=judge_model, temperature=0.0, max_tokens=256, timeout=120.0)

    judged_cells = []
    t0 = time.perf_counter()
    for cell_i, cell in enumerate(cells):
        if "error" in cell:
            judged_cells.append(cell)
            continue
        cell_id = f"idea{cell['idea_id']}/{cell['project']}"
        per_story_judged = []
        story_chunks_total = 0
        cell_t0 = time.perf_counter()
        for s in cell["per_story_results"]:
            ctx = (s.get("rag_result") or {}).get("retrieval_context") or ""
            story = (s.get("baseline_result") or {}).get("user_story") or {
                "id": s["story_id"], "title": s["story_title"]
            }
            chunks = parse_retrieval_context(ctx)
            story_chunks_total += len(chunks)
            verdicts = []
            for ch in chunks:
                v, r = judge_one_pair(client, story, ch)
                verdicts.append({
                    "chunk_index": ch["index"], "source": ch["source"][:160],
                    "relevance_score": ch["relevance"], "verdict": v, "reason": r,
                })
            per_story_judged.append({
                "story_id": s["story_id"], "story_title": s["story_title"],
                "n_chunks": len(chunks), "chunk_verdicts": verdicts,
            })
        m = metrics_for_cell(per_story_judged)
        cell_elapsed = time.perf_counter() - cell_t0
        print(f"  [{cell_i + 1:>2}/{len(cells)}] {cell_id:<35}  chunks={story_chunks_total:>3}  "
              f"P@k={m['mean_p_strict']:.2f}/{m['mean_p_lenient']:.2f}  "
              f"nDCG@5={m['mean_ndcg_at_5']:.2f}  "
              f"top1={m['n_top1_relevant']}/{m['n_stories']}  ({cell_elapsed:.1f}s)")
        judged_cells.append({
            "idea_id": cell["idea_id"], "idea_name": cell["idea_name"],
            "project": cell["project"], "chunk_count": cell.get("chunk_count", 0),
            "per_story_judged": per_story_judged, "metrics": m,
        })

    out_json.write_text(json.dumps(judged_cells, indent=2))
    print(f"\nWrote {out_json}")

    # Build a flat CSV: idea_id, idea_name, project, metric, value
    rows = [("idea_id", "idea_name", "project", "metric", "value")]
    for c in judged_cells:
        if "metrics" not in c:
            continue
        for k, v in c["metrics"].items():
            rows.append((c["idea_id"], c["idea_name"], c["project"], k, v))
    with out_csv.open("w", newline="") as f:
        csv.writer(f).writerows(rows)
    print(f"Wrote {out_csv}")
    print(f"Total wall time: {(time.perf_counter() - t0) / 60:.1f} min")

    # Print pivot tables: idea × project for each key metric
    print()
    metric_names = [("mean_p_strict", "P@k strict"), ("mean_p_lenient", "P@k lenient"),
                    ("mean_ndcg_at_5", "nDCG@5"),
                    ("n_top1_relevant", "top-1 relevant")]
    projects = sorted({c["project"] for c in judged_cells if "metrics" in c})
    ideas = sorted({(c["idea_id"], c["idea_name"]) for c in judged_cells if "metrics" in c})
    cell_lookup = {(c["idea_id"], c["project"]): c["metrics"] for c in judged_cells if "metrics" in c}

    for key, label in metric_names:
        print(f"=== {label} (idea × project) ===")
        print(f"  {'idea':<35}  " + "  ".join(f"{p:<10}" for p in projects) + "    mean")
        print("-" * 100)
        for iid, iname in ideas:
            vals = []
            for p in projects:
                m = cell_lookup.get((iid, p))
                if m is None:
                    vals.append(float('nan'))
                else:
                    vals.append(m[key])
            valid = [v for v in vals if v == v]
            mean = sum(valid) / len(valid) if valid else 0.0
            row = "  ".join(
                f"{v:<10.2f}" if isinstance(v, float) and v == v else f"{int(v):<10}" if isinstance(v, int) else f"{'-':<10}"
                for v in vals
            )
            print(f"  {iid:>2} {iname[:30]:<32}  {row}  {mean:.2f}")
        print()


if __name__ == "__main__":
    main()
