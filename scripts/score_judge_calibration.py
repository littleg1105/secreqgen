"""Score the hand-labeled judge calibration sample.

Reads:
  docs/judge_calibration_sample.md     — filled by you with `Human verdict: relevant|partial|irrelevant`
  docs/judge_calibration_sample.json   — sidecar with llama3:8b's predictions

Computes:
  - 3x3 confusion matrix (rows = human, cols = LLM)
  - Per-class precision, recall, F1
  - Overall agreement, Cohen's κ (unweighted), Cohen's κ (linear-weighted),
    and Fleiss's overall κ if more than 2 raters are present (left as future work).

Output: stdout + docs/judge_calibration_results.md

Linear weighting is appropriate for ordinal labels (relevant > partial >
irrelevant). Unweighted κ punishes a relevant↔partial confusion as
severely as a relevant↔irrelevant one, which over-states judge error
for ordinal scales (Landis & Koch 1977; Cohen 1968). We report both.

Usage:
  cd secreqgen && PYTHONPATH=src .venv/bin/python \
    scripts/score_judge_calibration.py
"""

from __future__ import annotations

import json
import re
from pathlib import Path

DEFAULT_MD = Path("/Users/georgeg/Documents/Uni Docs/Thesis/coded_project/secreqgen/docs/judge_calibration_sample.md")
DEFAULT_JSON = Path("/Users/georgeg/Documents/Uni Docs/Thesis/coded_project/secreqgen/docs/judge_calibration_sample.json")
DEFAULT_OUT = Path("/Users/georgeg/Documents/Uni Docs/Thesis/coded_project/secreqgen/docs/judge_calibration_results.md")

LABELS = ["relevant", "partial", "irrelevant"]
LABEL_IDX = {l: i for i, l in enumerate(LABELS)}


def parse_worksheet(md: Path) -> dict[str, str]:
    """Extract {sample_id: human_verdict} from the filled worksheet."""
    text = md.read_text()
    out = {}
    # Each block: "## SXXX — ..." then later "**Human verdict**: `VERDICT`"
    blocks = re.split(r"^## (S\d{3}) —", text, flags=re.MULTILINE)
    # blocks[0] is the preamble; then alternates sample_id, body, sample_id, body...
    for i in range(1, len(blocks), 2):
        sid = blocks[i]
        body = blocks[i+1] if i+1 < len(blocks) else ""
        # Find the human verdict line; tolerate variations like "`relevant`" or "relevant"
        m = re.search(r"\*\*Human verdict\*\*:\s*`?([a-zA-Z_]+)`?", body)
        if m:
            v = m.group(1).strip().lower()
            if v in LABEL_IDX:
                out[sid] = v
            elif v == "____________" or v == "" or v.startswith("___"):
                pass  # not yet filled
            else:
                print(f"  {sid}: unrecognized verdict {v!r}")
    return out


def confusion_matrix(human: list[str], llm: list[str]) -> list[list[int]]:
    m = [[0]*3 for _ in range(3)]
    for h, l in zip(human, llm):
        m[LABEL_IDX[h]][LABEL_IDX[l]] += 1
    return m


def cohens_kappa(confusion: list[list[int]], weights: str = "unweighted") -> float:
    """Unweighted or linear-weighted Cohen's κ for a 3x3 ordinal confusion matrix."""
    n = sum(sum(row) for row in confusion)
    if n == 0:
        return 0.0
    row_marginals = [sum(row) for row in confusion]
    col_marginals = [sum(confusion[i][j] for i in range(3)) for j in range(3)]

    if weights == "unweighted":
        # Standard Cohen's κ
        po = sum(confusion[i][i] for i in range(3)) / n
        pe = sum(row_marginals[i] * col_marginals[i] for i in range(3)) / (n * n)
        return (po - pe) / (1 - pe) if pe < 1 else 1.0

    # Linear-weighted κ:
    # Disagreement weight w_ij = |i - j| / (k - 1); agreement w_ii = 0.
    w = [[abs(i - j) / 2.0 for j in range(3)] for i in range(3)]
    observed_disagreement = sum(w[i][j] * confusion[i][j] for i in range(3) for j in range(3)) / n
    expected_disagreement = sum(
        w[i][j] * row_marginals[i] * col_marginals[j] / n
        for i in range(3) for j in range(3)
    ) / n
    if expected_disagreement == 0:
        return 1.0
    return 1.0 - observed_disagreement / expected_disagreement


def per_class_prf(confusion: list[list[int]]) -> list[dict]:
    out = []
    for i, label in enumerate(LABELS):
        tp = confusion[i][i]
        fp = sum(confusion[r][i] for r in range(3)) - tp   # LLM said i, human said something else
        fn = sum(confusion[i][c] for c in range(3)) - tp   # human said i, LLM said something else
        precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0
        out.append({"label": label, "precision": precision, "recall": recall, "f1": f1, "support_human": tp + fn})
    return out


def interpret_kappa(k: float) -> str:
    """Landis & Koch 1977 interpretation. Note: thresholds are conventional, not statistical."""
    if k < 0:
        return "worse than chance"
    if k < 0.20:
        return "slight"
    if k < 0.40:
        return "fair"
    if k < 0.60:
        return "moderate"
    if k < 0.80:
        return "substantial"
    return "almost perfect"


