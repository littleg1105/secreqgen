"""Final test, measure 1 (the primary measure: a script, no judge): name use.

For one answer: the share of its requirements that use one identifier or more of the answer key of the story,
where a name that the prompt gives outside the documentation passages does not count (score_record of
scripts/tuning_lib/measures.py, the measure of Study 2). An answer with no requirement counts 0. The value of a
story is the mean of its answers (2 models, 3 answers). Primary test: the standard stories, NEW minus OLD, the
exact two-sided Wilcoxon signed-rank test of thesis/numbers/stats.py (the one statistics module of the thesis).
The other kinds and NEW-0 are described with numbers and enter no test.

It reads the records of the runs and the sealed answer keys, prints numbers and story ids only, and writes
real_test_cases/final_test/measure1.json.

Run from coded_project/secreqgen:
  PYTHONPATH=src:scripts .venv/bin/python scripts/final_test_measure.py
"""

from __future__ import annotations

import importlib.util
import json
import statistics
import sys
from collections import defaultdict

from tuning_lib.final_test import KINDS, load_final_stories, read_run_set
from tuning_lib.guard import guarded_write_text
from tuning_lib.measures import latest_records, registered_module, score_record
from tuning_lib.pairstats import paired_summary
from tuning_lib.paths import FINAL_TEST_ROOT, MODELS, PROJECTS, TUNING_ROOT, WORKSPACE, model_slug
from tuning_lib.records import read_jsonl

ARM_OF = {"v0_old": "OLD", "s1_word_3000": "NEW", "v3_pn_render_fact_hits": "NEW-0"}
RUNS = {"A": "ft_a_{project}", "B": "ft_b_{project}"}
OUT = FINAL_TEST_ROOT / "measure1.json"
FIELDS = ("name_use", "n_with_name", "n_key_names", "n_requirements", "names_in_passages", "prompt_tokens", "name_use_common")


