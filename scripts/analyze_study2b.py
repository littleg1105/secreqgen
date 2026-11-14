"""Study 2b analysis: the 2 by 2 table of documents and instructions.

Usage (from coded_project/secreqgen), only after the human label file of the
review is locked:
    PYTHONPATH=src .venv/bin/python scripts/analyze_study2b.py

It writes analysis_s2b.json and analysis_s2b.md into real_test_cases/study2b/
and prints only "written:" and the two paths. It never writes under
real_test_cases/study2/.

The method is fixed in thesis/analysis_lock.md (sections 3, 4 and 8) and in lab
notebook section 16:

  Outcome (lock section 4). Each record is scored by the registered function
      analyze() of scripts/analyze_study2.py. The registered arms A and B are
      read from the registered records. For a new arm, the non-documentation
      prompt is the exact user prompt that was sent with the exact context
      string removed. The record value is the share of its requirements that
      cite an attributable fact-sheet name; a story value is the mean over its
      successful runs.
  Regression. Before it reads a new record, the script scores the registered
      arms A and B through its own path. It stops when one value differs from
      analysis_2026-11-02.json.
  Tests (lock 8.2). For each model, 15 stories: K1 = N1 minus A, K2 = B minus N1.
      Exact Wilcoxon test of thesis/numbers/stats.py. A contrast passes when
      its mean is above zero and p is below 0.0125.
  Share (lock 8.3). S = mean K1 / mean registered B minus A, for each model.
      Bands: 0.70 or more; 0.40 to below 0.70; below 0.40.
  Descriptive (lock 8.4). I0 minus A, N0 minus A, N1 minus N0. No test.
  Drift (lock 8.5). Repeat B against B above 0.08, or repeat A against A
      above 0.05, makes the contrasts of that model "indicative".
  Lock 8.6 asks the note to fix three things. They are marked "LOCK 8.6" below.

Statistics come only from thesis/numbers/stats.py (loaded by path).
"""

from __future__ import annotations

import sys

sys.dont_write_bytecode = True  # importing the registered scripts must not write files next to them

import argparse  # noqa: E402
import hashlib  # noqa: E402
import importlib.util  # noqa: E402
import json  # noqa: E402
import math  # noqa: E402
import os  # noqa: E402
import shutil  # noqa: E402
import tempfile  # noqa: E402
from datetime import datetime, timezone  # noqa: E402
from pathlib import Path  # noqa: E402

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

import analyze_study2 as registered  # noqa: E402  (the registered outcome; its main() runs only as __main__)

THESIS = SCRIPTS.parents[2]
STATS_PATH = THESIS / "thesis" / "numbers" / "stats.py"
STUDY2 = registered.STUDY2
STUDY2B = STUDY2.parent / "study2b"
REGISTERED_ANALYSIS = STUDY2 / "analysis_2026-11-02.json"
REVIEW_V2 = STUDY2 / "review_v2"
JSON_NAME = "analysis_s2b.json"
MD_NAME = "analysis_s2b.md"

PROJECTS = ("mastodon", "keycloak", "bitwarden")
MODELS = ("qwen3.8:27b", "gemma4:26b")
REGISTERED_ARMS = ("A", "B")
# Runs of the design for each new arm, in the order of the night. A unit test
# checks that this equals scripts/run_study2b.py (ARMS).
NEW_ARMS = {"N1": 3, "B2": 1, "A2": 1, "I0": 3, "N0": 3}
CONTEXT_OF_ARM = {"N1": "frozen", "B2": "frozen", "A2": None, "I0": "fixed_line", "N0": "frozen"}
FIXED_LINE = "No project documentation was retrieved for this story."
MAX_ATTEMPTS = 2  # one retry, as registered (lock 4.7)

ALPHA = 0.0125  # four tests (lock 8.2)
BAND_HIGH = 0.70
BAND_LOW = 0.40
I0_LIMIT = 0.05  # lock 8.4
DRIFT_LIMIT = {"B2": 0.08, "A2": 0.05}  # lock 8.5
BASE_OF_REPEAT = {"B2": "B", "A2": "A"}

