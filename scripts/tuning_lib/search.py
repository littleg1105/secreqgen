"""A new search step for the documentation passages (search test of 16 November 2026).

The present tool gives the model the 5 best sections. This module selects sections in another
way and writes them in the same block format, so that the prompt lab can use them:

  query      title, story sentence and acceptance criteria (no keyword list)
  candidates search by meaning (embedder) and word search (BM25), merged by rank position
  reranker   optional (the cross-encoder of the tool)
  page fill  a token budget, filled page by page: the selected sections of a page first, then
             its other sections in document order, with a limit for each page

The selection logic is pure and has no model. `SearchIndex` holds the models and is used by
scripts/search_freeze.py, which stores the selected passages below tuning/_search/. The prompt
lab reads only these stored files. Importing this module reads no file and loads no model.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from tuning_lib.passages import SEPARATOR
from tuning_lib.paths import PROJECTS, TUNING_ROOT

SEARCH_ROOT: Path = TUNING_ROOT / "_search"
FROZEN_NAME = "retrieval_frozen.json"
EMBEDDER_NAME = "sentence-transformers/all-mpnet-base-v2"
RERANKER_NAME = "cross-encoder/ms-marco-MiniLM-L-6-v2"
RRF_K = 60  # the usual constant of reciprocal rank fusion; not tuned
CANDIDATES_EACH = 30  # candidates from each search before the merge
POOL = 50  # candidates after the merge
PAGE_CAP = 2000  # most tokens of one page in the block
STOP_AT = 50  # stop when fewer tokens than this are left in the budget
SHOWN_RELEVANCE = 0.50  # the number that each passage of the new search shows (see format_block)


class SearchError(Exception):
    """A search option, a stored search file or an entry of it is missing or damaged."""


@dataclass(frozen=True)
class SearchOption:
    """One configuration of the new search."""

    name: str
    dense: bool
    words: bool
    reranker: bool
    budget: int
    page_cap: int = PAGE_CAP


OPTIONS: dict[str, SearchOption] = {
    # word search only, no reranker
    "word_3000": SearchOption("word_3000", dense=False, words=True, reranker=False, budget=3000),
    # search by meaning and word search, merged, then the reranker
    "both_3000": SearchOption("both_3000", dense=True, words=True, reranker=True, budget=3000),
    "both_4500": SearchOption("both_4500", dense=True, words=True, reranker=True, budget=4500),
}


@dataclass(frozen=True)
class Chunk:
    """One section (or one part of a long section) of a documentation page."""

    file: str  # path relative to the documentation folder
    section: str  # heading path, "A > B"; empty when the page has no heading
    text: str  # the text as the tool stores it (it starts with the "Section:" line)
    tokens: int  # cl100k tokens of the text


# ---------------------------------------------------------------------------
# Query and word tokens
# ---------------------------------------------------------------------------

_STOP = frozenset(
    """a an the of to and or in on for with is are be as that this it its by at from so i we you they he she
