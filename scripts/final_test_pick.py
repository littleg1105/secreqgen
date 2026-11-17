"""Final test, step 1: pick the documentation sections for the new stories.

The rules are fixed in thesis/analysis_lock.md before the first run of this script:
- 6 sections for each of the three products of Study 2;
- a section is one heading with its text, as the chunker of the tool cuts it;
- it holds 5 or more code-like names, 2,000 tokens at most, and no answer-key identifier of a Study 2 story;
- if a product has fewer than 6 such sections, the limit for that product is 3 code-like names (Bitwarden: its
  documentation folder is small, and the 5 Study 2 stories use most of its names);
- a public seed decides the order; one section for each documentation file while files are left.

The script writes below real_test_cases/final_test/sealed/ through the write guard and never replaces a file.
It prints counts and hashes only. The session that tuned the tool must not read the files that it writes.

Run from coded_project/secreqgen:
  PYTHONPATH=src:scripts .venv/bin/python scripts/final_test_pick.py
"""

from __future__ import annotations

import hashlib
import json
import random
import sys
from collections import OrderedDict

from tuning_lib import lab_measures as L
from tuning_lib import search as S
from tuning_lib.guard import assert_no_private_text, guarded_mkdir, guarded_write_text
from tuning_lib.paths import FINAL_TEST_ROOT, PROJECTS, RTC, STUDY2

SEED = "final-test-2026-11-16"
N_PER_PRODUCT = 6
MIN_NAMES = 5
MIN_NAMES_SMALL_FOLDER = 3  # used for a product only when MIN_NAMES gives fewer than N_PER_PRODUCT sections
MAX_TOKENS = 2000
PREFIX = {"keycloak": "KC-FT", "bitwarden": "BW-FT", "mastodon": "MT-FT"}
SEALED = FINAL_TEST_ROOT / "sealed"


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sections_of(docs_dir) -> "OrderedDict[tuple[str, str], dict]":
    """Sections of a documentation folder: (file, heading path) -> text and tokens, in document order."""
    out: "OrderedDict[tuple[str, str], dict]" = OrderedDict()
    for chunk in S.load_chunks(docs_dir):
        assert_no_private_text(chunk.text, what="a documentation section")
        entry = out.setdefault((chunk.file, chunk.section), {"parts": [], "tokens": 0})
        entry["parts"].append(chunk.text)
        entry["tokens"] += chunk.tokens
    return out


def eligible_sections(product: str, min_names: int = MIN_NAMES) -> tuple[list[dict], int]:
    meta = json.loads((STUDY2 / product / "stories.json").read_text(encoding="utf-8"))
    docs_dir = RTC / meta["docs_dir"]
    tuning_ids = sorted({i for s in meta["stories"] for i in s["fact_sheet"]["identifiers"]})
    patterns = [L.whole_rx(form) for name in tuning_ids for form in L.search_forms(name)]
    docs = [p.read_text(encoding="utf-8") for p in sorted(docs_dir.glob("**/*.md"))]
    vocab = L._vocab(docs)
    sections = sections_of(docs_dir)
    out = []
    for (file, section), entry in sections.items():
        if not section or entry["tokens"] > MAX_TOKENS:
            continue
        text = "\n\n".join(entry["parts"])
        names = L.extract_names(text, vocab)
        if len(names) < min_names:
            continue
        if any(rx.search(text) for rx in patterns):
            continue
        out.append({"file": file, "section": section, "tokens": entry["tokens"], "n_names": len(names), "text": text})
    out.sort(key=lambda e: (e["file"], e["section"]))
    return out, len(sections)


def pick(product: str, eligible: list[dict]) -> list[dict]:
    order = list(eligible)
    random.Random(f"{SEED}|{product}").shuffle(order)
    picked: list[dict] = []
    used: set[str] = set()
    for allow_used_file in (False, True):
        for entry in order:
            if len(picked) == N_PER_PRODUCT:
                break
            if entry in picked or (entry["file"] in used and not allow_used_file):
                continue
            picked.append(entry)
            used.add(entry["file"])
    return picked


def main() -> int:
    status = 0
    for product in PROJECTS:
        min_names = MIN_NAMES
        eligible, n_sections = eligible_sections(product, min_names)
        picked = pick(product, eligible)
        if len(picked) < N_PER_PRODUCT:
            min_names = MIN_NAMES_SMALL_FOLDER
            eligible, n_sections = eligible_sections(product, min_names)
            picked = pick(product, eligible)
        folder = SEALED / product
        picks_path = folder / "picks.json"
        if picks_path.exists():
            print(f"[FAIL] {product}: picks.json exists; it is not replaced")
            status = 1
            continue
        if len(picked) < N_PER_PRODUCT:
            print(f"[FAIL] {product}: only {len(picked)} sections can be picked")
            status = 1
            continue
        guarded_mkdir(folder / "sections")
        guarded_mkdir(folder / "drafts")
        rows = []
        for number, entry in enumerate(picked, start=1):
            story_id = f"{PREFIX[product]}-{number:03d}"
            text = f"Source file: {entry['file']}\nSection: {entry['section']}\n\n{entry['text']}\n"
            guarded_write_text(folder / "sections" / f"{story_id}.md", text)
            rows.append({
                "id": story_id, "file": entry["file"], "section": entry["section"], "tokens": entry["tokens"],
                "n_names": entry["n_names"], "section_text_sha256": sha(entry["text"]),
            })
        body = json.dumps(
            {"seed": SEED, "product": product, "min_names": min_names, "picks": rows}, ensure_ascii=False, indent=1
        ) + "\n"
        guarded_write_text(picks_path, body)
        print(
            f"{product}: {n_sections} sections, limit {min_names} names, {len(eligible)} eligible in "
            f"{len({e['file'] for e in eligible})} files, "
            f"{len(picked)} picked from {len({e['file'] for e in picked})} files; picks.json sha256 {sha(body)[:16]}"
        )
    return status


if __name__ == "__main__":
    sys.exit(main())
