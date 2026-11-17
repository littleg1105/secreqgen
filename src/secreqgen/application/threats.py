"""Use case: write a draft threat list for each user story.

The stage functions do one job each. The command line calls them in its own
order and keeps its own messages. run_threat_lists calls the same stages in
order, for a GUI or an HTTP API. It writes files only when an output folder is
given. It has no default folder.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from secreqgen.application.clients import ClientFactory
from secreqgen.application.errors import InputError, ModelServerError, NothingToDoError
from secreqgen.application.events import ProgressCallback, ProgressEvent
from secreqgen.application.inputs import (
    build_user_stories,
    check_docs_folder,
    check_input_file,
    read_story_data,
)

if TYPE_CHECKING:
    from secreqgen.config import SecReqGenConfig
    from secreqgen.generation.schemas import UserStory


def threat_product_name(product: str | None, data: dict) -> str | None:
    """Return the product name.

    The option value wins. Without it, the key "project" of the file counts.
    A number becomes text. Any other value that is not text gives None.
    """
    value = product if product is not None else data.get("project")
    if value is not None and not isinstance(value, str):
        value = str(value) if isinstance(value, int | float) else None
    return value


def resolve_threat_models(
    cfg: SecReqGenConfig,
    writer_model: str | None,
    checker_model: str | None,
    check: bool,
) -> tuple[str, str | None]:
    """Return the writer model and the checker model (None: no check).

    A model name in the settings counts only when the user set it. The value
    that the settings have with no user input was never tested here. A
    ThreatListError passes through.
    """
    from secreqgen.threats import choose_models

    settings_model = (
        cfg.llm.model_name if "model_name" in cfg.llm.model_fields_set else None
    )
    return choose_models(writer_model, settings_model, checker_model, check)


def select_stories(stories: list[UserStory], story_id: str) -> list[UserStory]:
    """Return the stories with this id. An empty list means: not found."""
    return [s for s in stories if s.id == story_id]


def threat_folder_name(story_id: str, used_names: set[str]) -> str:
    """Return the folder name of a story and add it to used_names.

    Two story ids can give the same name ("A/1" and "A_1"). The second one
    gets a number.
    """
    base_name = re.sub(r"[^A-Za-z0-9._-]", "_", story_id).strip(".") or "story"
    name, n = base_name, 1
    while name in used_names:
        n += 1
        name = f"{base_name}_{n}"
    used_names.add(name)
    return name


def write_threat_files(folder: Path, model: dict) -> None:
    """Write threats.json and threats.md into the folder. An OSError passes through."""
    import json

    from secreqgen.threats import render_markdown

    folder.mkdir(parents=True, exist_ok=True)
    (folder / "threats.json").write_text(
        json.dumps(model, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (folder / "threats.md").write_text(render_markdown(model), encoding="utf-8")


@dataclass(frozen=True)
class ThreatListSummary:
    """The counts of one threat list."""

    rows: int
    attacks: int
    rules: int
    clean: int
    accepted: int
    dropped: int
    calls: int
    seconds: Any


def summarise_threat_list(model: dict) -> ThreatListSummary:
    """Count the rows of a threat list."""
    rows = model["rows"]
    attacks = sum(1 for r in rows if r["type"] == "attack")
    return ThreatListSummary(
        rows=len(rows),
        attacks=attacks,
        rules=len(rows) - attacks,
        clean=sum(1 for r in rows if not r.get("weak_signs")),
        accepted=sum(1 for r in rows if r.get("second_model") == "accepts"),
        dropped=len(model["dropped"]),
        calls=len(model["calls"]),
        seconds=model["seconds"],
    )


@dataclass
class ThreatOptions:
    """The options of run_threat_lists. Every path is explicit."""

    input_file: Path
    docs: Path
    product: str | None = None
    story_id: str | None = None
    labels: bool = True
    writer_model: str | None = None
    check: bool = True
    checker_model: str | None = None
    about: str | None = None  # the text itself, not a file
    output: Path | None = None  # None: write no file


@dataclass
class ThreatListOutcome:
    """The threat list of one story."""

    story_id: str
    model: dict
    summary: ThreatListSummary
    folder: Path | None = None


@dataclass
class ThreatRun:
    """The result of run_threat_lists."""

    writer: str
    checker: str | None
    lists: list[ThreatListOutcome] = field(default_factory=list)
    failures: list[tuple[str, Exception]] = field(default_factory=list)
    skipped: list[tuple[int, Exception]] = field(default_factory=list)


def _checked_client(make_client, cfg, model_name: str, what: str):
    """Build a client and check it. Raise ModelServerError when it is not ready."""
    client = make_client(cfg, tested_setup=True, model_name=model_name)
    if not client.check_health():
        raise ModelServerError(
            f"Ollama does not answer, or it does not have the {what} {model_name}.",
            model=model_name,
        )
    return client


def run_threat_lists(
    options: ThreatOptions,
    *,
    cfg: SecReqGenConfig | None = None,
    make_client: ClientFactory | None = None,
    on_progress: ProgressCallback | None = None,
) -> ThreatRun:
    """Run the whole threat list pipeline.

    It raises InputError, NothingToDoError or ModelServerError. Errors of the
    domain packages pass through, except the errors of one story: a
    ThreatListError, a SearchError or a GenerationError goes to the failures.
    No list gives an empty list, not an error.
    """
    from secreqgen.exceptions import GenerationError
    from secreqgen.retrieval.word_search import SearchError
    from secreqgen.threats import ThreatListError, ThreatListGenerator

    check_input_file(options.input_file)
    check_docs_folder(options.docs)
    try:
        data = read_story_data(
            options.input_file, encoding="utf-8", require_object=True
        )
    except Exception as e:
        raise InputError(f"Error parsing input file: {e}") from e

    stories, skipped = build_user_stories(data.get("user_stories", []) or [])
    if options.story_id is not None:
        stories = select_stories(stories, options.story_id)
        if not stories:
            raise InputError(
                f"Story not found in the input file: {options.story_id}"
            )
    if not stories:
        raise NothingToDoError("No user stories found in input file.")

    product = threat_product_name(options.product, data)
    if cfg is None:
        from secreqgen.config import get_config

        cfg = get_config()
    if make_client is None:
        from secreqgen.application.clients import build_ollama_client

        make_client = build_ollama_client

    writer, checker_name = resolve_threat_models(
        cfg, options.writer_model, options.checker_model, options.check
    )
    llm_client = _checked_client(make_client, cfg, writer, "model")
    checker = None
    if checker_name:
        checker = _checked_client(make_client, cfg, checker_name, "checker model")
    generator = ThreatListGenerator(
        llm_client,
        options.docs,
        product=product,
        name_labels=options.labels,
        checker=checker,
        about=options.about,
    )

    run = ThreatRun(writer=writer, checker=checker_name, skipped=skipped)
    used_names: set[str] = set()
    total = len(stories)
    for done, story in enumerate(stories, start=1):
        error: Exception | None = None
        try:
            model = generator.generate(story)
        except (ThreatListError, SearchError, GenerationError) as e:
            run.failures.append((story.id, e))
            error = e
        else:
            folder = None
            if options.output is not None:
                folder = options.output / threat_folder_name(story.id, used_names)
                write_threat_files(folder, model)
            run.lists.append(
                ThreatListOutcome(
                    story_id=story.id,
                    model=model,
                    summary=summarise_threat_list(model),
                    folder=folder,
                )
            )
        if on_progress is not None:
            on_progress(ProgressEvent(done, total, story.id, error))
    return run
