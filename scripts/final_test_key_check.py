"""Final test: two outside AI raters check each answer key against its documentation text.

Rule (thesis/analysis_lock.md, final test, step 1, point 8): GPT (through Codex) and Gemini (through agy),
each in a call with no tools and no other job, check each identifier and each fact of each key. An
identifier is disputed when one rater or both do not accept it. A story with more than 20 of 100 disputed
identifiers is removed from the test.

The calls reuse the command lines and the log reader of scripts/run_study2_ai_raters.py. A message holds the
documentation text of the key, the story and the key: no path of this computer. The script writes below
real_test_cases/final_test/sealed/key_check/ and prints counts only (no story text, no name).

Run from coded_project/secreqgen:
  PYTHONPATH=src:scripts .venv/bin/python scripts/final_test_key_check.py [--preflight] [--jobs 4] [--reuse]
"""

from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import run_study2_ai_raters as R
from tuning_lib import search as S
from tuning_lib.guard import assert_no_private_text, guarded_mkdir, guarded_write_text
from tuning_lib.paths import FINAL_TEST_ROOT, PROJECTS, RTC

SEALED = FINAL_TEST_ROOT / "sealed"
OUT = SEALED / "key_check"
TIME_LIMIT_S = 900
MAX_FILE_CHARS = 30000
LIMIT = 0.20

PROMPT = """You check the answer key of a test story. The story tests a tool that writes security requirements from a user story and the documentation of a software product. The key lists names and facts of the documentation that good requirements for the story must use.

Judge each identifier and each fact of the key ONLY against the documentation text below. Do not use what you remember about the product.

An identifier is ok when all three points hold:
1. It stands character by character in the documentation text below.
2. It is a name of this product (a setting, an option, a flag, a command, an endpoint, a path, a class, a function, a field, an environment variable, a role, a permission, a file). It is NOT a standard name of a protocol, an algorithm, a standard, a third-party library, a programming language or a vendor.
3. A correct and specific security requirement for the story could use it.

A fact is ok when the documentation text below supports it.

Answer with ONE JSON object and nothing else, in this form:
{{"identifiers": [{{"name": "<the identifier, copied>", "ok": true, "why": "<12 words at most>"}}], "facts": [{{"number": 1, "ok": true, "why": "<12 words at most>"}}]}}
Give one entry for each identifier and one for each fact, in the order of the key.

PRODUCT: {product}

STORY
Title: {title}
{story}
Acceptance criteria:
{criteria}

KEY
Identifiers:
{identifiers}
Facts:
{facts}

DOCUMENTATION TEXT
{doc_text}
"""


def doc_text_of(project: str, docs_dir: Path, story: dict) -> str:
    if story["kind"] == "standard":
        return (SEALED / project / "sections" / f"{story['id']}.md").read_text(encoding="utf-8")
    parts = []
    for name in story["fact_sheet"]["source_files"]:
        text = S.strip_include_wrappers((docs_dir / name).read_text(encoding="utf-8"))
        parts.append(f"=== FILE {name} ===\n{text[:MAX_FILE_CHARS]}")
    return "\n\n".join(parts) or "(the key names no documentation file)"


def message_of(project: str, docs_dir: Path, story: dict) -> str:
    sheet = story["fact_sheet"]
    text = PROMPT.format(
        product=project,
        title=story["title"],
        story=story["story"],
        criteria="\n".join(f"- {c}" for c in story["acceptance_criteria"]),
        identifiers="\n".join(f"- {i}" for i in sheet["identifiers"]) or "(none)",
        facts="\n".join(f"{n}. {f}" for n, f in enumerate(sheet["facts"], start=1)) or "(none)",
        doc_text=doc_text_of(project, docs_dir, story),
    )
    assert_no_private_text(text, what="a message for an outside rater")
    return text


def parse_verdict(answer: str) -> dict | None:
    i, j = answer.find("{"), answer.rfind("}")
    if i < 0 or j <= i:
        return None
    try:
        data = json.loads(answer[i : j + 1])
    except ValueError:
        return None
    if not isinstance(data, dict) or not isinstance(data.get("identifiers"), list) or not isinstance(data.get("facts"), list):
        return None
    return data


