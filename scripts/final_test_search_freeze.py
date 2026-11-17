"""Final test: the passages of the word search for each story text of the sealed stories.

Same selection code as scripts/search_freeze.py (scripts/tuning_lib/search.py, option word_3000). It writes
real_test_cases/tuning/_search/word_3000/retrieval_frozen_<project>_final_test.json through the write guard,
never replaces a file with different content, and prints counts only.

Run from coded_project/secreqgen:
  PYTHONPATH=src:scripts TOKENIZERS_PARALLELISM=false .venv/bin/python scripts/final_test_search_freeze.py
"""

from __future__ import annotations

import json
import os
import sys

os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

from tuning_lib import search as S
from tuning_lib.final_test import load_final_stories, run_ids
from tuning_lib.guard import assert_no_private_text, guarded_mkdir, guarded_write_text
from tuning_lib.passages import split_context
from tuning_lib.paths import PROJECTS, RTC, STUDY2

OPTION = "word_3000"


def main() -> int:
    from sentence_transformers import CrossEncoder, SentenceTransformer

    embedder = SentenceTransformer(S.EMBEDDER_NAME)
    cross_encoder = CrossEncoder(S.RERANKER_NAME)
    option = S.OPTIONS[OPTION]
    status = 0
    for project in PROJECTS:
        docs_dir = RTC / json.loads((STUDY2 / project / "stories.json").read_text(encoding="utf-8"))["docs_dir"]
        chunks = S.load_chunks(docs_dir)
        for chunk in chunks:
            assert_no_private_text(chunk.text, what=f"a section of {project}")
        index = S.SearchIndex(chunks, embedder, cross_encoder)
        entries: dict[str, dict] = {}
        every = load_final_stories((project,))
        for story in (every[sid] for sid in run_ids(project)):  # only the stories of the run
            query = S.clean_query(story.title, story.story, story.acceptance_criteria)
            shown = index.run(option, query)
            block = S.format_block(chunks, shown)
            passages = split_context(block)
            tokens = sum(chunks[i].tokens for i in shown)
            if tokens > option.budget:
                print(f"[FAIL] {story.story_id}: {tokens} tokens are above the budget")
                return 1
            entries[story.story_id] = {
                "project": project, "story_text_sha256": S.sha_text(story.prompt_text()), "query_sha256": S.sha_text(query),
                "n_passages": len(passages), "tokens": tokens, "files": list(dict.fromkeys(chunks[i].file for i in shown)),
                "doc_block_sha256": S.sha_text(block), "doc_block": block,
            }
        text = S.frozen_text(option, entries)
        path = S.SEARCH_ROOT / OPTION / f"retrieval_frozen_{project}_final_test.json"
        n = len(entries)
        line = (f"{project}: {len(chunks)} sections; {n} story texts; mean tokens {sum(e['tokens'] for e in entries.values()) / n:.0f}; "
                f"mean passages {sum(e['n_passages'] for e in entries.values()) / n:.1f}")
        if path.is_file() and path.read_text(encoding="utf-8") == text:
            print(line + " | already stored with the same content")
        elif path.exists():
            print(f"[FAIL] {path.name} exists with different content; it is not replaced")
            status = 1
        else:
            guarded_mkdir(path.parent)
            guarded_write_text(path, text)
            print(line + f" | written {path.name} (sha256 {S.sha_text(text)[:16]})")
    return status


if __name__ == "__main__":
    sys.exit(main())