BAND_TEXT = {"high": "0.70 or more", "mid": "0.40 to below 0.70", "low": "below 0.40", None: "not defined"}
SENT_DOCS = "the documents carry most of the gain"
SENT_INSTR = ("the instructions carry most of the gain; the documents were named mainly when the prompt "
              "demanded it")
SENT_MIXED = "mixed"
SENT_I0 = "the instructions alone produced almost no documented names"
NOT_DEFINED = "not defined"


class AnalysisStop(Exception):
    """The analysis stops. The message names files or keys, never a value."""


def load_stats(path: Path | None = None):
    path = path or STATS_PATH
    spec = importlib.util.spec_from_file_location("thesis_numbers_stats_s2b", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def slug(model: str) -> str:
    """Folder name of a model, as run_cross_family_winning_config._slug (a unit test compares them)."""
    return model.replace(":", "_").replace(".", "_").replace("/", "_")


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def read_lines(path: Path) -> list[str]:
    return [line for line in path.read_text().splitlines() if line.strip()] if path.is_file() else []


# ---------------------------------------------------------------------------
# Scoring through the registered function
# ---------------------------------------------------------------------------
def score_with_registered_analyze(study2_root: Path, projects, cells: dict) -> dict:
    """Score records with analyze_study2.analyze() on a temporary copy of the layout it reads.

    cells: {(project, model): (record lines, bytes of retrieval_frozen.json or None)}.
    Returns {(model, arm, story_id): per-story entry of the registered report}.
    The registered module is pointed at the temporary folder in memory only; its file is not changed.
    """
    out: dict = {}
    with tempfile.TemporaryDirectory(prefix="s2b_score_") as tmp_name:
        tmp = Path(tmp_name)
        for project in projects:
            (tmp / project).mkdir()
            shutil.copyfile(study2_root / project / "stories.json", tmp / project / "stories.json")
        for (project, model), (lines, frozen_bytes) in cells.items():
            d = tmp / project / "results" / slug(model)
            d.mkdir(parents=True)
            (d / "records.jsonl").write_text("".join(line + "\n" for line in lines))
            if frozen_bytes is not None:
                (d / "retrieval_frozen.json").write_bytes(frozen_bytes)
        old = registered.STUDY2
        registered.STUDY2 = tmp
        try:
            reports = [registered.analyze(p) for p in projects]
        finally:
            registered.STUDY2 = old
    for rep in reports:
        for model, m in rep.get("models", {}).items():
            for arm, stories in m["per_story"].items():
                for sid, entry in stories.items():
                    out[(model, arm, sid)] = entry
    return out


def registered_cells(study2_root: Path, projects, models) -> dict:
    """The registered records of arms A and B, line for line in file order."""
    cells = {}
    for project in projects:
        for model in models:
            d = study2_root / project / "results" / slug(model)
            lines = [line for line in read_lines(d / "records.jsonl") if json.loads(line)["arm"] in REGISTERED_ARMS]
            if not lines:
                raise AnalysisStop(f"no registered records of arms A and B in {d}")
            frozen = d / "retrieval_frozen.json"
            cells[(project, model)] = (lines, frozen.read_bytes() if frozen.is_file() else None)
    return cells


def _differs(a, b, path: str, bad: list[str], tol: float) -> None:
    if isinstance(a, dict) and isinstance(b, dict):
        if set(a) != set(b):
            bad.append(f"{path} (keys)")
        for k in sorted(set(a) & set(b)):
            _differs(a[k], b[k], f"{path}.{k}", bad, tol)
    elif isinstance(a, (int, float)) and isinstance(b, (int, float)) and not isinstance(a, bool):
        if (math.isnan(a) != math.isnan(b)) or (not math.isnan(a) and abs(a - b) > tol):
            bad.append(path)
    elif a != b:
        bad.append(path)


def regression_check(scored: dict, registered_analysis: Path, projects, models, tol: float) -> int:
    """Compare the registered arms A and B, scored through this script, with the registered file."""
    if not registered_analysis.is_file():
        raise AnalysisStop(f"the registered analysis file is missing: {registered_analysis}")
    stored = {rep["project"]: rep for rep in json.loads(registered_analysis.read_text())}
    bad: list[str] = []
    n = 0
    for project in projects:
        rep = stored.get(project)
        if rep is None:
            bad.append(f"{project} (not in the registered file)")
            continue
        for model in models:
            for arm in REGISTERED_ARMS:
                old = rep["models"][model]["per_story"][arm]
                mine = {sid: scored[(model, arm, sid)] for (m, a, sid) in scored if m == model and a == arm
                        and sid in old}
                if set(mine) != set(old):
                    bad.append(f"{project}.{model}.per_story.{arm} (stories)")
                _differs(json.loads(json.dumps(mine, default=str)), old,
                         f"{project}.{model}.per_story.{arm}", bad, tol)
                n += len(old)
    if bad:
        raise AnalysisStop(f"{len(bad)} values of the registered arms A and B differ from "
                           f"{registered_analysis.name}: {bad[:20]}")
    return n


# ---------------------------------------------------------------------------
# New records
# ---------------------------------------------------------------------------
def verify_new_record(rec: dict, frozen: dict, identifiers: list[str]) -> str:
    """Check one Study 2b record and return its non-documentation prompt.

    The non-documentation prompt is the exact user prompt that was sent with the exact context string removed.
    """
    where = f"{rec.get('model')}/{rec.get('arm')}/{rec.get('story_id')}/run {rec.get('run')}"
    arm = rec.get("arm")
    if arm not in NEW_ARMS or rec.get("study") != "study2b":
        raise AnalysisStop(f"{where}: not a Study 2b record")
    sent = rec.get("prompt_user_sent")
    if not isinstance(sent, str) or sha256_text(sent) != rec.get("prompt_user_sha256"):
        raise AnalysisStop(f"{where}: the sent user prompt does not match its SHA-256")
    if sha256_text(rec.get("prompt_system") or "") != rec.get("prompt_system_sha256"):
        raise AnalysisStop(f"{where}: the sent system prompt does not match its SHA-256")
    ref = rec.get("doc_context_ref")
    if ref != CONTEXT_OF_ARM[arm]:
        raise AnalysisStop(f"{where}: the context type does not belong to this arm")
    if ref is None:
        ctx = None
    elif ref == "frozen":
        entry = frozen.get(rec["story_id"])
        if entry is None:
            raise AnalysisStop(f"{where}: no stored passages for this story in the copied file")
        ctx = entry["context"]
    else:
        ctx = FIXED_LINE
        # The registered function counts no "supported" name for this record. That is exact only
        # when the fixed line holds no fact-sheet name.
        if registered.found(FIXED_LINE, identifiers):
            raise AnalysisStop(f"{where}: the fixed line holds a fact-sheet name")
    if ctx is not None:
        if sha256_text(ctx) != rec.get("doc_context_sha256"):
            raise AnalysisStop(f"{where}: the context does not match its SHA-256")
        if sent.count(ctx) != 1:
            raise AnalysisStop(f"{where}: the context is not in the sent prompt exactly one time")
        non_doc = sent.replace(ctx, "", 1)
    else:
        non_doc = sent
    if non_doc != rec.get("prompt_user_non_doc"):
        raise AnalysisStop(f"{where}: the stored non-documentation prompt is not the sent prompt minus the context")
    return non_doc


def new_cells(study2_root: Path, study2b_root: Path, projects, models) -> tuple[dict, dict]:
    """Read and check the Study 2b records. Returns (cells for scoring, raw records per (project, model))."""
    cells, raw = {}, {}
    for project in projects:
        sheets = {s["id"]: s["fact_sheet"]["identifiers"]
                  for s in json.loads((study2_root / project / "stories.json").read_text())["stories"]}
        for model in models:
            d = study2b_root / project / slug(model)
            lines = read_lines(d / "records.jsonl")
            raw[(project, model)] = [json.loads(line) for line in lines]
            if not lines:
                continue
            copy = d / "retrieval_frozen.json"
            source = study2_root / project / "results" / slug(model) / "retrieval_frozen.json"
            if not copy.is_file() or copy.read_bytes() != source.read_bytes():
                raise AnalysisStop(f"{copy} is missing or differs from the registered stored passages")
            frozen = json.loads(copy.read_text())
            out_lines = []
            for rec in raw[(project, model)]:
                if rec.get("model") != model or rec.get("project") != project:
                    raise AnalysisStop(f"{d}: a record of another project or model")
                rec = dict(rec)
                rec["prompt_user_non_doc"] = verify_new_record(rec, frozen, sheets[rec["story_id"]])
                out_lines.append(json.dumps(rec, ensure_ascii=False))
            cells[(project, model)] = (out_lines, copy.read_bytes())
    return cells, raw


# LOCK 8.6 (2): what a complete arm is. For one model, an arm is complete when each of the
# 15 stories has each planned run of the design (N1, I0, N0: 3 runs; A2, B2: 1 run) with a
# parsed answer, or with a recorded failure after the one retry (two error records for that
# story and run). Only complete arms enter a statistic. A story whose runs all failed has no
# value; it leaves each contrast with that arm (lock 4.7), and S then uses the same stories in
# its numerator and its denominator.
def key_states(records: list[dict]) -> dict:
    out: dict = {}
    for r in records:
        st = out.setdefault((r["story_id"], r["arm"], r["run"]), {"ok": False, "errors": 0})
        if r.get("error"):
            st["errors"] += 1
        else:
            st["ok"] = True
    return out


def is_final(state: dict | None) -> bool:
    return state is not None and (state["ok"] or state["errors"] >= MAX_ATTEMPTS)


def arm_complete(records: list[dict], story_ids: list[str], arm: str) -> bool:
    states = key_states(records)
    return all(is_final(states.get((sid, arm, r))) for sid in story_ids for r in range(1, NEW_ARMS[arm] + 1))


def arm_counts(records: list[dict], arm: str) -> dict:
    """Records (last line per story and run), hard failures and parse retries (lock 4.7)."""
    latest = {}
    for r in records:
        if r["arm"] == arm:
            latest[(r["story_id"], r["run"])] = r
    kept = list(latest.values())
    return {
        "records": len(kept),
        "hard_failures": sum(1 for r in kept if r.get("error")),
        "parse_retries": sum(max(0, len(r.get("llm_calls") or []) - 1) for r in kept),
        "error_lines": sum(1 for r in records if r["arm"] == arm and r.get("error")),
    }


# ---------------------------------------------------------------------------
# Statistics (thesis/numbers/stats.py only)
# ---------------------------------------------------------------------------
def contrast(st, x: dict, y: dict, *, test: bool) -> dict | None:
    """x minus y, paired by story. With test=True also the exact Wilcoxon p of stats.py."""
    stories = sorted(set(x) & set(y))
    if not stories:
        return None
    diffs = [x[s] - y[s] for s in stories]
    w = st.wilcoxon_exact(diffs)
    out = {"stories": stories, "diffs": diffs, "n": len(diffs), "mean": st.mean(diffs),
           "n_pos": w["n_pos"], "n_neg": w["n_neg"], "n_zero": w["n_zero"], "d_z": st.cohen_dz(diffs),
           "test": "exact Wilcoxon signed-rank (stats.wilcoxon_exact)" if test else None}
    if test:
        out.update(p=w["p"], p_min=w["p_min"], n_used=w["n_used"])
    return out


def passes(c: dict | None, tol: float) -> bool:
    """Lock 8.2: the mean is above zero and p is below 0.0125."""
    return c is not None and c.get("p") is not None and c["mean"] > tol and c["p"] < ALPHA


def band(s: float | None, tol: float) -> str | None:
    """Lock 8.3. Two values closer than TOL count as equal (lock 3.1)."""
    if s is None:
        return None
    if s >= BAND_HIGH - tol:
        return "high"
    if s < BAND_LOW - tol:
        return "low"
    return "mid"


def share(num: dict | None, den: dict | None, tol: float) -> float | None:
    # LOCK 8.6 (3): when the registered B minus A mean of a model is not above zero, S is not
    # defined for that model. It gets no band, so neither of the first two sentences can apply.
    if num is None or den is None or not den["mean"] > tol:
        return None
    return num["mean"] / den["mean"]


def drift(st, rep: dict, base: dict, limit: float, tol: float) -> dict:
    # LOCK 8.6 (1): how drift is measured. For each model: the mean over the stories that have a
    # repeat value of (repeat story value minus registered story value). The registered story value
    # is the mean of its 3 registered runs; the repeat value is its 1 run. The repeat "differs by
    # more than" the limit when the absolute value of that mean is above the limit (B: 0.08, A: 0.05).
    stories = sorted(set(rep) & set(base))
    if not stories:
        return {"stories": 0, "difference": None, "limit": limit, "flagged": None}
    diffs = [rep[s] - base[s] for s in stories]
    d = st.mean(diffs)
    return {"stories": len(stories), "mean_repeat": st.mean([rep[s] for s in stories]),
            "mean_registered": st.mean([base[s] for s in stories]), "difference": d,
            "limit": limit, "flagged": abs(d) - limit > tol}


# ---------------------------------------------------------------------------
# Display (lock 3.5)
# ---------------------------------------------------------------------------
def pp(x: float | None, signed: bool = True) -> str:
    if x is None:
        return NOT_DEFINED
    return f"{100 * x:+.1f} pp" if signed else f"{100 * x:.1f}%"


def p_text(c: dict | None) -> str:
    if c is None or c.get("p") is None:
        return "no test (descriptive)"
    if c["n_used"] == 0:
        return "p = 1.0000, all differences are zero"
    at_floor = abs(c["p"] - c["p_min"]) <= 1e-15
    text = "p < 0.0001" if c["p"] < 0.0001 else f"p = {c['p']:.4f}"
    if at_floor:
        if c["n_used"] == c["n"]:
            text += f", the smallest value that {c['n']} stories can give"
        else:
            text += f", the smallest value that {c['n_used']} non-zero differences can give"
    return text


def dz_text(d: float | None) -> str:
    return NOT_DEFINED if d is None else f"{d:.2f}"


def share_text(s: float | None) -> str:
    return NOT_DEFINED if s is None else f"{s:.2f}"


# ---------------------------------------------------------------------------
# The analysis
# ---------------------------------------------------------------------------
def analyze(study2_root: Path, study2b_root: Path, registered_analysis: Path, st,
            projects=PROJECTS, models=MODELS) -> dict:
    tol = st.TOL
    # 1. Regression: the registered arms A and B through this script, before any new record is read.
    reg_scored = score_with_registered_analyze(study2_root, projects, registered_cells(study2_root, projects, models))
    n_reg = regression_check(reg_scored, registered_analysis, projects, models, tol)

    # 2. The new records.
    cells, raw = new_cells(study2_root, study2b_root, projects, models)
    new_scored = score_with_registered_analyze(study2_root, projects, cells) if cells else {}

    story_ids = {p: [s["id"] for s in json.loads((study2_root / p / "stories.json").read_text())["stories"]]
                 for p in projects}
    result = {"models": {}, "sentences": {}}
    for model in models:
        complete = {arm: all(arm_complete(raw[(p, model)], story_ids[p], arm) for p in projects) for arm in NEW_ARMS}
        values = {arm: {sid: e["frac_reqs_citing"] for (m, a, sid), e in reg_scored.items() if m == model and a == arm}
                  for arm in REGISTERED_ARMS}
        for arm in NEW_ARMS:
            values[arm] = ({sid: e["frac_reqs_citing"] for (m, a, sid), e in new_scored.items()
                            if m == model and a == arm} if complete[arm] else {})
        all_records = [r for p in projects for r in raw[(p, model)]]
        counts = {arm: {**arm_counts(all_records, arm), "complete": complete[arm],
                        "stories_with_value": len(values[arm]),
                        "rate": statistics_mean(st, values[arm])} for arm in NEW_ARMS}

        k1 = contrast(st, values["N1"], values["A"], test=True) if complete["N1"] else None
        k2 = contrast(st, values["B"], values["N1"], test=True) if complete["N1"] else None
        ba = None
        if k1 is not None:
            ba = contrast(st, {s: values["B"][s] for s in k1["stories"] if s in values["B"]}, values["A"], test=False)
        s_share = share(k1, ba, tol)
        identity_ok = None
        if k1 and k2 and ba and k1["stories"] == k2["stories"] == ba["stories"]:
            identity_ok = all(abs(a + b - c) <= 1e-12 for a, b, c in zip(k1["diffs"], k2["diffs"], ba["diffs"]))

        i0a = contrast(st, values["I0"], values["A"], test=False) if complete["I0"] else None
        n0a = contrast(st, values["N0"], values["A"], test=False) if complete["N0"] else None
        n1n0 = contrast(st, values["N1"], values["N0"], test=False) if complete["N0"] and complete["N1"] else None
        ba_n0 = None
        if n0a is not None:
            ba_n0 = contrast(st, {s: values["B"][s] for s in n0a["stories"] if s in values["B"]}, values["A"],
                             test=False)
        s_n0 = share(n0a, ba_n0, tol)

        drifts, reasons = {}, []
        for rep_arm, limit in DRIFT_LIMIT.items():
            base_arm = BASE_OF_REPEAT[rep_arm]
            if complete[rep_arm]:
                drifts[rep_arm] = drift(st, values[rep_arm], values[base_arm], limit, tol)
                if drifts[rep_arm]["flagged"]:
                    reasons.append(f"repeat {base_arm} differs from {base_arm} by more than {limit:.2f}")
            else:
                # LOCK 8.6 (1), continued: without a complete repeat arm, drift is not measured, and the
                # contrasts of that model are also called "indicative".
                drifts[rep_arm] = {"stories": 0, "difference": None, "limit": limit, "flagged": None}
                reasons.append(f"the drift check {rep_arm} is not complete")

        result["models"][model] = {
            "arms": counts,
            "K1_N1_minus_A": k1, "K2_B_minus_N1": k2, "registered_B_minus_A": ba,
            "K1_plus_K2_equals_B_minus_A": identity_ok,
            "K1_passes": passes(k1, tol), "K2_passes": passes(k2, tol),
            "S": s_share, "S_band": BAND_TEXT[band(s_share, tol)],
            "I0_minus_A": i0a, "N0_minus_A": n0a, "N1_minus_N0": n1n0, "S_N0_lower_limit": s_n0,
            "drift": drifts, "indicative": bool(reasons), "indicative_reasons": reasons,
        }

    result["sentences"] = sentences(result["models"], models, tol)
    result["meta"] = {
        "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "registered_values_checked": n_reg,
        "regression": f"the registered arms A and B, scored through this script, equal {registered_analysis.name}",
        "alpha": ALPHA, "bands": [BAND_HIGH, BAND_LOW], "drift_limits": DRIFT_LIMIT, "i0_limit": I0_LIMIT,
        "tol": tol, "projects": list(projects), "models": list(models),
        "stats_sha256": hashlib.sha256(STATS_PATH.read_bytes()).hexdigest() if STATS_PATH.is_file() else None,
        "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    }
    return result


def statistics_mean(st, values: dict) -> float | None:
    return st.mean(list(values.values())) if values else None


def sentences(per_model: dict, models, tol: float) -> dict:
    """The fixed sentences of lock 8.4 (and the sentence of LOCK 8.6 (3))."""
    out: dict = {"main": None, "not_defined": [], "i0": None, "indicative": []}
    missing = [m for m in models if per_model[m]["K1_N1_minus_A"] is None]
    if missing:
        out["main"] = "not delivered: arm N1 is not complete for " + ", ".join(missing)
    else:
        shares = {m: per_model[m]["S"] for m in models}
        bands = {m: band(shares[m], tol) for m in models}
        if all(bands[m] == "high" for m in models) and all(per_model[m]["K1_passes"] for m in models):
            out["main"] = SENT_DOCS
        elif all(bands[m] == "low" for m in models) and all(per_model[m]["K2_passes"] for m in models):
            out["main"] = SENT_INSTR
        else:
            out["main"] = SENT_MIXED + " (" + "; ".join(f"S = {share_text(shares[m])} for {m}" for m in models) + ")"
        for m in models:
            if shares[m] is None:
                # LOCK 8.6 (3): the sentence when the registered B minus A mean is not above zero.
                out["not_defined"].append(
                    f"S is not defined for {m}: its registered B minus A mean is not above zero, so there is "
                    "no gain to split between the documents and the instructions.")
    i0 = {m: per_model[m]["I0_minus_A"] for m in models}
    if all(i0[m] is not None for m in models) and all(i0[m]["mean"] <= I0_LIMIT + tol for m in models):
        out["i0"] = SENT_I0
    for m in models:
        if per_model[m]["indicative"]:
            out["indicative"].append(f"The contrasts of {m} are indicative: " + "; ".join(
                per_model[m]["indicative_reasons"]) + ".")
    return out


def to_markdown(result: dict) -> str:
    lines = ["# Study 2b analysis", "",
             f"Written {result['meta']['generated_at_utc']} by scripts/analyze_study2b.py. "
             "Method: thesis/analysis_lock.md sections 3, 4 and 8, and lab notebook section 16.", "",
             f"Regression: {result['meta']['regression']}.", "",
             "## Fixed sentences", "",
             f"- 2 by 2 table: {result['sentences']['main']}"]
    for s in result["sentences"]["not_defined"]:
        lines.append(f"- {s}")
    lines.append(f"- Instructions alone (I0 minus A): {result['sentences']['i0'] or 'no fixed sentence applies'}")
    for s in result["sentences"]["indicative"]:
        lines.append(f"- {s}")
    for model, m in result["models"].items():
        lines += ["", f"## {model}", "", "### Arms", "",
                  "| Arm | Complete | Records | Hard failures | Parse retries | Stories with a value | Rate |",
                  "|---|---|---|---|---|---|---|"]
        for arm, c in m["arms"].items():
            lines.append(f"| {arm} | {'yes' if c['complete'] else 'no'} | {c['records']} | {c['hard_failures']} | "
                         f"{c['parse_retries']} | {c['stories_with_value']} | {pp(c['rate'], signed=False)} |")
        lines += ["", "### Contrasts (paired by story)", "",
                  "| Contrast | Stories | Mean | Positive / zero / negative | p | d_z | Passes |",
                  "|---|---|---|---|---|---|---|"]
        rows = [("K1 = N1 minus A (tested)", m["K1_N1_minus_A"], m["K1_passes"]),
                ("K2 = B minus N1 (tested)", m["K2_B_minus_N1"], m["K2_passes"]),
                ("Registered B minus A (same stories)", m["registered_B_minus_A"], None),
                ("I0 minus A (descriptive)", m["I0_minus_A"], None),
                ("N0 minus A (descriptive, lower limit)", m["N0_minus_A"], None),
                ("N1 minus N0 (descriptive)", m["N1_minus_N0"], None)]
        for name, c, ok in rows:
            if c is None:
                lines.append(f"| {name} | not delivered (arm not complete) | | | | | |")
                continue
            passed = "descriptive" if ok is None else ("yes" if ok else "no")
            lines.append(f"| {name} | {c['n']} | {pp(c['mean'])} | {c['n_pos']} / {c['n_zero']} / {c['n_neg']} | "
                         f"{p_text(c)} | {dz_text(c['d_z'])} | {passed} |")
        lines += ["", f"Share S = mean K1 / mean registered B minus A: {share_text(m['S'])} (band: {m['S_band']}).",
                  f"Lower limit from N0 (descriptive): {share_text(m['S_N0_lower_limit'])}.",
                  f"K1 plus K2 equals B minus A for each story: {m['K1_plus_K2_equals_B_minus_A']}.",
                  "d_z is not stable with few stories.", "", "### Drift check", ""]
        for rep_arm, d in m["drift"].items():
            base = BASE_OF_REPEAT[rep_arm]
            if d["difference"] is None:
                lines.append(f"- {rep_arm} against {base}: not measured (arm not complete).")
            else:
                lines.append(f"- {rep_arm} against {base}: {d['stories']} stories, difference {pp(d['difference'])}, "
                             f"limit {100 * d['limit']:.1f} pp, {'above the limit' if d['flagged'] else 'within the limit'}.")
        if m["indicative"]:
            lines.append(f"- The contrasts of this model are indicative: {'; '.join(m['indicative_reasons'])}.")
    return "\n".join(lines) + "\n"


def is_inside(path: Path, base: Path) -> bool:
    """True when path is base or a folder below it, also when it is written in another case.

    The file system of this Mac does not see a difference between upper and lower case, so
    real_test_cases/STUDY2 is the same folder as real_test_cases/study2.
    """
    p, b = path.expanduser().resolve(), base.expanduser().resolve()
    if p == b or p.is_relative_to(b):
        return True
    pc, bc = str(p).casefold(), str(b).casefold()
    if pc == bc or pc.startswith(bc.rstrip("/") + "/"):
        return True
    if b.exists():
        for folder in (p, *p.parents):
            try:
                if folder.exists() and os.path.samefile(folder, b):
                    return True
            except OSError:
                continue
    return False


def check_label_lock(review_dir: Path | None = None) -> str | None:
    """None when the human label file is locked, else the reason. Results stay sealed until then."""
    review_dir = review_dir or REVIEW_V2
    try:
        import score_study2_review as review
        state = review.lock_state(review_dir, review.read_locks(review_dir), review.HUMAN)
    except Exception as e:
        return f"the lock state of the review cannot be read: {e}"
    return None if state == "locked_ok" else f"the human label file is not locked (state: {state})"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--study2b-root", default=str(STUDY2B), help="folder with the Study 2b records")
    ap.add_argument("--out-dir", default=str(STUDY2B), help="folder for analysis_s2b.json and analysis_s2b.md")
    ap.add_argument("--overwrite", action="store_true", help="write over existing output files")
    args = ap.parse_args(argv)
    out_dir = Path(args.out_dir).expanduser().resolve()
    if is_inside(out_dir, STUDY2):
        print(f"refused: {out_dir} is inside {STUDY2}. This script never writes there.")
        return 1
    reason = check_label_lock()
    if reason:
        print(f"refused: {reason}. The Study 2b results stay sealed until the labels are locked.")
        return 1
    paths = (out_dir / JSON_NAME, out_dir / MD_NAME)
    if not args.overwrite and any(p.exists() for p in paths):
        print("refused: an output file exists. Use --overwrite to write over it.")
        return 1
    try:
        result = analyze(STUDY2, Path(args.study2b_root).expanduser(), REGISTERED_ANALYSIS, load_stats())
    except (AnalysisStop, KeyError) as e:  # a KeyError names a key, never a value
        print(f"stopped: {type(e).__name__}: {e}")
        return 1
    out_dir.mkdir(parents=True, exist_ok=True)
    paths[0].write_text(json.dumps(result, indent=1, default=str) + "\n")
    paths[1].write_text(to_markdown(result))
    for p in paths:
        print(f"written: {p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
