"""Shared code for the test on a product that the tuning never used (an "outside product").

The lab works on the three products of Study 2. This module lets the same prompt code work on one
more product. The stories come from real_test_cases/<project>/user_stories.json. The passage blocks
come from a frozen file below tuning/_xproject/. The module reads files and writes only below
tuning/ through the write guard. Importing it reads no file and uses no network.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from tuning_lib.guard import DEFAULT_GUARD, GuardError, WriteGuard, guarded_mkdir, guarded_write_text
from tuning_lib.passages import PassagesError, StoredContext, doc_passages, split_context
from tuning_lib.paths import (
    BLOCK_SOURCE_MODEL,
    DATA,
    EXTRA_MODELS,
    FALLBACK_CONTEXT,
    REPO_DIR_OVERRIDE,
    REPOS_ROOT,
    RTC,
    SCRIPTS,
    STUDY2,
    STUDY2B,
    TUNING_ROOT,
    model_slug,
)
from tuning_lib.search import strip_include_wrappers
from tuning_lib.prompts import (
    VERSIONS_ROOT,
    BuiltPrompt,
    PromptBuilder,
    PromptError,
    Story,
)

XPROJECT_ROOT: Path = TUNING_ROOT / "_xproject"
EVIDENCE_X_ROOT: Path = TUNING_ROOT / "_judging" / "evidence_x"
SPLITS_FILE: Path = SCRIPTS / "tuning_lib" / "splits.json"
FROZEN_NAME = "retrieval_frozen.json"
FROZEN_KEYS: tuple[str, ...] = ("context", "context_sha256", "n_chunks")
EVIDENCE_NOTE = (
    "This product has no fact sheet and no documentation excerpts in this file. "
    "Check each project-specific claim of an item by search: first the documentation folder, "
    "then the repository folder."
)


class XProjectError(PromptError):
    """Something is wrong with an outside product, its stories or its frozen passages."""


@dataclass(frozen=True)
class XProduct:
    """One allowed outside product: its documentation folder and its repository folder."""

    name: str
    docs_dir: Path
    repo_dir: Path


# The documentation folder equals PROJECTS["mattermost"][1] of run_cross_family_winning_config.py.
# scripts/xproject_retrieve.py checks that. This module does not import that heavy script.
ALLOWED_PRODUCTS: dict[str, XProduct] = {
    "mattermost": XProduct(
        name="mattermost",
        docs_dir=RTC / "mattermost" / "docs",
        repo_dir=REPOS_ROOT / REPO_DIR_OVERRIDE.get("mattermost", "mattermost"),
    ),
    # Added on 16 November 2026 for the second check of the search. The documentation folder
    # equals PROJECTS["gitlab"][1] of run_cross_family_winning_config.py.
    "gitlab": XProduct(
        name="gitlab",
        docs_dir=RTC / "gitlab" / "docs",
        repo_dir=REPOS_ROOT / REPO_DIR_OVERRIDE.get("gitlab", "gitlab"),
    ),
    # The three products of the lab, for their older stories (May 2026, real_test_cases/<product>/
    # user_stories.json). The lab never used these stories. The documentation folders are the
    # docs_dir values of study2/<product>/stories.json (scripts/xproject_search_freeze.py checks it).
    "keycloak": XProduct(
        name="keycloak",
        docs_dir=RTC / "extracted_docs_md" / "keycloak",
        repo_dir=REPOS_ROOT / REPO_DIR_OVERRIDE.get("keycloak", "keycloak"),
    ),
    "bitwarden": XProduct(
        name="bitwarden",
        docs_dir=RTC / "bitwarden-test-data",
        repo_dir=REPOS_ROOT / REPO_DIR_OVERRIDE.get("bitwarden", "bitwarden"),
    ),
    "mastodon": XProduct(
        name="mastodon",
        docs_dir=RTC / "mastodon" / "docs",
        repo_dir=REPOS_ROOT / REPO_DIR_OVERRIDE.get("mastodon", "mastodon"),
    ),
}


def product(project: str, allowed: Mapping[str, XProduct] | None = None) -> XProduct:
    """The allow-list entry of a product. An unknown product raises XProjectError."""
    table = ALLOWED_PRODUCTS if allowed is None else allowed
    if project not in table:
        raise XProjectError(f"product {project!r} is not allowed; allowed: {sorted(table)}")
    return table[project]


def check_product_folders(project: str, allowed: Mapping[str, XProduct] | None = None) -> XProduct:
    """Return the product after a check that its documentation and repository folders exist."""
    item = product(project, allowed)
    for label, folder in (("documentation", item.docs_dir), ("repository", item.repo_dir)):
        if not folder.is_dir():
            raise XProjectError(f"{project}: the {label} folder does not exist: {folder}")
    return item


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Stories
# ---------------------------------------------------------------------------


def lab_story_ids(splits_path: Path = SPLITS_FILE) -> frozenset[str]:
    """Ids of the development and hold-out stories of the lab (ids only)."""
    try:
        data = json.loads(splits_path.read_text(encoding="utf-8"))
        ids = [*data["dev"], *data["holdout"]]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise XProjectError(f"cannot read the split file: {type(exc).__name__}") from exc
    return frozenset(str(i) for i in ids)


def load_xstories(
    project: str,
    *,
    rtc_root: Path = RTC,
    allowed: Mapping[str, XProduct] | None = None,
    splits_path: Path = SPLITS_FILE,
) -> dict[str, Story]:
    """Read the stories of an outside product (read only). A lab story id is refused."""
    product(project, allowed)
    path = rtc_root / project / "user_stories.json"
    try:
        items = json.loads(path.read_text(encoding="utf-8"))["user_stories"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise XProjectError(f"cannot read the stories of {project}: {type(exc).__name__}") from exc
    banned = lab_story_ids(splits_path)
    out: dict[str, Story] = {}
    for item in items:
        try:
            story = Story(
                story_id=item["id"],
                project=project,
                title=item["title"],
                story=item["story"],
                acceptance_criteria=tuple(item["acceptance_criteria"]),
                identifiers=(),
            )
        except (KeyError, TypeError) as exc:
            raise XProjectError(f"a story of {project} has a missing key: {exc!r}") from exc
        if story.story_id in banned:
            raise XProjectError(f"story id {story.story_id} is a story of the lab; refused")
        if story.story_id in out:
            raise XProjectError(f"duplicate story id {story.story_id}")
        out[story.story_id] = story
    if not out:
        raise XProjectError(f"{project}: no story found")
    return out


# ---------------------------------------------------------------------------
# Frozen passage blocks
# ---------------------------------------------------------------------------


def block_model(model: str) -> str:
    """The model whose frozen block a generator model uses. An extra model has no block of its own."""
    return BLOCK_SOURCE_MODEL if model in EXTRA_MODELS else model


def frozen_path(project: str, model: str, xroot: Path = XPROJECT_ROOT) -> Path:
    """Path of the frozen passage file of one product and generator model."""
    return xroot / project / model_slug(model) / FROZEN_NAME


def read_xfrozen(
    project: str, model: str, story_id: str, xroot: Path = XPROJECT_ROOT
) -> StoredContext:
    """Read and check the frozen block of one story. Read only.

    Checks: the entry and its keys, the hash, not the 41-character fallback text, and at least
    one documentation passage after split_context.
    """
    path = frozen_path(project, model, xroot)
    try:
        frozen = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise XProjectError(f"frozen file not readable: {path} ({type(exc).__name__})") from exc
    if not isinstance(frozen, dict) or story_id not in frozen:
        raise XProjectError(f"no frozen block for story {story_id!r} in {path.name}")
    entry = frozen[story_id]
    try:
        text, sha, n_chunks = entry["context"], entry["context_sha256"], int(entry["n_chunks"])
    except (KeyError, TypeError, ValueError) as exc:
        raise XProjectError(f"frozen block of {story_id!r} is damaged: {exc!r}") from exc
    if not isinstance(text, str) or not text.strip():
        raise XProjectError(f"frozen block of {story_id!r} is empty")
    if text == FALLBACK_CONTEXT or len(text) <= len(FALLBACK_CONTEXT):
        raise XProjectError(f"frozen block of {story_id!r} is the fallback text (retrieval failed)")
    if _sha(text) != sha:
        raise XProjectError(f"frozen block of {story_id!r} does not match its hash")
    try:
        passages = split_context(text)
    except PassagesError as exc:
        raise XProjectError(f"frozen block of {story_id!r} is broken: {exc}") from exc
    if not doc_passages(passages):
        raise XProjectError(f"frozen block of {story_id!r} holds no documentation passage")
    return StoredContext(story_id=story_id, text=text, sha256=sha, n_chunks_stored=n_chunks)


# ---------------------------------------------------------------------------
# The prompt builder
# ---------------------------------------------------------------------------


class XProjectBuilder(PromptBuilder):
    """Builds prompts for an outside product, in the same way as PromptBuilder.

    Only the sources differ: the stories and the frozen blocks. The version folders, the passage
    changes, the system context and the checks of PromptBuilder.build are used as they are.
    """

    def __init__(
        self,
        projects: Sequence[str] = ("mattermost",),
        *,
        versions_root: Path = VERSIONS_ROOT,
        xroot: Path = XPROJECT_ROOT,
        rtc_root: Path = RTC,
        allowed: Mapping[str, XProduct] | None = None,
        splits_path: Path = SPLITS_FILE,
    ) -> None:
        super().__init__(versions_root=versions_root, study2_root=STUDY2, rtc_root=rtc_root)
        self.projects = tuple(projects)
        self.xroot = xroot
        self.allowed = allowed
        self.splits_path = splits_path

    def stories(self) -> dict[str, Story]:
        """All stories of the chosen products, loaded once."""
        if self._stories is None:
            loaded: dict[str, Story] = {}
            for project in self.projects:
                loaded.update(
                    load_xstories(
                        project, rtc_root=self.rtc_root, allowed=self.allowed,
                        splits_path=self.splits_path,
                    )
                )
            self._stories = loaded
        return self._stories

    def stored_block(self, story_id: str, model: str) -> str:
        """The frozen passage block of the story for the generator model.

        The wrapper lines that the extraction of the Keycloak documentation left around an included
        file hold a folder path of the machine that made the extraction. They are removed here, in
        the same way as the new search removes them before it reads a file. A block with no such
        line stays byte-equal (each block of mattermost and gitlab).
        """
        key = (story_id, model)
        if key not in self._blocks:
            project = self.story(story_id).project
            text = read_xfrozen(project, block_model(model), story_id, self.xroot).text
            self._blocks[key] = strip_include_wrappers(text)
        return self._blocks[key]

    def verify_v0_old(self, story_id: str, model: str) -> None:
        """Not available: an outside product has no stored prompt to compare with."""
        raise XProjectError("an outside product has no stored prompt; v0_old cannot be verified")


def build_x_prompt(builder: XProjectBuilder, story_id: str, model: str, version: str) -> BuiltPrompt:
    """Build one prompt. Same as builder.build; named for clarity in scripts."""
    return builder.build(story_id, model, version)


# ---------------------------------------------------------------------------
# The guard of the vector store folder
# ---------------------------------------------------------------------------


def vectordb_guard(tuning_root: Path = TUNING_ROOT, data_root: Path = DATA) -> WriteGuard:
    """A write guard that allows only tuning/ and refuses data/ and the registered data."""
    return WriteGuard(
        allowed_roots=(tuning_root,),
        forbidden_roots=(data_root, STUDY2, STUDY2B, REPOS_ROOT),
        forbidden_names=("study2", "study2b"),
    )


def check_persist_dir(
    path: Path, *, tuning_root: Path = TUNING_ROOT, data_root: Path = DATA
) -> Path:
    """Return the resolved folder for a new vector store, or raise GuardError.

    The resolved folder (symbolic links followed) must be inside tuning/ and must not be inside
    secreqgen/data/. Call this before any object of the vector store is made.
    """
    return vectordb_guard(tuning_root, data_root).check(path)


def persist_dir_of(project: str, xroot: Path = XPROJECT_ROOT) -> Path:
    """Folder of the new vector store of an outside product."""
    return xroot / project / "vectordb"


# ---------------------------------------------------------------------------
# Evidence files for the judges
# ---------------------------------------------------------------------------


def evidence_text(story: Story, item: XProduct, system_context: str) -> str:
    """The text of the evidence file of one story."""
    criteria = "\n".join(f"{n}. {ac}" for n, ac in enumerate(story.acceptance_criteria, start=1))
    return (
        f"# Evidence for story {story.story_id}\n\n"
        f"Product: {item.name}\n\n"
        f"Documentation folder: {item.docs_dir}\n\n"
        f"Repository folder: {item.repo_dir}\n\n"
        f"## Story block\n\n{story.story}\n\nAcceptance criteria:\n\n{criteria}\n\n"
        f"## System description\n\n```yaml\n{system_context.rstrip()}\n```\n\n"
        f"{EVIDENCE_NOTE}\n"
    )


def write_evidence_files(
    project: str,
    *,
    out_dir: Path = EVIDENCE_X_ROOT,
    rtc_root: Path = RTC,
    allowed: Mapping[str, XProduct] | None = None,
    splits_path: Path = SPLITS_FILE,
    guard: WriteGuard = DEFAULT_GUARD,
) -> list[Path]:
    """Write one evidence file for each story of the product. Each write goes through the guard."""
    item = check_product_folders(project, allowed)
    stories = load_xstories(project, rtc_root=rtc_root, allowed=allowed, splits_path=splits_path)
    try:
        context = (rtc_root / project / "system_context.yaml").read_text(encoding="utf-8")
    except OSError as exc:
        raise XProjectError(f"system context of {project!r} not readable: {exc}") from exc
    guarded_mkdir(out_dir, guard=guard)
    written = []
    for story_id in sorted(stories):
        text = evidence_text(stories[story_id], item, context)
        written.append(guarded_write_text(out_dir / f"{story_id}.md", text, guard=guard))
    return written


__all__ = [
    "ALLOWED_PRODUCTS", "EVIDENCE_X_ROOT", "GuardError", "XPROJECT_ROOT", "XProduct",
    "XProjectBuilder", "XProjectError", "block_model", "build_x_prompt", "check_persist_dir",
    "check_product_folders", "evidence_text", "frozen_path", "lab_story_ids", "load_xstories",
    "persist_dir_of", "product", "read_xfrozen", "vectordb_guard", "write_evidence_files",
]
