"""Use case: write a draft threat list for each user story.

The stage functions do one job each. The command line calls them in its own
order and keeps its own messages. run_threat_lists calls the same stages in
order, for a GUI or an HTTP API. It writes files only when an output folder is
given. It has no default folder.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from secreqgen.application.clients import ClientFactory
from secreqgen.application.errors import InputError, ModelServerError, NothingToDoError
from secreqgen.application.events import ProgressCallback, ProgressEvent
from secreqgen.application.inputs import (
    build_user_stories,
    check_about,
    check_docs_folder,
    check_input_file,
    read_story_data,
)

if TYPE_CHECKING:
    from secreqgen.application.steps import StepCallback
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
    # Options for a GUI. Each default keeps the behaviour of the command line.
    strict_input: bool = False  # errors of the input become InputError
    keep_finished: bool = False  # a story that raises ends the run, not the result
    story_ids: tuple[str, ...] | None = None  # more than one story, in file order
    prompts_dir: Path | None = None  # a folder of user prompt files
    # Other file types in the documentation folder (outside the tested setup).
    other_formats: bool = False
    docs_cache: Path | None = None  # a folder that keeps the mirror between runs


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
    stopped: bool = False  # the stop request ended the run
    aborted: Exception | None = None  # the error that ended the run
    not_run: list[str] = field(default_factory=list)  # ids of stories not started
    docs_report: Any = None  # the MirrorReport of converted files, else None


def _checked_client(make_client, cfg, model_name: str, what: str):
    """Build a client and check it. Raise ModelServerError when it is not ready."""
    client = make_client(cfg, tested_setup=True, model_name=model_name)
    if not client.check_health():
        raise ModelServerError(
            f"Ollama does not answer, or it does not have the {what} {model_name}.",
            model=model_name,
        )
    return client


def _require_healthy(client, model_name: str, what: str) -> None:
    """Raise ModelServerError when the client is not ready."""
    if not client.check_health():
        raise ModelServerError(
            f"Ollama does not answer, or it does not have the {what} {model_name}.",
            model=model_name,
        )


def _select_story_ids(stories: list, story_ids: tuple[str, ...]) -> list:
    """Return the stories with these ids, in file order. An unknown id is an error."""
    known = {story.id for story in stories}
    for story_id in story_ids:
        if story_id not in known:
            raise InputError(f"Story not found in the input file: {story_id}")
    wanted = set(story_ids)
    return [story for story in stories if story.id in wanted]


def _check_output_folder(folder: Path) -> None:
    """Make the output folder and test it with one small file."""
    try:
        folder.mkdir(parents=True, exist_ok=True)
        probe = folder / ".write_test"
        probe.write_text("test", encoding="utf-8")
        probe.unlink()
    except OSError as e:
        raise InputError(
            f"Cannot write to the output folder: {folder}: {e.strerror or e}"
        ) from e


def _build_generator(
    options, llm_client, checker, product, about, strict, docs=None
):
    """Build the threat list generator. A user prompt folder gives a subclass.

    docs is the documentation folder to search. None means options.docs.
    """
    from secreqgen.retrieval.word_search import SearchError
    from secreqgen.threats import ThreatListError, ThreatListGenerator

    docs = options.docs if docs is None else docs
    arguments = {
        "product": product,
        "name_labels": options.labels,
        "checker": checker,
        "about": about,
    }
    try:
        if options.prompts_dir is None:
            return ThreatListGenerator(llm_client, docs, **arguments)
        from secreqgen.application.prompt_sets import build_threat_generator_class

        generator_class = build_threat_generator_class()
        return generator_class(
            llm_client,
            docs,
            prompts_dir=options.prompts_dir,
            prompt_label=f"user:{options.prompts_dir.name}",
            **arguments,
        )
    except (ThreatListError, SearchError) as e:
        if not strict:
            raise
        raise InputError(str(e)) from e


def _all_calls_failed(model: dict) -> bool:
    """True when the story has model calls and none of them gave a valid answer."""
    calls = model.get("calls") or []
    return bool(calls) and not any(call.get("ok") for call in calls)


def _server_answers(client) -> bool:
    """True when the client reports a healthy model server. It never raises."""
    try:
        return bool(client.check_health())
    except Exception:
        return False


def run_threat_lists(
    options: ThreatOptions,
    *,
    cfg: SecReqGenConfig | None = None,
    make_client: ClientFactory | None = None,
    on_progress: ProgressCallback | None = None,
    should_stop: Callable[[], bool] | None = None,
    on_story_start: Callable[[str, int, int], None] | None = None,
    on_step: StepCallback | None = None,
) -> ThreatRun:
    """Run the whole threat list pipeline.

    It raises InputError, NothingToDoError or ModelServerError. Errors of the
    domain packages pass through, except the errors of one story: a
    ThreatListError, a SearchError or a GenerationError goes to the failures.
    No list gives an empty list, not an error.

    The options strict_input and keep_finished are for a GUI. With
    strict_input, the input errors before the first story are InputError. With
    keep_finished, an error inside the story loop ends the loop and goes to
    run.aborted, and the lists that are done stay in the result. should_stop is
    asked before each story. on_story_start runs before each story. With
    on_step, the steps inside each story go to the callback as StepEvent.
    """
    from secreqgen.exceptions import GenerationError
    from secreqgen.retrieval.word_search import SearchError
    from secreqgen.threats import ThreatListError

    strict = options.strict_input
    if options.story_id is not None and options.story_ids is not None:
        raise InputError("Use story_id or story_ids, not both.")
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
    if options.story_ids is not None:
        stories = _select_story_ids(stories, options.story_ids)
    if not stories:
        raise NothingToDoError("No user stories found in input file.")

    product = threat_product_name(options.product, data)
    if cfg is None:
        from secreqgen.config import get_config

        cfg = get_config()
    if make_client is None:
        from secreqgen.application.clients import build_ollama_client

        make_client = build_ollama_client

    prepared = None
    if options.other_formats and options.docs is not None:
        from secreqgen.application.documentation import prepare_documentation

        prepared = prepare_documentation(
            options.docs,
            other_formats=True,
            cache_dir=options.docs_cache,
        )
    docs = options.docs if prepared is None else prepared.folder
    try:
        about = options.about
        if options.prompts_dir is not None:
            from secreqgen.application.prompt_sets import check_prompt_folder

            check_prompt_folder("threats", options.prompts_dir)
        try:
            writer, checker_name = resolve_threat_models(
                cfg, options.writer_model, options.checker_model, options.check
            )
        except ThreatListError as e:
            if not strict:
                raise
            raise InputError(str(e)) from e
        if strict:
            # The cheap input checks come before the checks of the model server.
            about = check_about(options.about)
            if options.output is not None:
                _check_output_folder(options.output)
            llm_client = make_client(cfg, tested_setup=True, model_name=writer)
            checker = None
            if checker_name:
                checker = make_client(
                    cfg, tested_setup=True, model_name=checker_name
                )
            generator = _build_generator(
                options, llm_client, checker, product, about, strict, docs
            )
            _require_healthy(llm_client, writer, "model")
            if checker is not None:
                _require_healthy(checker, checker_name, "checker model")
        else:
            llm_client = _checked_client(make_client, cfg, writer, "model")
            checker = None
            if checker_name:
                checker = _checked_client(
                    make_client, cfg, checker_name, "checker model"
                )
            generator = _build_generator(
                options, llm_client, checker, product, about, strict, docs
            )

        if on_step is not None:
            from secreqgen.application.steps import watch_threat_generator

            generator = watch_threat_generator(generator, on_step)

        run = ThreatRun(
            writer=writer,
            checker=checker_name,
            skipped=skipped,
            docs_report=None if prepared is None else prepared.report,
        )
        used_names: set[str] = set()
        total = len(stories)
        for done, story in enumerate(stories, start=1):
            if should_stop is not None and should_stop():
                run.stopped = True
                run.not_run = [s.id for s in stories[done - 1 :]]
                break
            if on_story_start is not None:
                on_story_start(story.id, done, total)
            error: Exception | None = None
            outcome: ThreatListOutcome | None = None
            try:
                try:
                    model = generator.generate(story)
                except (ThreatListError, SearchError, GenerationError) as e:
                    run.failures.append((story.id, e))
                    error = e
                else:
                    folder = None
                    if options.output is not None:
                        name = threat_folder_name(story.id, used_names)
                        folder = options.output / name
                        write_threat_files(folder, model)
                    outcome = ThreatListOutcome(
                        story_id=story.id,
                        model=model,
                        summary=summarise_threat_list(model),
                        folder=folder,
                    )
                    run.lists.append(outcome)
            except Exception as e:
                if not options.keep_finished:
                    raise
                # The story that raised counts as failed. The rest did not run.
                run.failures.append((story.id, e))
                run.aborted = e
                run.not_run = [s.id for s in stories[done:]]
                if on_progress is not None:
                    on_progress(ProgressEvent(done, total, story.id, e))
                break
            if on_progress is not None:
                on_progress(ProgressEvent(done, total, story.id, error, outcome))
            if (
                options.keep_finished
                and outcome is not None
                and _all_calls_failed(outcome.model)
                and not _server_answers(llm_client)
            ):
                run.aborted = ModelServerError(
                    f"The model server stopped answering during story {story.id}.",
                    model=writer,
                )
                run.not_run = [s.id for s in stories[done:]]
                break
        return run
    finally:
        if prepared is not None:
            prepared.close()
