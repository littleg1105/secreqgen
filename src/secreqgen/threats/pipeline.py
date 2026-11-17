# ruff: noqa: E501
"""The threat list of one user story.

Method (tested on 17 November 2026, see the lab notes of that day):
1. The word search gives the passages of the story, as for the requirements.
2. The passages go to the model in small groups (GROUP_SIZE passages, one call for each group). A small prompt
   found about two times more security statements than one prompt with all passages.
   One more group holds sections of the search pool that name a risk and that the block does not show.
3. For each passage the model lists its security statements as exact quotes (an empty list is normal) and writes
   one row for each: type (attack or rule), threat, harm, the part of the story, requirement.
4. A script checks each row: the quote is in its passage, the passage fits the story, one row for one quote,
   the story part is a copy of words of the story. The harm is kept for an attack row only.
5. One call for a story names a threat actor and a STRIDE category for each row, from closed lists. They are the
   model's choice from the threat sentence. They have no source in the documentation.
6. A script looks for the signs of a weak row (a rule row whose quote has no rule word, a threat sentence with
   words that are not in the passage, a quote that is a fragment, no STRIDE category). Rows with no such sign come
   first on the page; no row is removed. (A second model call that asked "is this sentence about the story?" was
   tested and gave no gain on the local models, so it is not part of the tool.)
7. Optional: a second local model checks each row with the passage in view. It names a fault only when it is sure
   (the sentence is about a different feature, or the threat says something that the passage does not say).
   Rows with no fault come first on the page; no row is removed. In the test of 17 November 2026 (279 judged rows
   of the local models): gemma4:26b as the checker of qwen3.8:27b accepted 104 of 187 rows, 23 of them wrong
   (22 of 100), and refused 83, 42 of them wrong; it refused 30 of 91 good rows. qwen3.8:27b as the checker of
   gemma4:26b accepted 65 of 92 rows, 7 wrong (11 of 100), and refused 27, 12 wrong; it refused 13 of 49 good rows.
   A first, stricter prompt (three yes/no questions, all must be yes) refused 2 of 3 good rows.
8. Optional: one paragraph about the product (ABOUT_MAX_WORDS words at most) stands before the passages in the
   prompt of the writer. It is background and not a source: the prompt says so, and each row still needs a quote
   from a passage. The checker does not get the paragraph: its prompt is the same in each run.
   Test of 17 November 2026 (12 stories, local models, blind judges; in this test the paragraph was in both
   prompts): it did not lower the number of wrong rows (gemma4:26b: 7 of 49 rows wrong without it, 7 of 62 with
   it; qwen3.8:27b: 43 of 121 against 35 of 116, a difference that can be chance). gemma4:26b wrote more rows with
   it (62 against 49, of them 44 against 33 supported; a repeat run with no paragraph gave 49 again).
   Why the checker does not get it: the judged rows of that test were checked again, with and without the
   paragraph. qwen3.8:27b as the checker accepted 51 of 62 rows with it (6 wrong) and 44 without it (4 wrong): of
   the 7 rows that only the paragraph made it accept, 2 were wrong. gemma4:26b as the checker accepted 69 of 116
   rows with it (19 wrong) and 70 without it (21 wrong): no difference. A repeat of the check with the paragraph
   changed 2 of 62 and 7 of 116 verdicts.
"""

from __future__ import annotations

import logging
import re
import string
import time
from pathlib import Path
from typing import Any

from secreqgen.retrieval.passage_edits import (
    SEPARATOR,
    PassagesError,
    drop_empty,
    render_markup,
    split_context,
)
from secreqgen.retrieval.word_search import (
    POOL,
    WordSearchRetriever,
    clean_query,
    format_block,
)
from secreqgen.threats.actors import ACTORS, STRIDE
from secreqgen.threats.checks import (
    check_quote,
    extract_json,
    has_risk_word,
    keep_or_drop,
    normalise,
    weak_signs,
)

logger = logging.getLogger(__name__)