def main():
    if not DEFAULT_MD.exists() or not DEFAULT_JSON.exists():
        print(f"Missing inputs. Run generate_judge_calibration_sample.py first.")
        print(f"  md: {DEFAULT_MD} {'exists' if DEFAULT_MD.exists() else 'MISSING'}")
        print(f"  json: {DEFAULT_JSON} {'exists' if DEFAULT_JSON.exists() else 'MISSING'}")
        return

    print(f"Loading sidecar (LLM verdicts) ← {DEFAULT_JSON}")
    sidecar = json.loads(DEFAULT_JSON.read_text())
    llm_by_id = {s["sample_id"]: s["llm_verdict"] for s in sidecar}

    print(f"Parsing human labels ← {DEFAULT_MD}")
    human_by_id = parse_worksheet(DEFAULT_MD)
    print(f"  human labels filled: {len(human_by_id)} / {len(sidecar)}")

    paired = [(sid, human_by_id[sid], llm_by_id[sid])
              for sid in human_by_id
              if sid in llm_by_id and llm_by_id[sid] in LABEL_IDX]
    n = len(paired)
    if n == 0:
        print("No filled labels. Fill in `Human verdict` lines and rerun.")
        return

    human = [h for _, h, _ in paired]
    llm = [l for _, _, l in paired]
    conf = confusion_matrix(human, llm)

    print(f"\nPaired observations: {n}")
    print("\nConfusion matrix (rows = human, cols = LLM):")
    print(f"  {'':>12} " + " ".join(f"{l:>12}" for l in LABELS) + "  row_total")
    for i, label in enumerate(LABELS):
        row_total = sum(conf[i])
        print(f"  {label:>12} " + " ".join(f"{conf[i][j]:>12d}" for j in range(3)) + f"  {row_total:>9d}")
    col_total = [sum(conf[i][j] for i in range(3)) for j in range(3)]
    print(f"  {'col_total':>12} " + " ".join(f"{c:>12d}" for c in col_total) + f"  {n:>9d}")

    k_uw = cohens_kappa(conf, weights="unweighted")
    k_lw = cohens_kappa(conf, weights="linear")
    print(f"\nCohen's κ (unweighted): {k_uw:.3f}  ({interpret_kappa(k_uw)})")
    print(f"Cohen's κ (linear):     {k_lw:.3f}  ({interpret_kappa(k_lw)})")

    prf = per_class_prf(conf)
    print(f"\nPer-class precision / recall / F1 (LLM as predictor of human label):")
    print(f"  {'label':>12} {'P':>6} {'R':>6} {'F1':>6}  support_human")
    for r in prf:
        print(f"  {r['label']:>12} {r['precision']:>6.2f} {r['recall']:>6.2f} {r['f1']:>6.2f}  {r['support_human']}")

    overall_agreement = sum(conf[i][i] for i in range(3)) / n
    print(f"\nRaw agreement (matches / total): {overall_agreement:.3f} ({sum(conf[i][i] for i in range(3))}/{n})")

    # Persist to docs/
    out_lines = [
        "# Judge Calibration Results",
        "",
        f"Sample size: {n} hand-labeled (story, chunk) pairs.",
        "Labels: `relevant`, `partial`, `irrelevant` (ordinal).",
        "Judge model: `llama3:8b` (Ollama, temperature=0.0).",
        "",
        "## Confusion matrix",
        "",
        "Rows = human verdict, columns = LLM verdict.",
        "",
        "| human \\ LLM | relevant | partial | irrelevant | row total |",
        "|---|---|---|---|---|",
    ]
    for i, label in enumerate(LABELS):
        row_total = sum(conf[i])
        out_lines.append(f"| **{label}** | {conf[i][0]} | {conf[i][1]} | {conf[i][2]} | {row_total} |")
    out_lines.append(f"| **col total** | {col_total[0]} | {col_total[1]} | {col_total[2]} | {n} |")
    out_lines.extend([
        "",
        "## Agreement statistics",
        "",
        f"- **Raw agreement** (matches / total): **{overall_agreement:.3f}** ({sum(conf[i][i] for i in range(3))}/{n})",
        f"- **Cohen's κ (unweighted)**: **{k_uw:.3f}** — {interpret_kappa(k_uw)} (Landis & Koch 1977)",
        f"- **Cohen's κ (linear-weighted)**: **{k_lw:.3f}** — {interpret_kappa(k_lw)}",
        "",
        "Linear weighting credits relevant↔partial confusions as half-disagreements ",
        "(appropriate for ordinal labels). Unweighted κ treats them as full ",
        "disagreements (more conservative). Report both; cite linear-weighted as ",
        "the primary defensibility statistic.",
        "",
        "## Per-class precision / recall / F1",
        "",
        "(LLM-as-predictor performance against human ground truth.)",
        "",
        "| label | precision | recall | F1 | support (human) |",
        "|---|---|---|---|---|",
    ])
    for r in prf:
        out_lines.append(f"| {r['label']} | {r['precision']:.2f} | {r['recall']:.2f} | {r['f1']:.2f} | {r['support_human']} |")
    out_lines.extend([
        "",
        "## Interpretation guide",
        "",
        "- κ ≥ 0.60 (substantial agreement): the judge can stand as the primary",
        "  metric source for Chapter 7 without footnote caveats beyond reporting κ.",
        "- 0.40 ≤ κ < 0.60 (moderate): use the judge but report κ in the methods",
        "  chapter and note that headline numbers should be read with that error",
        "  band in mind. Mention which class transitions are most error-prone.",
        "- κ < 0.40 (fair or worse): the judge is too noisy to defend in a thesis.",
        "  Either re-prompt the judge, switch to a stronger model (e.g. mistral-",
        "  small3.2:24b which is already in the model role table), or fall back to",
        "  hand-labeled ground truth for the small sample of stories where each",
        "  verdict matters most.",
    ])
    DEFAULT_OUT.write_text("\n".join(out_lines))
    print(f"\nWrote results → {DEFAULT_OUT}")


if __name__ == "__main__":
    main()
