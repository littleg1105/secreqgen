"""Make the stored passages of the new search options (search test of 16 November 2026).

For each story of the three products with an answer key and for each search option of
scripts/tuning_lib/search.py, this script selects the documentation passages and stores them
below real_test_cases/tuning/_search/<option>/retrieval_frozen.json. The prompt lab reads these
files through the passage changes "search_<option>".

It calls no text-generation model. It uses the embedder and the reranker of the tool from the
local cache (no download) and rank_bm25. It keeps the embeddings in memory, so it does not touch
the vector store. It writes only through the write guard. The answer key is never part of a query:
the identifiers are used only after the selection, for the count that the script prints.

Run from coded_project/secreqgen:
  PYTHONPATH=src:scripts TOKENIZERS_PARALLELISM=false .venv/bin/python scripts/search_freeze.py
  ... --check   compare with the stored files and write nothing
"""

from __future__ import annotations

import argparse
import json
import os
import sys

os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

from tuning_lib import search as S
from tuning_lib.guard import assert_no_private_text, guarded_mkdir, guarded_write_text
from tuning_lib.passages import split_context
from tuning_lib.paths import PROJECTS, RTC, STUDY2
from tuning_lib.prompts import load_stories


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--options", default=",".join(S.OPTIONS))
    parser.add_argument("--check", action="store_true", help="compare with the stored files, write nothing")
    args = parser.parse_args(argv)
    names = [n for n in args.options.split(",") if n]
    for name in names:
        if name not in S.OPTIONS:
            print(f"[FAIL] unknown option {name!r}; known: {sorted(S.OPTIONS)}")
            return 2

    from sentence_transformers import CrossEncoder, SentenceTransformer

    embedder = SentenceTransformer(S.EMBEDDER_NAME)
    cross_encoder = CrossEncoder(S.RERANKER_NAME)
    stories = load_stories()
    entries: dict[str, dict[str, dict]] = {name: {} for name in names}
    found: dict[str, dict[str, int]] = {name: {p: 0 for p in PROJECTS} for name in names}
    needed = {p: 0 for p in PROJECTS}
    for project in PROJECTS:
        docs_dir = RTC / json.loads((STUDY2 / project / "stories.json").read_text(encoding="utf-8"))["docs_dir"]
        chunks = S.load_chunks(docs_dir)
        for chunk in chunks:  # no local path and no account name may be in a section
            assert_no_private_text(chunk.text, what=f"a section of {project}")
        index = S.SearchIndex(chunks, embedder, cross_encoder)
        print(f"{project}: {len(chunks)} sections, {len({c.file for c in chunks})} files")
        for story in stories.values():
            if story.project != project:
                continue
            query = S.clean_query(story.title, story.story, story.acceptance_criteria)
            ids = list(dict.fromkeys(story.identifiers))
            needed[project] += len(ids)
            for name in names:
                option = S.OPTIONS[name]
                shown = index.run(option, query)
                block = S.format_block(chunks, shown)
                passages = split_context(block)  # the block must have the form that the lab reads
                tokens = sum(chunks[i].tokens for i in shown)
                if tokens > option.budget:
                    print(f"[FAIL] {story.story_id} {name}: {tokens} tokens are above the budget")
                    return 1
                text = "\n".join(chunks[i].text for i in shown)
                found[name][project] += sum(1 for n in ids if n in text)  # after the selection only
                entries[name][story.story_id] = {
                    "project": project,
                    "story_text_sha256": S.sha_text(story.prompt_text()),
                    "query_sha256": S.sha_text(query),
                    "n_passages": len(passages),
                    "tokens": tokens,
                    "files": list(dict.fromkeys(chunks[i].file for i in shown)),
                    "doc_block_sha256": S.sha_text(block),
                    "doc_block": block,
                }
    status = 0
    for name in names:
        text = S.frozen_text(S.OPTIONS[name], entries[name])
        path = S.frozen_path(name)
        per = entries[name]
        n = len(per)
        total = sum(found[name].values())
        print(
            f"{name}: {n} stories; needed names in the selected text {total} of {sum(needed.values())} "
            f"({', '.join(f'{p} {found[name][p]} of {needed[p]}' for p in PROJECTS)}); "
            f"mean tokens {sum(e['tokens'] for e in per.values()) / n:.0f}; "
            f"mean passages {sum(e['n_passages'] for e in per.values()) / n:.1f}; "
            f"mean pages {sum(len(e['files']) for e in per.values()) / n:.1f}"
        )
        if args.check:
            same = path.is_file() and path.read_text(encoding="utf-8") == text
            print(f"  check against {path.name}: {'equal' if same else 'NOT EQUAL'}")
            status = status or (0 if same else 1)
        else:
            guarded_mkdir(path.parent)
            guarded_write_text(path, text)
            print(f"  written: tuning/_search/{name}/{path.name} (sha256 {S.sha_text(text)[:16]})")
    return status


if __name__ == "__main__":
    sys.exit(main())
