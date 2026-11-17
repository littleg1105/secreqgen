"""The final test: stories from the sealed folder, passages of the old search, and the arm with no documentation.

Only the sources differ from the lab: the stories come from real_test_cases/final_test/sealed/, the passage
blocks of the old search from real_test_cases/final_test/retrieval/. The prompt code, the versions and the
passage changes are those of the lab (frozen). The answer keys (Story.identifiers) never go into a prompt.
Importing this module reads no file.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Sequence
from pathlib import Path

from tuning_lib.passages import SEPARATOR, PassagesError, doc_passages, split_context
from tuning_lib.paths import FALLBACK_CONTEXT, FINAL_TEST_ROOT, PROJECTS
from tuning_lib.prompts import PromptError, Story
from tuning_lib.search import strip_include_wrappers
from tuning_lib.xproject import XProjectBuilder, frozen_path

SEALED: Path = FINAL_TEST_ROOT / "sealed"
RETRIEVAL_ROOT: Path = FINAL_TEST_ROOT / "retrieval"
ARMS: tuple[str, ...] = ("main", "no_doc")
# The one passage of the arm NEW-0 (analysis lock, final test, step 2, point 3).
NO_DOC_BLOCK = "### [1] none > none (Relevance: 0.50)\nNo documentation was found for this story."
BAREBONES_SUFFIX = "-B"
RUN_SET_FILE: Path = FINAL_TEST_ROOT / "run_stories.json"  # written one time by scripts/final_test_set.py
KINDS: tuple[str, ...] = ("standard", "barebones", "trivial", "new_feature")


def read_sealed(project: str, sealed: Path = SEALED) -> list[dict]:
    """The story objects of one product, as the seal holds them."""
    return json.loads((sealed / project / "stories.json").read_text(encoding="utf-8"))["stories"]


def load_final_stories(projects: Sequence[str] = PROJECTS, sealed: Path = SEALED) -> dict[str, Story]:
    """Each story text of the final test. A standard story gives two texts: itself and its barebones form."""
    out: dict[str, Story] = {}
    for project in projects:
        for item in read_sealed(project, sealed):
            ids = tuple(item["fact_sheet"]["identifiers"])
            out[item["id"]] = Story(
                story_id=item["id"], project=project, title=item["title"], story=item["story"],
                acceptance_criteria=tuple(item["acceptance_criteria"]), identifiers=ids,
            )
            if item["kind"] == "standard":
                bare_id = item["id"] + BAREBONES_SUFFIX
                out[bare_id] = Story(
                    story_id=bare_id, project=project, title="", story=item["barebones_story"],
                    acceptance_criteria=(), identifiers=ids,
                )
    return out


def kinds(projects: Sequence[str] = PROJECTS, sealed: Path = SEALED) -> dict[str, str]:
    """Story id -> kind (standard, barebones, trivial, new_feature)."""
    out: dict[str, str] = {}
    for project in projects:
        for item in read_sealed(project, sealed):
            out[item["id"]] = item["kind"]
            if item["kind"] == "standard":
                out[item["id"] + BAREBONES_SUFFIX] = "barebones"
    return out


def run_story_ids(project: str, removed: Sequence[str] = (), n_standard: int = 5, sealed: Path = SEALED) -> dict[str, list[str]]:
    """The story ids of one product that the run uses, by kind: the first n standard stories in pick order
    that were not removed, their barebones forms, and the trivial and new-feature stories that were not removed."""
    gone = set(removed)
    items = read_sealed(project, sealed)
    standard = [i["id"] for i in items if i["kind"] == "standard" and i["id"] not in gone][:n_standard]
    return {
        "standard": standard,
        "barebones": [s + BAREBONES_SUFFIX for s in standard],
        "trivial": [i["id"] for i in items if i["kind"] == "trivial" and i["id"] not in gone],
        "new_feature": [i["id"] for i in items if i["kind"] == "new_feature" and i["id"] not in gone],
    }


def read_run_set(path: Path = RUN_SET_FILE) -> dict[str, dict[str, list[str]]]:
    """Product -> kind -> story ids of the run (fixed after the key check of the outside raters)."""
    return json.loads(path.read_text(encoding="utf-8"))["stories"]


def run_ids(project: str, kinds_wanted: Sequence[str] = KINDS, path: Path = RUN_SET_FILE) -> list[str]:
    """The story ids of one product in the run, for the given kinds."""
    per = read_run_set(path)[project]
    return [sid for kind in kinds_wanted for sid in per[kind]]


_HEADER_NUMBER_RE = re.compile(r"^### \[(\d+)\] ")


def old_search_block(project: str, model: str, story_id: str, root: Path = RETRIEVAL_ROOT) -> tuple[str, bool]:
    """The passage block of the old search for one story text, and True when it holds documentation.

    The old search can find no documentation passage for a story (nothing passes its cut). The old tool then puts
    its fallback sentence into the prompt. The prompt code of the lab needs a block in passage form, so for this
    case the block is the one fixed line of NEW-0 ("No documentation was found for this story."), followed by the
    code-search hits of the old search, if it gave any. The story stays in the test (analysis lock, final test).
    """
    path = frozen_path(project, model, root)
    try:
        entry = json.loads(path.read_text(encoding="utf-8"))[story_id]
        text, sha = entry["context"], entry["context_sha256"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise PromptError(f"no block of the old search for {story_id} in {path.name}: {type(exc).__name__}") from exc
    if not isinstance(text, str) or hashlib.sha256(text.encode("utf-8")).hexdigest() != sha:
        raise PromptError(f"the block of the old search for {story_id} does not match its hash")
    if text == FALLBACK_CONTEXT or len(text) <= len(FALLBACK_CONTEXT):
        return NO_DOC_BLOCK, False
    try:
        passages = split_context(text)
    except PassagesError as exc:
        raise PromptError(f"the block of the old search for {story_id} is broken: {exc}") from exc
    if doc_passages(passages):
        return strip_include_wrappers(text), True
    parts = [NO_DOC_BLOCK]
    for number, passage in enumerate(passages, start=2):  # code-search hits only
        renumbered, n = _HEADER_NUMBER_RE.subn(f"### [{number}] ", passage.text, count=1)
        if n != 1:
            raise PromptError(f"a code-search hit of {story_id} has no numbered header line")
        parts.append(renumbered)
    return SEPARATOR.join(parts), False


class FinalTestBuilder(XProjectBuilder):
    """Builds the prompts of the final test with the lab code.

    arm "main": the stored block is the passage block of the old search for the story (the version decides
    what the prompt shows: v0_old shows it, s1_word_3000 puts the passages of the word search in its place).
    arm "no_doc": the stored block is the one fixed line of NEW-0.
    """

    def __init__(self, projects: Sequence[str], arm: str = "main", *, retrieval_root: Path = RETRIEVAL_ROOT, sealed: Path = SEALED) -> None:
        if arm not in ARMS:
            raise PromptError(f"unknown arm {arm!r}; known: {ARMS}")
        super().__init__(tuple(projects), xroot=retrieval_root)
        self.arm = arm
        self.sealed = sealed

    def stories(self) -> dict[str, Story]:
        if self._stories is None:
            self._stories = load_final_stories(self.projects, self.sealed)
        return self._stories

    def stored_block(self, story_id: str, model: str) -> str:
        if self.arm == "no_doc":
            return NO_DOC_BLOCK
        key = (story_id, model)
        if key not in self._blocks:
            project = self.story(story_id).project
            self._blocks[key] = old_search_block(project, model, story_id, self.xroot)[0]
        return self._blocks[key]


__all__ = ["ARMS", "BAREBONES_SUFFIX", "FinalTestBuilder", "KINDS", "NO_DOC_BLOCK", "RETRIEVAL_ROOT", "RUN_SET_FILE",
           "SEALED", "kinds", "load_final_stories", "old_search_block", "read_run_set", "read_sealed", "run_ids",
           "run_story_ids"]