PROMPT_DIR = Path(__file__).parent / "prompts"
PROMPT_SET = "threats_v5b_labels"
GROUP_SIZE = 5  # passages in one call
EXTRA_SECTIONS = 5  # sections of the search pool with a risk word, in one more group
EXTRA_TOKENS = 1000
MAX_QUOTE_WORDS = 30  # the limit that the prompt names
QUOTE_LIMIT = 60  # a longer quote is refused by the script
ABOUT_MAX_WORDS = 120  # the optional paragraph about the product: one paragraph, no more
ABOUT_WRITER = (
    "## About the product\n"
    "This paragraph helps you to understand the product. It is not a source: do not quote it, "
    "and do not take a fact for a row from it.\n\n$about\n\n"
)
CHECK_PASSAGE_CHARS = 12000  # the most text of one passage in a check call (a passage has 2,000 tokens at most)
MAX_JSON_ATTEMPTS = 3
HARMS = (
    "disclosure of data",
    "change of data",
    "false identity",
    "access with no permission",
    "loss of service",
    "no record of actions",
)
DRAFT_NOTICE = (
    "DRAFT for a person to check. Each row comes from one sentence of the product documentation. "
    "The list holds only what the documentation states. It is not a complete threat model."
)


class ThreatListError(Exception):
    """The threat list of a story could not be made."""


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


_LABEL_LINE_RE = re.compile(r"(?m)^\W*(\d{1,3})\W{1,4}([A-Za-z_ \-]+?)(?:\s*[,;|/]\s*|\s+-\s+)\(?\s*(none|[STRIDE])[a-z]*\b", re.I)
_SOURCE_NAME_RE = re.compile(r"^\W*[Pp]\s*(\d+)\W*$")


def parse_label_lines(answer: str) -> dict[int, tuple[str, str]]:
    """The actor and the STRIDE value of each numbered line of an answer. A value that is not in a list is ""."""
    out: dict[int, tuple[str, str]] = {}
    for match in _LABEL_LINE_RE.finditer(answer or ""):
        actor = re.sub(r"[\s\-]+", "_", match.group(2).strip().lower())
        stride = match.group(3)
        stride = "none" if stride.lower() == "none" else stride.upper()
        out.setdefault(int(match.group(1)), (actor if actor in ACTORS else "", stride if stride == "none" or stride in STRIDE else ""))
    return out


def _source_name(value: Any) -> str:
    """The source name of an answer in the form of the prompt: "[P3]", "p3" and " P3 " all give "P3"."""
    text = _text(value)
    match = _SOURCE_NAME_RE.match(text)
    return f"P{int(match.group(1))}" if match else text


