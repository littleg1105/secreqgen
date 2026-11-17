"""Make the stored passages of the new search options for an outside product.

Same selection code as scripts/search_freeze.py (scripts/tuning_lib/search.py). The product comes
from the allow-list of scripts/tuning_lib/xproject.py. The stories come from
real_test_cases/<project>/user_stories.json. An outside product has no answer key, so this script
prints no count of needed names.

It calls no text-generation model. It uses the embedder and the reranker of the tool from the
local cache (no download) and rank_bm25. It keeps the embeddings in memory, so it does not touch
a vector store. It writes tuning/_search/<option>/retrieval_frozen_<project>.json through the
write guard (for the older stories of a product of the lab: retrieval_frozen_<project>_other_stories.json,
so the file of the lab does not change). It never replaces a file that has different content.

Run from coded_project/secreqgen:
  PYTHONPATH=src:scripts TOKENIZERS_PARALLELISM=false .venv/bin/python scripts/xproject_search_freeze.py --project mattermost
  ... --check   compare with the stored files and write nothing
"""

from __future__ import annotations

import argparse
import os
import sys

os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

from tuning_lib import search as S
from tuning_lib.guard import assert_no_private_text, guarded_mkdir, guarded_write_text
from tuning_lib.passages import split_context
from tuning_lib.paths import PROJECTS
from tuning_lib.xproject import ALLOWED_PRODUCTS, check_product_folders, load_xstories


def build_entries(project: str, names: list[str], embedder: object, cross_encoder: object) -> dict[str, dict[str, dict]]:
    """The entries of each option for each story of the product. Raises ValueError on a bad block."""
    item = check_product_folders(project)
    chunks = S.load_chunks(item.docs_dir)
    for chunk in chunks:  # no local path and no account name may be in a section
        assert_no_private_text(chunk.text, what=f"a section of {project}")
    index = S.SearchIndex(chunks, embedder, cross_encoder)
    print(f"{project}: {len(chunks)} sections, {len({c.file for c in chunks})} files")
    entries: dict[str, dict[str, dict]] = {name: {} for name in names}
    for story in load_xstories(project).values():
        query = S.clean_query(story.title, story.story, story.acceptance_criteria)
        for name in names:
            option = S.OPTIONS[name]
            shown = index.run(option, query)
            block = S.format_block(chunks, shown)
            passages = split_context(block)  # the block must have the form that the lab reads
            tokens = sum(chunks[i].tokens for i in shown)
            if tokens > option.budget:
                raise ValueError(f"{story.story_id} {name}: {tokens} tokens are above the budget")
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
    return entries


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--project", required=True, choices=sorted(ALLOWED_PRODUCTS))
    parser.add_argument("--options", default=",".join(S.OPTIONS))
    parser.add_argument("--check", action="store_true", help="compare with the stored files, write nothing")
    args = parser.parse_args(argv)
    in_lab = args.project in PROJECTS  # then these are the older stories of a lab product: a second file
    names = [n for n in args.options.split(",") if n]
    for name in names:
        if name not in S.OPTIONS:
            print(f"[FAIL] unknown option {name!r}; known: {sorted(S.OPTIONS)}")
            return 2

    from sentence_transformers import CrossEncoder, SentenceTransformer

    embedder = SentenceTransformer(S.EMBEDDER_NAME)
    cross_encoder = CrossEncoder(S.RERANKER_NAME)
    try:
        entries = build_entries(args.project, names, embedder, cross_encoder)
    except ValueError as exc:
        print(f"[FAIL] {exc}")
        return 1
    status = 0
    for name in names:
        per = entries[name]
        n = len(per)
        text = S.frozen_text(S.OPTIONS[name], per)
        path = S.other_stories_path(name, args.project) if in_lab else S.frozen_path(name, project=args.project)
        print(
            f"{name}: {n} stories; mean tokens {sum(e['tokens'] for e in per.values()) / n:.0f}; "
            f"mean passages {sum(e['n_passages'] for e in per.values()) / n:.1f}; "
            f"mean pages {sum(len(e['files']) for e in per.values()) / n:.1f}"
        )
        same = path.is_file() and path.read_text(encoding="utf-8") == text
        if args.check:
            print(f"  check against {path.name}: {'equal' if same else 'NOT EQUAL'}")
            status = status or (0 if same else 1)
        elif same:
            print(f"  already stored with the same content: {path.name}")
        elif path.exists():
            print(f"[FAIL] {path.name} exists with different content; it is not replaced")
            status = 1
        else:
            guarded_mkdir(path.parent)
            guarded_write_text(path, text)
            print(f"  written: tuning/_search/{name}/{path.name} (sha256 {S.sha_text(text)[:16]})")
    return status


if __name__ == "__main__":
    sys.exit(main())