def one_call(rater: str, story_id: str, message: str, reuse: bool = False) -> dict:
    """One rater call with one more try when no answer comes. Returns the verdict (or None) and the status.

    With reuse, a stored answer of an earlier run of this script is taken as it is, and a new call writes its
    logs as tries 3 and 4, so the logs of the first run stay.
    """
    folder = OUT / rater
    stored = folder / f"{story_id}.json"
    if reuse and stored.is_file():
        return {"verdict": json.loads(stored.read_text(encoding="utf-8")), "status": "answer", "attempt": 0}
    last = "no call"
    for attempt in ((3, 4) if reuse else (1, 2)):
        out_path, err_path = folder / f"{story_id}.a{attempt}.log", folder / f"{story_id}.a{attempt}.err"
        res = R.run_cli(rater, message, TIME_LIMIT_S, out_path, err_path)
        log = R.safe_read_log(rater, out_path.read_text(encoding="utf-8", errors="replace") if out_path.is_file() else "")
        status, reason = R.judge(res, log, rater)
        verdict = parse_verdict(log["answer"]) if status == "answer" else None
        if verdict is not None:
            (folder / f"{story_id}.json").write_text(json.dumps(verdict, ensure_ascii=False, indent=1), encoding="utf-8")
            return {"verdict": verdict, "status": "answer", "attempt": attempt}
        last = reason or "the answer is not the JSON object"
    return {"verdict": None, "status": f"no answer ({last})", "attempt": attempt}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--preflight", action="store_true", help="one small call to each rater, then stop")
    parser.add_argument("--jobs", type=int, default=4)
    parser.add_argument("--reuse", action="store_true", help="keep each stored answer; call only where no answer is stored")
    args = parser.parse_args(argv)
    for rater in R.RATERS:
        guarded_mkdir(OUT / rater)
    if args.preflight:
        for rater in R.RATERS:
            out_path, err_path = OUT / rater / "preflight.log", OUT / rater / "preflight.err"
            res = R.run_cli(rater, R.PREFLIGHT_PROMPT, R.PREFLIGHT_LIMIT_S, out_path, err_path)
            log = R.safe_read_log(rater, out_path.read_text(encoding="utf-8", errors="replace") if out_path.is_file() else "")
            status, reason = R.judge(res, log, rater)
            print(f"preflight {rater}: {status} {reason} | answer {log['answer'].strip()[:20]!r} | version {R.cli_version(rater)} | seconds {res.get('seconds')}")
        return 0
    jobs = []
    stories = {}
    for project in PROJECTS:
        data = json.loads((SEALED / project / "stories.json").read_text(encoding="utf-8"))
        docs_dir = RTC / data["docs_dir"]
        for story in data["stories"]:
            stories[story["id"]] = story
            message = message_of(project, docs_dir, story)
            for rater in R.RATERS:
                jobs.append((rater, story["id"], message))
    print(f"calls planned: {len(jobs)} ({len(stories)} stories, {len(R.RATERS)} raters)")
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        results = list(pool.map(lambda j: (j[0], j[1], one_call(*j, reuse=args.reuse)), jobs))
    by_story: dict[str, dict] = {sid: {} for sid in stories}
    for rater, sid, result in results:
        by_story[sid][rater] = result
    summary = {}
    removed = []
    for sid, story in stories.items():
        ids, facts = story["fact_sheet"]["identifiers"], story["fact_sheet"]["facts"]
        disputed, fact_disputed, row = set(), set(), {}
        for rater in R.RATERS:
            result = by_story[sid][rater]
            verdict = result["verdict"]
            bad_ids = [e.get("name") for e in (verdict or {}).get("identifiers", []) if isinstance(e, dict) and e.get("ok") is False]
            bad_ids = [n for n in bad_ids if n in ids]
            bad_facts = [e.get("number") for e in (verdict or {}).get("facts", []) if isinstance(e, dict) and e.get("ok") is False]
            disputed.update(bad_ids)
            fact_disputed.update(n for n in bad_facts if isinstance(n, int))
            row[rater] = {"status": result["status"], "identifiers_not_ok": len(bad_ids), "facts_not_ok": len(bad_facts),
                          "identifiers_judged": len((verdict or {}).get("identifiers", []))}
        share = len(disputed) / len(ids) if ids else 0.0
        out = share > LIMIT
        if out:
            removed.append(sid)
        summary[sid] = {"kind": story["kind"], "n_identifiers": len(ids), "n_facts": len(facts), "disputed_identifiers": sorted(disputed),
                        "disputed_facts": sorted(fact_disputed), "share_disputed": round(share, 3), "removed": out, "raters": row}
        print(
            f"{sid:10s} {story['kind']:12s} identifiers {len(ids):2d} | not ok: Gemini {row['gemini']['identifiers_not_ok']}, GPT {row['gpt']['identifiers_not_ok']}, "
            f"either {len(disputed)} ({100 * share:.0f} of 100){' REMOVED' if out else ''} | facts {len(facts)}, not ok by either {len(fact_disputed)} | "
            f"calls: Gemini {row['gemini']['status'][:9]}, GPT {row['gpt']['status'][:9]}"
        )
    guarded_write_text(OUT / "summary.json", json.dumps(summary, ensure_ascii=False, indent=1) + "\n")
    no_answer = sum(1 for _r, _s, res in results if res["status"] != "answer")
    print(f"stories removed (more than 20 of 100 disputed identifiers): {len(removed)} {removed}")
    print(f"calls with no answer: {no_answer} of {len(results)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