class ThreatListGenerator:
    """Write the threat list of a user story from the documentation folder of the product."""

    def __init__(
        self,
        llm_client: Any,
        docs_dir: Path,
        *,
        product: str | None = None,
        name_labels: bool = True,
        checker: Any = None,
        about: str | None = None,
    ) -> None:
        self.llm = llm_client
        self.name_labels = name_labels
        self.checker = checker  # a client of a second local model that checks each row, or None
        self.about = " ".join(about.split()) if about and about.strip() else None
        if self.about and len(self.about.split()) > ABOUT_MAX_WORDS:
            raise ThreatListError(
                f"the paragraph about the product has {len(self.about.split())} words; the limit is {ABOUT_MAX_WORDS}"
            )
        self.retriever = WordSearchRetriever(Path(docs_dir), product=product)
        try:
            self._system = (PROMPT_DIR / "system_prompt.txt").read_text(encoding="utf-8")
            self._user = string.Template((PROMPT_DIR / "user_prompt.txt").read_text(encoding="utf-8"))
            self._labels = string.Template((PROMPT_DIR / "labels_prompt.txt").read_text(encoding="utf-8"))
            self._check = string.Template((PROMPT_DIR / "check_prompt.txt").read_text(encoding="utf-8"))
        except OSError as exc:
            raise ThreatListError(f"a prompt file is missing: {exc}") from exc

    # ------------------------------------------------------------------ passages
    def passage_groups(self, user_story: Any) -> tuple[list[list[dict]], int]:
        """The passages of a story in groups. Returns (groups, number of passages in the extra group)."""
        query = clean_query(user_story.title, user_story.story, list(user_story.acceptance_criteria))
        result = self.retriever.retrieve(query)
        passages = [{"ref": f"P{p.index}", "header": p.source, "text": p.text} for p in split_context(result.block)]
        groups = [passages[i : i + GROUP_SIZE] for i in range(0, len(passages), GROUP_SIZE)]
        shown = {(c.file, c.section) for c in result.chunks}
        extra: list[int] = []
        used = 0
        for index in self.retriever.word_order(query)[:POOL]:
            chunk = self.retriever.chunks[int(index)]
            if (chunk.file, chunk.section) in shown or len(extra) >= EXTRA_SECTIONS:
                continue
            if not has_risk_word(chunk.text) or used + chunk.tokens > EXTRA_TOKENS:
                continue
            extra.append(int(index))
            used += chunk.tokens
        n_extra = 0
        if extra:
            try:
                block = drop_empty(render_markup(format_block(self.retriever.chunks, extra), self.retriever.product))
                more = split_context(block)
            except PassagesError:
                more = []
            if more:
                base = len(passages)
                groups.append([{"ref": f"P{base + p.index}", "header": p.source, "text": p.text} for p in more])
                n_extra = len(more)
        return groups, n_extra

    @staticmethod
    def _render_group(group: list[dict]) -> str:
        parts = []
        for p in group:
            text = p["text"]
            body = text.split("\n", 1)[1] if text.startswith("### ") and "\n" in text else text
            parts.append(f"### [{p['ref']}] {p['header']}\n{body}")
        return SEPARATOR.join(parts)

    # ------------------------------------------------------------------ labels
    def _name_labels(self, user_story: Any, rows: list[dict]) -> None:
        """One call for a story: the model chooses a threat actor and a STRIDE category for each row, from closed lists."""
        prompt = self._labels.substitute(
            story_title=user_story.title,
            story_text=user_story.story,
            threats="\n".join(f"{n}. {row['threat']}" for n, row in enumerate(rows, start=1)),
        )
        try:
            answer = self.llm.generate(prompt=prompt).content
        except Exception as exc:  # the list is still useful with no labels  # noqa: BLE001
            logger.warning("threat list: the label call failed (%s)", type(exc).__name__)
            return
        for number, (actor, stride) in parse_label_lines(answer).items():
            if 1 <= number <= len(rows):
                rows[number - 1]["actor"] = actor
                rows[number - 1]["stride"] = stride

    # ------------------------------------------------------------------ check by a second model
    def _check_rows(self, user_story: Any, rows: list[dict], passages: list[dict]) -> None:
        """One call of the second model for each row, with the passage in view: does it accept the row?"""
        by_ref = {p["ref"]: p for p in passages}
        criteria = "\n".join(f"- {c}" for c in user_story.acceptance_criteria)
        for row in rows:
            passage = by_ref.get(row["source"])
            if passage is None:
                continue
            text = passage["text"]
            body = text.split("\n", 1)[1] if text.startswith("### ") and "\n" in text else text
            prompt = self._check.substitute(
                story_title=user_story.title,
                story_text=user_story.story,
                acceptance_criteria=criteria,
                header=passage["header"],
                passage=body.strip()[:CHECK_PASSAGE_CHARS],
                quote=" ".join(row["quote"].split()),
                threat=row["threat"],
                requirement=row["requirement"],
            )
            try:
                row["second_model"] = keep_or_drop(self.checker.generate(prompt=prompt).content)
            except Exception as exc:  # the row stays, with no verdict  # noqa: BLE001
                logger.warning("threat list: the check call failed (%s)", type(exc).__name__)

    # ------------------------------------------------------------------ one story
    def generate(self, user_story: Any) -> dict:
        """The threat list of one user story, as a JSON-safe dict."""
        criteria = list(user_story.acceptance_criteria)
        criteria_lines = "\n".join(f"- {c}" for c in criteria)
        story_text = normalise(" ".join([user_story.title, user_story.story, *criteria]))
        groups, n_extra = self.passage_groups(user_story)
        start = time.time()
        rows: list[dict] = []
        dropped: list[dict] = []
        calls: list[dict] = []
        seen: set[str] = set()
        passages: list[dict] = []
        for number, group in enumerate(groups, start=1):
            texts = {p["ref"]: p["text"] for p in group}
            passages += [{**p, "group": number} for p in group]
            prompt = self._user.substitute(
                passages=self._render_group(group),
                story_title=user_story.title,
                story_text=user_story.story,
                acceptance_criteria=criteria_lines,
                max_quote_words=MAX_QUOTE_WORDS,
            )
            if self.about:
                prompt = string.Template(ABOUT_WRITER).substitute(about=self.about) + prompt
            answer = None
            attempts = 0
            t0 = time.time()
            for attempts in range(1, MAX_JSON_ATTEMPTS + 1):
                try:
                    response = self.llm.generate(prompt=prompt, system_prompt=self._system, format="json")
                    candidate = extract_json(response.content)
                except ValueError:
                    continue
                except Exception as exc:  # a failed call is a failed attempt; the other groups go on  # noqa: BLE001
                    logger.warning("threat list: a model call failed (%s)", type(exc).__name__)
                    continue
                if isinstance(candidate.get("rows"), list):  # an object with no row list is no answer
                    answer = candidate
                    break
            calls.append({"group": number, "passages": len(group), "attempts": attempts, "ok": answer is not None, "seconds": round(time.time() - t0, 1)})
            if answer is None:
                logger.warning("threat list: group %d of story %s gave no JSON answer", number, user_story.id)
                continue
            notes = answer.get("passages") if isinstance(answer.get("passages"), list) else []
            fits = {_source_name(n.get("source")): _text(n.get("fits_story")).lower() for n in notes if isinstance(n, dict)}
            answer_rows = answer.get("rows") if isinstance(answer.get("rows"), list) else []
            for position, row in enumerate(answer_rows, start=1):
                if not isinstance(row, dict):
                    continue
                source, quote = _source_name(row.get("source")), _text(row.get("quote"))
                why = None
                if not quote:
                    why = "no quote"
                elif source not in texts:
                    why = f"unknown source name {source!r}"
                elif fits.get(source) not in ("yes", "partly"):
                    why = f"the note of {source} says fits_story {fits.get(source)!r}"
                elif len(quote.split()) > QUOTE_LIMIT:
                    why = f"the quote has {len(quote.split())} words"
                elif not check_quote(quote, texts[source]):
                    why = "the quote is not in the passage"
                elif normalise(quote) in seen:
                    why = "a second row for the same quote"
                elif not _text(row.get("threat")):
                    why = "no threat sentence"
                elif not _text(row.get("requirement")):
                    why = "no requirement"
                if why:
                    dropped.append({"group": number, "row": position, "why": why, "content": row})
                    continue
                seen.add(normalise(quote))
                kind = "attack" if _text(row.get("type")).lower() == "attack" else "rule"
                harm = _text(row.get("harm")).lower()
                part = _text(row.get("story_part"))
                rows.append(
                    {
                        "id": f"T{len(rows) + 1}",
                        "type": kind,
                        "threat": _text(row.get("threat")),
                        # The harm of a rule row was right for 1 of 2 rows in the test: it is kept for an attack row only.
                        "harm": harm if kind == "attack" and harm in HARMS else "",
                        "story_part": part if part and normalise(part) in story_text else "",
                        "source": source,
                        "quote": quote,
                        "requirement": _text(row.get("requirement")),
                        "from_extra_group": bool(n_extra) and number == len(groups),
                        "actor": "",
                        "stride": "",
                        "weak_signs": [],
                        "second_model": "",
                    }
                )
        if self.name_labels and rows:
            self._name_labels(user_story, rows)
        if self.checker is not None and rows:
            self._check_rows(user_story, rows, passages)
        texts = {p["ref"]: p["text"] for p in passages}
        plain_story = " ".join([user_story.title, user_story.story, *criteria])
        for row in rows:
            row["weak_signs"] = weak_signs(row["type"], row["quote"], row["threat"], texts.get(row["source"], ""), plain_story, row["stride"])
        model_name = getattr(self.llm, "model_name", None)
        return {
            "draft_notice": DRAFT_NOTICE,
            "prompt_set": PROMPT_SET,
            "product": self.retriever.product,
            "model": model_name if isinstance(model_name, str) else None,
            "story": {"id": user_story.id, "title": user_story.title, "story": user_story.story, "acceptance_criteria": criteria},
            "seconds": round(time.time() - start),
            "calls": calls,
            "label_step": self.name_labels,
            "about": self.about,
            "checker": getattr(self.checker, "model_name", None) if self.checker is not None else None,
            "extra_passages": n_extra,
            "passages": passages,
            "rows": rows,
            "dropped": dropped,
        }
