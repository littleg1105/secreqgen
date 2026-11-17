"""Final test, step 1: check the drafts of the new stories, and seal them.

The rules are in thesis/analysis_lock.md (the line of 16 November 2026 about step 1 of the final test).
This script prints codes and counts only: no story text, no identifier, no fact. The session that tuned the
tool runs it and must not read the files below real_test_cases/final_test/sealed/.

Run from coded_project/secreqgen:
  PYTHONPATH=src:scripts .venv/bin/python scripts/final_test_check.py            check the drafts
  ... --seal        when each draft passes: write sealed/<product>/stories.json and sealed/SEAL.sha256
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

from tuning_lib import lab_measures as L
from tuning_lib import search as S
from tuning_lib.guard import DEFAULT_GUARD, guarded_write_text

FINAL_TEST_ROOT_CHECK = DEFAULT_GUARD.check  # raises when a path is outside the allowed folders
from tuning_lib.paths import FINAL_TEST_ROOT, PROJECTS, RTC, SCRIPTS, STUDY2

SEALED = FINAL_TEST_ROOT / "sealed"
PREFIX = {"keycloak": "KC", "bitwarden": "BW", "mastodon": "MT"}
VERSIONS = ("v0_old", "v3_pn_render_fact_hits", "s1_word_3000")
STORY_RE = re.compile(r"^As an? .+?, I want .+?, so that .+", re.IGNORECASE | re.DOTALL)
# kind -> (criteria min, max), (identifiers min, max), (facts min, max), (source files min, max)
LIMITS = {
    "standard": ((4, 5), (3, 15), (4, 8), (1, 1)),
    "trivial": ((2, 4), (0, 6), (0, 4), (0, 3)),
    "new_feature": ((3, 5), (0, 8), (2, 6), (1, 3)),
}
KEYS = {
    "standard": {"id", "kind", "title", "story", "acceptance_criteria", "barebones_story", "fact_sheet"},
    "trivial": {"id", "kind", "title", "story", "acceptance_criteria", "fact_sheet"},
    "new_feature": {"id", "kind", "title", "story", "acceptance_criteria", "not_in_documentation", "fact_sheet"},
}
SHEET_KEYS = {"source_files", "identifiers", "generic_terms", "facts"}


def sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _strings(value, lo: int, hi: int) -> bool:
    return isinstance(value, list) and lo <= len(value) <= hi and all(isinstance(x, str) and x.strip() for x in value)


def _words(text: str) -> int:
    return len(text.split())


def _whole(name: str, text: str) -> bool:
    return any(L.whole_rx(form).search(text) for form in L.search_forms(name))


def _in_story(name: str, story_text: str) -> bool:
    """True when the name stands in the story text as a whole word or phrase (letter case does not matter)."""
    pattern = r"(?<![A-Za-z0-9_])" + re.escape(name) + r"(?![A-Za-z0-9_])"
    return _whole(name, story_text) or re.search(pattern, story_text, re.IGNORECASE) is not None


class Product:
    """What the checks of one product need. It reads files and prints nothing."""

    def __init__(self, project: str) -> None:
        meta = json.loads((STUDY2 / project / "stories.json").read_text(encoding="utf-8"))
        self.project = project
        self.docs_dir_name = meta["docs_dir"]
        self.repo_dir_name = meta["repo_dir"]
        self.docs_dir = RTC / meta["docs_dir"]
        self.tuning_ids = {i for s in meta["stories"] for i in s["fact_sheet"]["identifiers"]}
        self.doc_files = {
            str(p.relative_to(self.docs_dir)): S.strip_include_wrappers(p.read_text(encoding="utf-8"))
            for p in sorted(self.docs_dir.glob("**/*.md"))
        }
        self.docs_text = "\n".join(self.doc_files.values())
        self.vocab = L._vocab(list(self.doc_files.values()))
        self.picks = json.loads((SEALED / project / "picks.json").read_text(encoding="utf-8"))["picks"]
        self.sections = {
            p["id"]: (SEALED / project / "sections" / f"{p['id']}.md").read_text(encoding="utf-8") for p in self.picks
        }
        self.templates = "\n".join(
            f.read_text(encoding="utf-8")
            for v in VERSIONS
            for f in sorted((SCRIPTS / "tuning_lib" / "prompt_versions" / v).glob("*"))
            if f.is_file()
        )

    def expected(self) -> list[tuple[str, str]]:
        prefix = PREFIX[self.project]
        out = [(p["id"], "standard") for p in self.picks]
        out += [(f"{prefix}-TR-00{n}", "trivial") for n in (1, 2)]
        out += [(f"{prefix}-NF-00{n}", "new_feature") for n in (1, 2)]
        return out

    def check(self, story_id: str, kind: str) -> tuple[list[str], list[str], dict | None]:
        """Failure codes, warning codes and the draft. The codes hold counts, never text."""
        path = SEALED / self.project / "drafts" / f"{story_id}.json"
        if not path.is_file():
            return ["missing_file"], [], None
        try:
            draft = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            return ["bad_json"], [], None
        if not isinstance(draft, dict):
            return ["bad_json"], [], None
        fails: list[str] = []
        warns: list[str] = []
        if set(draft) != KEYS[kind]:
            fails.append(f"keys:{len(set(draft) ^ KEYS[kind])}")
        sheet = draft.get("fact_sheet")
        if not isinstance(sheet, dict) or set(sheet) != SHEET_KEYS:
            fails.append("fact_sheet_keys")
            return fails, warns, None
        if draft.get("id") != story_id:
            fails.append("id")
        if draft.get("kind") != kind:
            fails.append("kind")
        (c_lo, c_hi), (i_lo, i_hi), (f_lo, f_hi), (s_lo, s_hi) = LIMITS[kind]
        title, story, criteria = draft.get("title"), draft.get("story"), draft.get("acceptance_criteria")
        if not isinstance(title, str) or not 4 <= _words(title) <= 14:
            fails.append("title")
        if not isinstance(story, str) or not STORY_RE.match(story.strip()):
            fails.append("story_form")
        if not _strings(criteria, c_lo, c_hi):
            fails.append("n_criteria")
        bare = draft.get("barebones_story", "") if kind == "standard" else ""
        if kind == "standard" and (not isinstance(bare, str) or not 3 <= _words(bare) <= 15):
            fails.append("barebones_length")
        if kind == "new_feature" and not (isinstance(draft.get("not_in_documentation"), str) and draft["not_in_documentation"].strip()):
            fails.append("not_in_documentation")
        ids, generic, facts, sources = sheet["identifiers"], sheet["generic_terms"], sheet["facts"], sheet["source_files"]
        if not (isinstance(ids, list) and all(isinstance(x, str) and x.strip() for x in ids) and i_lo <= len(ids) <= i_hi):
            fails.append("n_identifiers")
            ids = [x for x in ids if isinstance(x, str)] if isinstance(ids, list) else []
        if len(set(ids)) != len(ids):
            fails.append("duplicate_identifiers")
        if not (isinstance(generic, list) and all(isinstance(x, str) for x in generic) and len(generic) <= 10):
            fails.append("generic_terms")
            generic = []
        if not (isinstance(facts, list) and all(isinstance(x, str) and x.strip() for x in facts) and f_lo <= len(facts) <= f_hi):
            fails.append("n_facts")
        if not (isinstance(sources, list) and all(isinstance(x, str) for x in sources) and s_lo <= len(sources) <= s_hi):
            fails.append("n_source_files")
            sources = []
        if kind == "standard":
            pick = next(p for p in self.picks if p["id"] == story_id)
            if sources != [pick["file"]]:
                fails.append("source_file")
            where = self.sections[story_id]
            code = "ident_not_in_section"
        else:
            unknown = [s for s in sources if s not in self.doc_files]
            if unknown:
                fails.append(f"source_file_unknown:{len(unknown)}")
            where = "\n".join(self.doc_files[s] for s in sources if s in self.doc_files) or self.docs_text
            code = "ident_not_in_source_files"
        n = sum(1 for x in ids if x not in where)
        if n:
            fails.append(f"{code}:{n}")
        story_text = "\n".join([title or "", story or "", *(criteria if isinstance(criteria, list) else []), bare or ""])
        n = sum(1 for x in ids if _in_story(x, story_text))
        if n:
            fails.append(f"ident_in_story:{n}")
        n = sum(1 for x in ids if x in self.tuning_ids)
        if n:
            fails.append(f"ident_is_tuning_key:{n}")
        n = sum(1 for x in ids if _whole(x, self.templates))
        if n:
            fails.append(f"ident_in_templates:{n}")
        n = sum(1 for x in ids if x in generic)
        if n:
            fails.append(f"ident_is_generic:{n}")
        n = sum(1 for name in L.extract_names(story_text, self.vocab) if _whole(name, self.docs_text))
        if n:
            warns.append(f"code_like_name_in_story:{n}")
        return fails, warns, draft


def drop_banned() -> int:
    """Remove from each draft the identifiers that a writer cannot know to be banned.

    A writer sees the documentation only. So it cannot know that a name is an answer-key identifier of a
    Study 2 story, or that a name stands in a prompt template. This step removes such identifiers from the
    key (it changes nothing else) and prints counts only.
    """
    total = 0
    for project in PROJECTS:
        product = Product(project)
        for story_id, _kind in product.expected():
            path = SEALED / project / "drafts" / f"{story_id}.json"
            try:
                draft = json.loads(path.read_text(encoding="utf-8"))
                ids = draft["fact_sheet"]["identifiers"]
            except (OSError, ValueError, KeyError, TypeError):
                continue
            if not isinstance(ids, list):
                continue
            kept = [x for x in ids if not (isinstance(x, str) and (x in product.tuning_ids or _whole(x, product.templates)))]
            if len(kept) != len(ids):
                draft["fact_sheet"]["identifiers"] = kept
                FINAL_TEST_ROOT_CHECK(path)
                path.write_text(json.dumps(draft, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
                print(f"{story_id}: {len(ids) - len(kept)} banned identifier(s) removed, {len(kept)} left")
                total += len(ids) - len(kept)
    print(f"banned identifiers removed in total: {total}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--seal", action="store_true")
    parser.add_argument("--drop-banned", action="store_true", help="remove Study 2 key names and template names from the keys")
    args = parser.parse_args(argv)
    if args.drop_banned:
        return drop_banned()
    all_ok = True
    good: dict[str, list[dict]] = {}
    counts = {"identifiers": 0, "facts": 0}
    for project in PROJECTS:
        product = Product(project)
        good[project] = []
        for story_id, kind in product.expected():
            fails, warns, draft = product.check(story_id, kind)
            status = "PASS" if not fails else "FAIL " + ",".join(fails)
            print(f"{story_id:10s} {kind:12s} {status}" + (f"  (warning: {','.join(warns)})" if warns else ""))
            if fails or draft is None:
                all_ok = False
            else:
                good[project].append(draft)
                counts["identifiers"] += len(draft["fact_sheet"]["identifiers"])
                counts["facts"] += len(draft["fact_sheet"]["facts"])
    n_good = sum(len(v) for v in good.values())
    print(f"drafts that pass: {n_good} of 30; identifiers in them: {counts['identifiers']}; facts: {counts['facts']}")
    if not args.seal:
        return 0 if all_ok else 1
    if not all_ok:
        print("[FAIL] not sealed: a draft does not pass")
        return 1
    seal_lines = []
    for project in PROJECTS:
        product = Product(project)
        body = json.dumps(
            {
                "project": project, "docs_dir": product.docs_dir_name, "repo_dir": product.repo_dir_name,
                "status": "sealed for the final test (written by agents that saw the documentation only)",
                "stories": good[project],
            },
            ensure_ascii=False, indent=1,
        ) + "\n"
        path = SEALED / project / "stories.json"
        if path.exists():
            print(f"[FAIL] {project}: stories.json exists; it is not replaced")
            return 1
        guarded_write_text(path, body)
        files = [path, SEALED / project / "picks.json", *sorted((SEALED / project / "sections").glob("*.md"))]
        for f in files:
            seal_lines.append(f"{sha_bytes(f.read_bytes())}  {f.relative_to(SEALED)}")
        print(f"{project}: stories.json sha256 {sha_bytes(path.read_bytes())[:16]} ({len(good[project])} stories)")
    seal_text = "\n".join(seal_lines) + "\n"
    seal_path = SEALED / "SEAL.sha256"
    if seal_path.exists():
        print("[FAIL] SEAL.sha256 exists; it is not replaced")
        return 1
    guarded_write_text(seal_path, seal_text)
    print(f"SEAL.sha256: {len(seal_lines)} files, sha256 of the list {sha_bytes(seal_text.encode('utf-8'))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