def _stats_module():
    spec = importlib.util.spec_from_file_location("_thesis_stats", WORKSPACE / "thesis" / "numbers" / "stats.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def read_answers() -> tuple[list[dict], dict[str, int]]:
    """One row for each good answer of the runs, and the count of failed generations for each arm."""
    stories = load_final_stories()
    found = registered_module().found
    rows, failed = [], defaultdict(int)
    for project in PROJECTS:
        for pattern in RUNS.values():
            for model in MODELS:
                path = TUNING_ROOT / pattern.format(project=project) / project / model_slug(model) / "records.jsonl"
                if not path.is_file():
                    continue
                for rec in latest_records(read_jsonl(path)).values():
                    arm = ARM_OF[rec["config_id"]]
                    if rec.get("error") is not None:
                        failed[arm] += 1
                        continue
                    ids = stories[rec["story_id"]].identifiers
                    sc = score_record(rec, ids)
                    in_prompt = found(rec["prompt_system"] + "\n" + rec["prompt_user_sent"], list(ids))
                    rows.append({
                        "_rec": rec, "_given": set(sc.prompt_given),
                        "arm": arm, "project": project, "model": model, "story_id": rec["story_id"], "run": rec["run"],
                        "name_use": sc.name_use, "n_with_name": sc.n_with_name, "n_key_names": len(sc.attributable),
                        "n_requirements": sc.n_requirements, "n_key": len(ids), "n_given": len(sc.prompt_given),
                        "names_in_passages": len(in_prompt - set(sc.prompt_given)),
                        "prompt_tokens": rec.get("prompt_tokens") or 0, "cost_usd": rec.get("cost_usd") or 0.0,
                    })
    # Second reading (described, no test): a name that the prompt of ONE arm gives outside the documentation is
    # taken out of the key for EACH arm, so that the two arms are scored on the same names.
    union: dict[tuple[str, str], set[str]] = defaultdict(set)
    for r in rows:
        if r["arm"] in ("OLD", "NEW"):
            union[(r["story_id"], r["model"])] |= r["_given"]
    for r in rows:
        common = [i for i in stories[r["story_id"]].identifiers if i not in union[(r["story_id"], r["model"])]]
        r["name_use_common"] = score_record(r["_rec"], common).name_use
        del r["_rec"], r["_given"]
    return rows, dict(failed)


def story_values(rows: list[dict], field: str, models: tuple[str, ...] = MODELS) -> dict[tuple[str, str], float]:
    """(arm, story id) -> mean of the field over the answers of the story (both models by default)."""
    groups: dict[tuple[str, str], list[float]] = defaultdict(list)
    for r in rows:
        if r["model"] in models:
            groups[(r["arm"], r["story_id"])].append(r[field])
    return {k: statistics.fmean(v) for k, v in groups.items()}


def main() -> int:
    stats = _stats_module()
    run_set = read_run_set()
    rows, failed = read_answers()
    if not rows:
        print("[FAIL] no record found")
        return 1
    ids_of = {kind: [sid for project in PROJECTS for sid in run_set[project][kind]] for kind in KINDS}
    n_answers = defaultdict(int)
    for r in rows:
        n_answers[(r["arm"], r["story_id"])] += 1
    short = {f"{arm} {sid}": n for (arm, sid), n in sorted(n_answers.items()) if n != 6}
    print(f"good answers: {len(rows)}; failed generations: {failed or 0}; story and arm with a count that is not 6: {short or 'none'}")
    print(f"cost of the good answers: {sum(r['cost_usd'] for r in rows):.4f} USD")
    values = {field: story_values(rows, field) for field in FIELDS}
    out: dict = {"n_answers": len(rows), "failed": failed, "kinds": {}, "stories": {}}

    def mean_of(field: str, arm: str, sids: list[str]) -> float | None:
        got = [values[field][(arm, s)] for s in sids if (arm, s) in values[field]]
        return statistics.fmean(got) if got else None

    def fmt(x: float | None, pct: bool = False) -> str:
        return "n/a" if x is None else (f"{100 * x:5.1f}" if pct else f"{x:5.2f}")

    print("\nKind          n  arm    name use  req. with key name  key names used  requirements  key names in passages  prompt tokens")
    for kind in KINDS:
        sids = ids_of[kind]
        out["kinds"][kind] = {"n_stories": len(sids), "arms": {}}
        for arm in ("OLD", "NEW", "NEW-0"):
            if mean_of("name_use", arm, sids) is None:
                continue
            m = {field: mean_of(field, arm, sids) for field in FIELDS}
            out["kinds"][kind]["arms"][arm] = m
            print(f"{kind:12s} {len(sids):2d}  {arm:5s}  {fmt(m['name_use'], True)}     {fmt(m['n_with_name'])}               {fmt(m['n_key_names'])}"
                  f"           {fmt(m['n_requirements'])}         {fmt(m['names_in_passages'])}                 {m['prompt_tokens']:6.0f}")
        diffs = [values["name_use"][("NEW", s)] - values["name_use"][("OLD", s)] for s in sids
                 if ("NEW", s) in values["name_use"] and ("OLD", s) in values["name_use"]]
        ps = paired_summary(diffs)
        out["kinds"][kind]["new_minus_old"] = {"n": ps.n, "mean": ps.mean, "se": ps.se, "up": ps.up, "equal": ps.equal, "down": ps.down}
        se = "n/a" if ps.se is None else f"{100 * ps.se:.1f}"
        print(f"{kind:12s}     NEW minus OLD, name use: mean {100 * ps.mean:+.1f} points (SE {se}); stories up {ps.up}, equal {ps.equal}, down {ps.down}")

    std = ids_of["standard"]
    diffs = [values["name_use"][("NEW", s)] - values["name_use"][("OLD", s)] for s in std]
    w = stats.wilcoxon_exact(diffs)
    out["primary"] = {"n_stories": len(std), "mean_difference": statistics.fmean(diffs), "wilcoxon_exact": w,
                      "d_z": stats.cohen_dz(diffs)}
    print(f"\nPRIMARY TEST (standard stories, NEW minus OLD, exact two-sided Wilcoxon signed-rank test): n {w['n_total']}, used {w['n_used']}, "
          f"up {w['n_pos']}, down {w['n_neg']}, zero {w['n_zero']}, W+ {w['w_plus']}, W- {w['w_minus']}, p = {w['p']:.4f}; "
          f"mean difference {100 * statistics.fmean(diffs):+.1f} points")
    print("\nStandard stories (name use of 100):  story        key  OLD    NEW    NEW-0  NEW minus OLD")
    n_key = {r["story_id"]: r["n_key"] for r in rows}
    for s in std:
        old, new, zero = (values["name_use"].get((arm, s)) for arm in ("OLD", "NEW", "NEW-0"))
        out["stories"][s] = {"n_key": n_key[s], "OLD": old, "NEW": new, "NEW-0": zero}
        print(f"                                     {s:12s} {n_key[s]:3d}  {fmt(old, True)}  {fmt(new, True)}  {fmt(zero, True)}  {100 * (new - old):+6.1f}")
    for kind in KINDS[1:]:
        for s in ids_of[kind]:
            out["stories"][s] = {"n_key": n_key.get(s), "OLD": values["name_use"].get(("OLD", s)), "NEW": values["name_use"].get(("NEW", s))}
    print("\nBy model, standard stories (name use of 100; described, no test):")
    out["by_model"] = {}
    for model in MODELS:
        per = story_values(rows, "name_use", (model,))
        old = statistics.fmean(per[("OLD", s)] for s in std)
        new = statistics.fmean(per[("NEW", s)] for s in std)
        zero = [per[("NEW-0", s)] for s in std if ("NEW-0", s) in per]
        ps = paired_summary([per[("NEW", s)] - per[("OLD", s)] for s in std])
        out["by_model"][model] = {"OLD": old, "NEW": new, "NEW-0": statistics.fmean(zero) if zero else None, "mean_difference": ps.mean, "se": ps.se}
        print(f"  {model:12s} OLD {100 * old:5.1f}  NEW {100 * new:5.1f}  NEW-0 {fmt(statistics.fmean(zero) if zero else None, True)}  "
              f"NEW minus OLD {100 * ps.mean:+.1f} (SE {100 * ps.se:.1f}); stories up {ps.up}, equal {ps.equal}, down {ps.down}")
    common = [values["name_use_common"][("NEW", s)] - values["name_use_common"][("OLD", s)] for s in std]
    wc = stats.wilcoxon_exact(common)
    out["second_reading_common_names"] = {
        "OLD": statistics.fmean(values["name_use_common"][("OLD", s)] for s in std),
        "NEW": statistics.fmean(values["name_use_common"][("NEW", s)] for s in std),
        "mean_difference": statistics.fmean(common), "wilcoxon_exact": wc,
    }
    print(f"\nSecond reading (described): names that the prompt of one arm gives are taken out of the key for both arms. Standard stories: "
          f"OLD {100 * out['second_reading_common_names']['OLD']:.1f}, NEW {100 * out['second_reading_common_names']['NEW']:.1f}, "
          f"mean difference {100 * statistics.fmean(common):+.1f} points; up {wc['n_pos']}, down {wc['n_neg']}, zero {wc['n_zero']}; p = {wc['p']:.4f}")
    # Names of the key that the prompt gives outside the documentation (the measure does not count them for that arm).
    given = defaultdict(list)
    for r in rows:
        if r["story_id"] in std:
            given[r["arm"]].append(r["n_given"])
    out["key_names_given_by_prompt_standard"] = {arm: statistics.fmean(v) for arm, v in given.items()}
    print("\nKey names that the prompt gives outside the documentation (mean for each answer, standard stories): "
          + ", ".join(f"{arm} {statistics.fmean(v):.2f}" for arm, v in sorted(given.items())))
    zero_key = sorted(s for kind in KINDS for s in ids_of[kind] if n_key.get(s) == 0)
    out["stories_with_empty_key"] = zero_key
    print(f"stories with a key of 0 identifiers (name use is 0 by definition): {len(zero_key)} {zero_key}")
    guarded_write_text(OUT, json.dumps(out, indent=1) + "\n")
    print(f"\nwritten: final_test/{OUT.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