want wants must should shall can could may might will would when if then not no do does did each all any which who whom
what how why where there their them his her our your my me us into than too very only also both more most such own same
other some these those has have had having been being was were am but nor over under again once here about against between
through during before after above below up down out off further""".split()
)
_TOKEN_RE = re.compile(r"[A-Za-z0-9]+(?:[._\-/:][A-Za-z0-9]+)*")
_CAMEL_RE = re.compile(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|[0-9]+")
_SPLIT_RE = re.compile(r"[._\-/:]")


def clean_query(title: str, story: str, criteria: Sequence[str]) -> str:
    """The query of the new search: title, story sentence and acceptance criteria."""
    return title + "\n" + story + "\n" + "; ".join(criteria)


def word_tokens(text: str, *, drop_stop: bool = False) -> list[str]:
    """Lower-case tokens for the word search. A code-like name stays whole and also gives its parts.

    `proxy-headers` gives proxy-headers, proxy, headers. `clientAuth` gives clientauth, client, auth.
    """
    out: list[str] = []
    for match in _TOKEN_RE.finditer(text):
        whole = match.group(0)
        low = whole.lower()
        out.append(low)
        parts = _SPLIT_RE.split(whole)
        extra: list[str] = []
        for part in parts:
            pieces = _CAMEL_RE.findall(part)
            if len(pieces) > 1:
                extra.extend(piece.lower() for piece in pieces)
            if len(parts) > 1:
                extra.append(part.lower())
        out.extend(token for token in extra if token and token != low)
    if drop_stop:
        out = [token for token in out if token not in _STOP]
    return out


# ---------------------------------------------------------------------------
# Selection (no model)
# ---------------------------------------------------------------------------


def rrf(lists: Sequence[Sequence[int]], k: int = RRF_K) -> list[int]:
    """Merge ranked lists by position (reciprocal rank fusion). Ties go to the lower index."""
    score: dict[int, float] = defaultdict(float)
    for ranked in lists:
        for position, index in enumerate(ranked, start=1):
            score[int(index)] += 1.0 / (k + position)
    return sorted(score, key=lambda index: (-score[index], index))


def candidates(
    option: SearchOption,
    dense_order: Sequence[int] | None,
    word_order: Sequence[int] | None,
) -> list[int]:
    """The candidate list of an option, before the reranker."""
    if option.dense and dense_order is None:
        raise SearchError(f"{option.name}: the option needs the order of the search by meaning")
    if option.words and word_order is None:
        raise SearchError(f"{option.name}: the option needs the order of the word search")
    if option.dense and option.words:
        merged = rrf([list(dense_order)[:CANDIDATES_EACH], list(word_order)[:CANDIDATES_EACH]])  # type: ignore[arg-type]
        return merged[:POOL]
    if option.words:
        return [int(i) for i in list(word_order)[:POOL]]  # type: ignore[arg-type]
    if option.dense:
        return [int(i) for i in list(dense_order)[:POOL]]  # type: ignore[arg-type]
    raise SearchError(f"{option.name}: the option uses no search")


def page_fill(
    order: Sequence[int], chunks: Sequence[Chunk], budget: int, page_cap: int = PAGE_CAP
) -> list[int]:
    """Fill a token budget page by page.

    Pages come in the order of their first section in `order`. For each page: its sections of
    `order` first (in that order), then its other sections in document order. A section that does
    not fit in the page limit or in the budget is left out, and the next one is tried.
    """
    if budget <= 0 or page_cap <= 0:
        raise SearchError("the budget and the page limit must be above zero")
    ranked = [int(i) for i in order]
    pages: list[str] = []
    for index in ranked:
        if chunks[index].file not in pages:
            pages.append(chunks[index].file)
    by_file: dict[str, list[int]] = defaultdict(list)
    for index, chunk in enumerate(chunks):
        by_file[chunk.file].append(index)
    selected: list[int] = []
    texts: set[str] = set()  # a section with the same text as a selected one is left out
    used = 0
    for page in pages:
        first = [i for i in ranked if chunks[i].file == page]
        rest = [i for i in by_file[page] if i not in first]
        page_used = 0
        for index in first + rest:
            size = chunks[index].tokens
            if page_used + size > page_cap or used + size > budget:
                continue
            if chunks[index].text in texts:
                continue
            selected.append(index)
            texts.add(chunks[index].text)
            used += size
            page_used += size
        if budget - used < STOP_AT:
            break
    return selected


def shown_order(selected: Sequence[int], chunks: Sequence[Chunk]) -> list[int]:
    """Order for the block: pages in the order of selection, sections of a page in document order."""
    pages: list[str] = []
    for index in selected:
        if chunks[index].file not in pages:
            pages.append(chunks[index].file)
    out: list[int] = []
    for page in pages:
        out.extend(sorted(i for i in selected if chunks[i].file == page))
    return out


def format_block(chunks: Sequence[Chunk], shown: Sequence[int]) -> str:
    """Write the passages in the block format of the tool (format_retrieved_context).

    The block format needs a number in "Relevance". The new search does not select by one
    similarity number: page fill adds sections that have a low similarity to the story and that
    are wanted all the same. So each passage shows the same number (SHOWN_RELEVANCE), and the
    model gets no order of importance from it. The file is shown with its path inside the
    documentation folder.
    """
    if not shown:
        raise SearchError("no passage was selected")
    parts = []
    for number, index in enumerate(shown, start=1):
        chunk = chunks[index]
        section = chunk.section if chunk.section else "General"
        parts.append(f"### [{number}] {chunk.file} > {section} (Relevance: {SHOWN_RELEVANCE:.2f})\n{chunk.text}")
    return SEPARATOR.join(parts)


def select(
    option: SearchOption,
    query: str,
    chunks: Sequence[Chunk],
    *,
    dense_order: Sequence[int] | None,
    word_order: Sequence[int] | None,
    rerank: Callable[[str, list[int]], list[int]] | None,
) -> list[int]:
    """Run one option: candidates, optional reranker, page fill, order for the block."""
    pool = candidates(option, dense_order, word_order)
    if option.reranker:
        if rerank is None:
            raise SearchError(f"{option.name}: the option needs a reranker")
        pool = rerank(query, pool)
    return shown_order(page_fill(pool, chunks, option.budget, option.page_cap), chunks)


# ---------------------------------------------------------------------------
# The index with the models (used by scripts/search_freeze.py only)
# ---------------------------------------------------------------------------


_INCLUDE_ONE_LINE_RE = re.compile(r'<div class="included"\s+path="[^"]*">')
_INCLUDE_PATH_RE = re.compile(r'path="[^"]*">')
_DIV_OPEN_RE = re.compile(r"<div\b")
_DIV_CLOSE = "</div>"


def strip_include_wrappers(text: str) -> str:
    """Remove the wrapper lines that the extraction of a documentation set left around an included file.

    The wrapper is `<div class="included" path="LOCAL PATH">` (on one line or on two) and its closing
    `</div>` line. It is no documentation text, and its path is the folder of the machine that made
    the extraction, so it must not go into a prompt. Other div elements stay as they are.
    """
    lines = text.split("\n")
    out: list[str] = []
    stack: list[str] = []
    k = 0
    while k < len(lines):
        line = lines[k]
        stripped = line.strip()
        two_lines = (
            stripped == '<div class="included"'
            and k + 1 < len(lines)
            and _INCLUDE_PATH_RE.fullmatch(lines[k + 1].strip()) is not None
        )
        if two_lines:
            stack.append("included")
            k += 2
            continue
        if _INCLUDE_ONE_LINE_RE.fullmatch(stripped):
            stack.append("included")
            k += 1
            continue
        if stripped == _DIV_CLOSE and stack and stack[-1] == "included":
            stack.pop()
            k += 1
            continue
        stack.extend("other" for _ in _DIV_OPEN_RE.findall(line))
        for _ in range(line.count(_DIV_CLOSE)):
            if stack and stack[-1] == "other":
                stack.pop()
        out.append(line)
        k += 1
    return "\n".join(out)


def load_chunks(docs_dir: Path) -> list[Chunk]:
    """Chunk each Markdown file of a documentation folder with the chunker of the tool (read only).

    Include wrappers are removed first (see strip_include_wrappers).
    """
    from secreqgen.ingestion.chunker import ArchitectureAwareChunker, Section

    chunker = ArchitectureAwareChunker()
    out: list[Chunk] = []
    for path in sorted(Path(docs_dir).rglob("*.md")):
        relative = path.relative_to(docs_dir).as_posix()
        content = chunker._strip_front_matter(path.read_text(encoding="utf-8", errors="replace"))
        content = strip_include_wrappers(content)
        if not content.strip():
            continue
        sections = chunker._parse_markdown_structure(content) or [Section("", 0, content, [])]
        for section in sections:
            for piece in chunker._chunk_section(section, relative, len(out)):
                out.append(
                    Chunk(
                        file=relative,
                        section=" > ".join(piece.section_hierarchy),
                        text=piece.content,
                        tokens=chunker.count_tokens(piece.content),
                    )
                )
    if not out:
        raise SearchError(f"no Markdown text below {docs_dir}")
    return out


class SearchIndex:
    """The sections of one product with their embeddings and their word index."""

    def __init__(self, chunks: Sequence[Chunk], embedder: Any, cross_encoder: Any) -> None:
        from rank_bm25 import BM25Okapi

        self.chunks = list(chunks)
        self._embedder = embedder
        self._cross_encoder = cross_encoder
        self._vectors = embedder.encode(
            [c.text for c in self.chunks], normalize_embeddings=True, batch_size=32, show_progress_bar=False
        )
        self._bm25 = BM25Okapi([word_tokens(Path(c.file).stem + " " + c.text) for c in self.chunks])

    def dense(self, query: str) -> tuple[list[int], dict[int, float]]:
        """Order of the search by meaning, and the similarity of each section."""
        import numpy as np

        vector = self._embedder.encode([query], normalize_embeddings=True, show_progress_bar=False)[0]
        with np.errstate(all="ignore"):
            sims = self._vectors @ vector
        order = [int(i) for i in np.argsort(-sims, kind="stable")]
        return order, {i: float(sims[i]) for i in range(len(self.chunks))}

    def words(self, query: str) -> list[int]:
        """Order of the word search."""
        import numpy as np

        scores = self._bm25.get_scores(word_tokens(query, drop_stop=True))
        return [int(i) for i in np.argsort(-scores, kind="stable")]

    def rerank(self, query: str, pool: list[int]) -> list[int]:
        """Order of the reranker for the candidates."""
        scores = self._cross_encoder.predict(
            [(query, self.chunks[i].text) for i in pool], show_progress_bar=False
        )
        return [i for i, _ in sorted(zip(pool, scores), key=lambda pair: -float(pair[1]))]

    def run(self, option: SearchOption, query: str) -> list[int]:
        """The shown sections of an option."""
        return select(
            option,
            query,
            self.chunks,
            dense_order=self.dense(query)[0] if option.dense else None,
            word_order=self.words(query) if option.words else None,
            rerank=self.rerank,
        )


# ---------------------------------------------------------------------------
# The stored search files
# ---------------------------------------------------------------------------


def sha_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


_PRODUCT_NAME_RE = re.compile(r"[a-z0-9_]+")


def frozen_name(project: str | None = None) -> str:
    """File name of the stored search of a product.

    The three products of the lab share one file. Each other product has its own file, so the
    file of the lab does not change when a product is added.
    """
    if project is None or project in PROJECTS:
        return FROZEN_NAME
    if not _PRODUCT_NAME_RE.fullmatch(project):
        raise SearchError(f"bad product name {project!r}")
    return f"retrieval_frozen_{project}.json"


def frozen_path(option: str, root: Path | None = None, project: str | None = None) -> Path:
    if option not in OPTIONS:
        raise SearchError(f"unknown search option {option!r}; known: {sorted(OPTIONS)}")
    return (root or SEARCH_ROOT) / option / frozen_name(project)


def other_stories_path(option: str, project: str, root: Path | None = None) -> Path:
    """File of the stored search for stories of a lab product that are not stories of the lab.

    The three products of the lab have older stories (May 2026) that the lab never used. Their
    passages go into this second file, so the file of the lab does not change.
    """
    if option not in OPTIONS:
        raise SearchError(f"unknown search option {option!r}; known: {sorted(OPTIONS)}")
    if project not in PROJECTS:
        raise SearchError(f"{project!r} is not a product of the lab; it has its own file")
    return (root or SEARCH_ROOT) / option / f"retrieval_frozen_{project}_other_stories.json"


def frozen_text(option: SearchOption, entries: Mapping[str, Mapping[str, Any]]) -> str:
    """The text of a stored search file."""
    data = {
        "option": asdict(option),
        "embedder": EMBEDDER_NAME,
        "reranker": RERANKER_NAME if option.reranker else None,
        "rrf_k": RRF_K,
        "candidates_each": CANDIDATES_EACH,
        "pool": POOL,
        "stories": dict(entries),
    }
    return json.dumps(data, ensure_ascii=False, indent=1) + "\n"


_CACHE: dict[Path, dict[str, Any]] = {}


def read_frozen(
    option: str, root: Path | None = None, project: str | None = None, *, path: Path | None = None
) -> dict[str, Any]:
    """Read a stored search file (read only, kept in memory). `path` names the file directly."""
    path = path or frozen_path(option, root, project)
    if path not in _CACHE:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise SearchError(
                f"the stored search of {option!r} is not readable ({type(exc).__name__}); "
                "run scripts/search_freeze.py first"
            ) from exc
        if not isinstance(data, dict) or not isinstance(data.get("stories"), dict):
            raise SearchError(f"the stored search of {option!r} has no stories")
        _CACHE[path] = data
    return _CACHE[path]


def frozen_entry(option: str, project: str, story_text: str, root: Path | None = None) -> dict[str, Any]:
    """The stored passages of a story. The story is found by the hash of its prompt text."""
    wanted = sha_text(story_text)
    stores = [read_frozen(option, root, project)["stories"]]
    if project in PROJECTS:
        # More story sets of a lab product (the older stories, the stories of the final test), if their
        # files were made: each file retrieval_frozen_<project>_<set>.json of the option folder.
        folder = (root or SEARCH_ROOT) / option
        for extra in sorted(folder.glob(f"retrieval_frozen_{project}_*.json")):
            stores.append(read_frozen(option, path=extra)["stories"])
    found = [
        entry
        for store in stores
        for entry in store.values()
        if entry.get("story_text_sha256") == wanted and entry.get("project") == project
    ]
    if len(found) != 1:
        raise SearchError(
            f"the stored search of {option!r} holds {len(found)} entries for this story of {project!r}"
        )
    entry = found[0]
    block = entry.get("doc_block")
    if not isinstance(block, str) or sha_text(block) != entry.get("doc_block_sha256"):
        raise SearchError(f"the stored search of {option!r} has a damaged block")
    return entry
