"""Study 2 analysis — documentation-attributable citation of fact-sheet identifiers.

Reads the records written by scripts/run_study2.py and the fact sheets in
real_test_cases/study2/<project>/stories.json and computes, per record:

  cited        fact-sheet identifiers found in the generated requirements
  prompt_given identifiers present in the NON-documentation part of the prompt
               (system prompt + template + story + system context) — subtracted,
               because the model may simply be echoing them (the §13 lesson)
  attributable cited − prompt_given
  supported    attributable identifiers that occur in the documentation text the
               model was actually given (B: frozen passages, C: passages + token
               list, D: corpus); for arm A nothing is supplied, so every
               attributable identifier is parametric (memorised or guessed)
  unsupported  attributable − supported (for B/C/D: not in supplied docs)

Primary outcome (pre-registered, notebook §14): per requirement, 1 if it cites
≥1 attributable identifier, else 0; averaged per (model, story, arm, run), then
over runs → one value per (model, story, arm). Primary contrast C − B, paired
by story within model; secondary B − A and D − B. Wilcoxon signed-rank when
scipy is available, exact sign test otherwise; Cohen's d_z alongside.

Usage:
  PYTHONPATH=src .venv/bin/python scripts/analyze_study2.py [--projects mastodon,keycloak,bitwarden]
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
from collections import defaultdict
from pathlib import Path

STUDY2 = Path("/Users/georgeg/Documents/Uni Docs/Thesis/coded_project/real_test_cases/study2")
REQ_FIELDS = ("title", "description", "rationale", "implementation_notes")


def req_text(req: dict) -> str:
    parts = [str(req.get(f) or "") for f in REQ_FIELDS]
    parts += [str(x) for x in (req.get("acceptance_criteria") or [])]
    parts += [str(x) for x in (req.get("source_references") or [])]
    return "\n".join(parts)


def found(text: str, identifiers: list[str]) -> set[str]:
    return {t for t in identifiers if t and t in text}


def load_project(project: str) -> tuple[dict, dict[str, dict]]:
    data = json.loads((STUDY2 / project / "stories.json").read_text())
    sheets = {s["id"]: s["fact_sheet"] for s in data["stories"]}
    return data, sheets


def doc_context_for(rec: dict, result_dir: Path, frozen: dict, corpus_cache: dict) -> str:
    ref = rec.get("doc_context_ref")
    if ref is None:
        return ""
    if ref.startswith("frozen"):
        ctx = frozen.get(rec["story_id"], {}).get("context", "")
        if ref == "frozen+tokens":
            ctx = json.dumps(rec.get("extracted_tokens", [])) + "\n" + ctx
        return ctx
    if ref == "corpus_D.txt":
        if result_dir not in corpus_cache:
            p = result_dir / "corpus_D.txt"
            corpus_cache[result_dir] = p.read_text(errors="ignore") if p.exists() else ""
        return corpus_cache[result_dir]
    return ""


def paired_test(diffs: list[float]) -> dict:
    nz = [d for d in diffs if abs(d) > 1e-12]
    out = {"n": len(diffs), "n_nonzero": len(nz), "mean_diff": statistics.fmean(diffs) if diffs else float("nan")}
    if len(diffs) >= 2 and statistics.pstdev(diffs) > 0:
        sd = statistics.stdev(diffs)
        out["d_z"] = out["mean_diff"] / sd if sd > 0 else float("nan")
    else:
        out["d_z"] = float("nan")
    try:
        from scipy.stats import wilcoxon  # type: ignore
        if len(nz) >= 1:
            stat, p = wilcoxon(nz, zero_method="wilcox", alternative="two-sided")
            out["test"] = "wilcoxon"
            out["p"] = float(p)
        else:
            out["test"] = "wilcoxon"
            out["p"] = float("nan")
    except Exception:
        # exact two-sided sign test as a dependency-free fallback
        pos = sum(1 for d in nz if d > 0)
        n = len(nz)
        if n == 0:
            out["test"] = "sign"
            out["p"] = float("nan")
        else:
            k = min(pos, n - pos)
            cdf = sum(math.comb(n, j) for j in range(0, k + 1)) / 2 ** n
            out["test"] = "sign"
            out["p"] = min(1.0, 2 * cdf)
    return out


def analyze(project: str) -> dict:
    data, sheets = load_project(project)
    results_root = STUDY2 / project / "results"
    if not results_root.exists():
        return {"project": project, "error": "no results"}
    corpus_cache: dict = {}
    per_rec = []
    for result_dir in sorted(p for p in results_root.iterdir() if p.is_dir()):
        records_path = result_dir / "records.jsonl"
        if not records_path.exists():
            continue
        frozen_path = result_dir / "retrieval_frozen.json"
        frozen = json.loads(frozen_path.read_text()) if frozen_path.exists() else {}
        for line in records_path.read_text().splitlines():
            if not line.strip():
                continue
            rec = json.loads(line)
            if rec.get("error"):
                per_rec.append({**{k: rec[k] for k in ("model", "story_id", "arm", "run")}, "error": rec["error"]})
                continue
            sheet = sheets[rec["story_id"]]
            ids = sheet["identifiers"]
            prompt_given = found(rec.get("prompt_system", "") + "\n" + rec.get("prompt_user_non_doc", ""), ids)
            doc_ctx = doc_context_for(rec, result_dir, frozen, corpus_cache)
            in_docs = found(doc_ctx, ids) if doc_ctx else set()
            reqs = rec.get("requirements", [])
            cited_any = []
            all_cited, all_supported, all_unsupported = set(), set(), set()
            for req in reqs:
                cited = found(req_text(req), ids)
                attributable = cited - prompt_given
                supported = attributable & in_docs
                unsupported = attributable - in_docs
                cited_any.append(1 if attributable else 0)
                all_cited |= cited
                all_supported |= supported
                all_unsupported |= unsupported
            per_rec.append({
                "model": rec["model"], "story_id": rec["story_id"], "arm": rec["arm"], "run": rec["run"],
                "n_requirements": len(reqs),
                "frac_reqs_citing": (sum(cited_any) / len(cited_any)) if cited_any else 0.0,
                "distinct_attributable": len(all_supported | all_unsupported),
                "distinct_supported": len(all_supported),
                "distinct_unsupported": len(all_unsupported),
                "prompt_given": sorted(prompt_given),
                "sheet_size": len(ids), "docs_cover": len(in_docs),
                "prompt_tokens": rec.get("prompt_tokens"), "completion_tokens": rec.get("completion_tokens"),
                "latency_ms": rec.get("latency_ms"),
            })

    # aggregate: (model, story, arm) -> mean over runs
    cell = defaultdict(list)
    for r in per_rec:
        if "error" in r:
            continue
        cell[(r["model"], r["story_id"], r["arm"])].append(r)
    summary = defaultdict(dict)
    for (model, sid, arm), rs in cell.items():
        summary[(model, arm)][sid] = {
            "frac_reqs_citing": statistics.fmean(x["frac_reqs_citing"] for x in rs),
            "distinct_supported": statistics.fmean(x["distinct_supported"] for x in rs),
            "distinct_unsupported": statistics.fmean(x["distinct_unsupported"] for x in rs),
            "prompt_tokens": statistics.fmean(x["prompt_tokens"] or 0 for x in rs),
            "latency_s": statistics.fmean((x["latency_ms"] or 0) / 1000 for x in rs),
            "n_runs": len(rs),
        }

    report = {"project": project, "n_records": len(per_rec),
              "n_errors": sum(1 for r in per_rec if "error" in r), "models": {}}
    models = sorted({m for (m, _a) in summary})
    for model in models:
        arms = sorted({a for (m, a) in summary if m == model})
        table = {}
        for arm in arms:
            cells = summary[(model, arm)]
            table[arm] = {
                "stories": len(cells),
                "frac_reqs_citing": statistics.fmean(c["frac_reqs_citing"] for c in cells.values()),
                "distinct_supported": statistics.fmean(c["distinct_supported"] for c in cells.values()),
                "distinct_unsupported": statistics.fmean(c["distinct_unsupported"] for c in cells.values()),
                "prompt_tokens": statistics.fmean(c["prompt_tokens"] for c in cells.values()),
                "latency_s": statistics.fmean(c["latency_s"] for c in cells.values()),
            }
        contrasts = {}
        for name, (x, y) in {"C-B (primary)": ("C", "B"), "B-A": ("B", "A"), "D-B": ("D", "B")}.items():
            if x in arms and y in arms:
                common = sorted(set(summary[(model, x)]) & set(summary[(model, y)]))
                diffs = [summary[(model, x)][s]["frac_reqs_citing"] - summary[(model, y)][s]["frac_reqs_citing"] for s in common]
                contrasts[name] = {**paired_test(diffs), "stories": common}
        report["models"][model] = {"arms": table, "contrasts": contrasts,
                                   "per_story": {arm: summary[(model, arm)] for arm in arms}}
    return report


def print_report(rep: dict) -> None:
    print(f"\n### {rep['project']}: {rep.get('n_records', 0)} records, {rep.get('n_errors', 0)} errors")
    if "error" in rep:
        print("  ", rep["error"])
        return
    for model, m in rep["models"].items():
        print(f"\n  model {model}")
        print(f"  {'arm':4s} {'stories':>7s} {'reqs citing':>12s} {'supported':>10s} {'unsupported':>12s} {'prompt tok':>11s} {'s/story':>8s}")
        for arm, t in m["arms"].items():
            print(f"  {arm:4s} {t['stories']:7d} {t['frac_reqs_citing']:12.2f} {t['distinct_supported']:10.2f} {t['distinct_unsupported']:12.2f} {t['prompt_tokens']:11.0f} {t['latency_s']:8.0f}")
        for name, c in m["contrasts"].items():
            p = c.get("p")
            ptxt = "n/a" if p is None or (isinstance(p, float) and math.isnan(p)) else f"{p:.3f}"
            print(f"  {name:14s} mean diff {c['mean_diff']:+.3f}  d_z {c['d_z']:+.2f}  {c['test']} p={ptxt}  (n={c['n']}, nonzero={c['n_nonzero']})")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--projects", default="mastodon,keycloak,bitwarden")
    ap.add_argument("--json", default=None, help="write the full report here")
    args = ap.parse_args()
    reports = [analyze(p.strip()) for p in args.projects.split(",") if p.strip()]
    for rep in reports:
        print_report(rep)
    if args.json:
        Path(args.json).write_text(json.dumps(reports, indent=1, default=str))
        print(f"\nwritten {args.json}")


if __name__ == "__main__":
    main()
