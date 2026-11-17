# ruff: noqa
# fmt: off
# (The copied lab definitions must stay equal to their lab source, so no tool reformats this file.)
"""Word search (BM25) with page fill: the search step of the tested configuration "s1_word_3000".

The search needs the documentation folder only: no vector store, no embedding model and no reranker.
For one user story it gives a passage block of at most 3,000 tokens:

  query      title, story sentence and acceptance criteria
  ranking    BM25 over the sections of the documentation (file name and text of each section)
  page fill  the token budget is filled page by page: the ranked sections of a page first, then its other
             sections in document order, with a limit for each page

Every definition below the line "COPIED CODE" is a copy, word for word, of scripts/tuning_lib/search.py, the
lab code that made the final test of 17 November 2026. Do not edit a copied definition here:
tests/unit/test_word_search.py compares each one with its lab source, and compares the blocks of this module
with the stored blocks of the lab, byte for byte.
"""

from __future__ import annotations

import logging
import re
import time
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from secreqgen.retrieval.passage_edits import (
    SEPARATOR,
    PassagesError,
    PromptError,
    drop_empty,
    render_markup,
    split_context,
)

logger = logging.getLogger(__name__)

DEFAULT_BUDGET = 3000  # tokens of documentation in one prompt (the tested value)

# ----------------------------------------------------------------------------- COPIED CODE
POOL = 50  # candidates after the merge
PAGE_CAP = 2000  # most tokens of one page in the block
STOP_AT = 50  # stop when fewer tokens than this are left in the budget
SHOWN_RELEVANCE = 0.50  # the number that each passage of the new search shows (see format_block)


class SearchError(Exception):
    """A search option, a stored search file or an entry of it is missing or damaged."""


@dataclass(frozen=True)
class Chunk:
    """One section (or one part of a long section) of a documentation page."""

    file: str  # path relative to the documentation folder
    section: str  # heading path, "A > B"; empty when the page has no heading
    text: str  # the text as the tool stores it (it starts with the "Section:" line)
    tokens: int  # cl100k tokens of the text
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


# ----------------------------------------------------------------------------- END OF COPIED CODE


@dataclass
class WordRetrievalResult:
    """The passages of the word search for one query.

    block is the text for the prompt. raw_block is the block before render_markup and drop_empty.
    """

    block: str
    raw_block: str
    query_text: str
    n_passages: int
    tokens: int
    files: list[str]
    retrieval_time_ms: float
    chunks: list[Chunk] = field(default_factory=list)

    @property
    def total_retrieved(self) -> int:
        return self.n_passages

    @property
    def above_threshold(self) -> int:
        return self.n_passages


class WordSearchRetriever:
    """The retriever of the tested configuration. It has the methods that the generator calls.

    Example:
        >>> retriever = WordSearchRetriever(Path("docs"), product="keycloak")
        >>> result = retriever.retrieve_for_user_story(user_story)
        >>> block = retriever.format_result_for_prompt(result)
    """

    def __init__(
        self,
        docs_dir: Path,
        *,
        product: Optional[str] = None,
        system_context_file: Optional[Path] = None,
        budget: int = DEFAULT_BUDGET,
        page_cap: int = PAGE_CAP,
    ) -> None:
        from rank_bm25 import BM25Okapi

        self.docs_dir = Path(docs_dir)
        if not self.docs_dir.is_dir():
            raise SearchError(f"the documentation folder does not exist: {self.docs_dir}")
        # The lab writes product names in small letters ("keycloak"). Any letter case is accepted here.
        self.product = product.strip().lower() if product and product.strip() else None
        self.system_context_file = Path(system_context_file) if system_context_file else None
        self.budget = budget
        self.page_cap = page_cap
        self.chunks: list[Chunk] = load_chunks(self.docs_dir)
        # Same index text as the lab: the file name without its ending, then the text of the section.
        self._bm25 = BM25Okapi([word_tokens(Path(c.file).stem + " " + c.text) for c in self.chunks])

    def word_order(self, query: str) -> list[int]:
        """Order of the word search (copy of SearchIndex.words of the lab)."""
        import numpy as np

        scores = self._bm25.get_scores(word_tokens(query, drop_stop=True))
        return [int(i) for i in np.argsort(-scores, kind="stable")]

    def retrieve(self, query: str, **_ignored: Any) -> WordRetrievalResult:
        """Select the passages for a query text. A search that selects nothing raises SearchError."""
        start = time.perf_counter()
        query_words = word_tokens(query, drop_stop=True)
        if not query_words or float(self._bm25.get_scores(query_words).max()) <= 0.0:
            # The order is then the order of the files. Say so: a silent fallback hides a search that found nothing.
            logger.warning(
                "word search: no word of the story is in the documentation; the passages are the first pages of the folder"
            )
        pool = [int(i) for i in self.word_order(query)[:POOL]]
        shown = shown_order(page_fill(pool, self.chunks, self.budget, self.page_cap), self.chunks)
        raw_block = format_block(self.chunks, shown)
        try:
            block = drop_empty(render_markup(raw_block, self.product))
            n_passages = len(split_context(block))
        except (PassagesError, PromptError) as exc:
            raise SearchError(f"no usable documentation text was left for this story: {exc}") from exc
        elapsed = (time.perf_counter() - start) * 1000
        logger.info("word search: %d passages, %d tokens", len(shown), sum(self.chunks[i].tokens for i in shown))
        return WordRetrievalResult(
            block=block,
            raw_block=raw_block,
            query_text=query,
            n_passages=n_passages,
            tokens=sum(self.chunks[i].tokens for i in shown),
            files=list(dict.fromkeys(self.chunks[i].file for i in shown)),
            retrieval_time_ms=elapsed,
            chunks=[self.chunks[i] for i in shown],
        )

    def retrieve_for_user_story(self, user_story: Any, top_k: Optional[int] = None) -> WordRetrievalResult:
        """Select the passages for a user story (query: title, story sentence, acceptance criteria)."""
        query = clean_query(user_story.title, user_story.story, list(user_story.acceptance_criteria))
        return self.retrieve(query)

    def get_system_context(self) -> Optional[str]:
        """The text of the system context file, or None when no file was given."""
        if self.system_context_file is None:
            return None
        return self.system_context_file.read_text(encoding="utf-8")

    def format_result_for_prompt(self, result: WordRetrievalResult) -> str:
        """The passage block for the prompt."""
        return result.block
