"""Use case: generate security requirements from user stories.

The stage functions do one job each. The command line calls them in its own
order and keeps its own messages. run_requirements calls the same stages in
order, for a GUI or an HTTP API. It writes no file.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from secreqgen.application.clients import ClientFactory
from secreqgen.application.errors import InputError, ModelServerError, NothingToDoError
from secreqgen.application.events import ProgressCallback, ProgressEvent
from secreqgen.application.inputs import (
    build_user_stories,
    check_docs_option,
    check_input_file,
    read_story_data,
)

if TYPE_CHECKING:
    from secreqgen.application.steps import StepCallback
    from secreqgen.config import SecReqGenConfig
    from secreqgen.generation import SecurityRequirementsGenerator
    from secreqgen.generation.schemas import UserStory
    from secreqgen.output import OutputFormat


def requirements_format(name: str, *, allow_csv: bool) -> OutputFormat | None:
    """Return the output format of a name, or None when the name is unknown."""
    from secreqgen.output import OutputFormat

    format_map = {
        "json": OutputFormat.JSON,
        "yaml": OutputFormat.YAML,
        "markdown": OutputFormat.MARKDOWN,
        "md": OutputFormat.MARKDOWN,
    }
    if allow_csv:
        format_map["csv"] = OutputFormat.CSV
    return format_map.get(name.lower())


def build_requirements_retriever(
    cfg,
    mode,
    *,
    docs: Path | None = None,
    product: str | None = None,
    system_context_file: Path | None = None,
):
    """Build the retriever of a mode.

    RAG with a documentation folder gives the word search. RAG with no folder
    gives the dense retriever. Zero-shot gives None.
    """
    from secreqgen.application.retrievers import (
        build_vector_retriever,
        build_word_retriever,
    )
    from secreqgen.generation import GenerationMode

    if mode == GenerationMode.RAG and docs is not None:
        return build_word_retriever(docs, product, system_context_file)
    if mode == GenerationMode.RAG:
        return build_vector_retriever(cfg)
    return None


def requirements_prompt_builder(docs: Path | None):
    """Build the prompt builder. With a documentation folder it is the tested set."""
    from secreqgen.generation import SecurityRequirementsPromptBuilder

    prompts_dir = None
    if docs is not None:
        from secreqgen.generation.prompt_sets import TESTED_PROMPT_SET

        prompts_dir = TESTED_PROMPT_SET
    return SecurityRequirementsPromptBuilder(prompts_dir=prompts_dir)


def build_requirements_generator(llm_client, retriever, prompt_builder):
    """Build the generator of the requirements."""
    from secreqgen.generation import SecurityRequirementsGenerator

    return SecurityRequirementsGenerator(
        llm_client=llm_client,
        retriever=retriever,
        prompt_builder=prompt_builder,
    )


def generate_for_stories(
    generator: SecurityRequirementsGenerator,
    stories: list[UserStory],
    mode,
    system_context: str | None,
    on_progress: ProgressCallback | None = None,
) -> tuple[list, list[tuple[str, Exception]]]:
    """Generate the requirements of each story.

    A GenerationError or a SearchError of one story goes to the failures. An
    LLMConnectionError passes through. on_progress runs after each story.
    """
    from secreqgen.exceptions import GenerationError
    from secreqgen.retrieval.word_search import SearchError

    results: list = []
    failures: list[tuple[str, Exception]] = []
    total = len(stories)
    for done, story in enumerate(stories, start=1):
        error: Exception | None = None
        try:
            result = generator.generate(
                user_story=story,
                mode=mode,
                system_context=system_context,
            )
            results.append(result)
        except (GenerationError, SearchError) as e:
            failures.append((story.id, e))
            error = e
        if on_progress is not None:
            on_progress(ProgressEvent(done, total, story.id, error))
    return results, failures


@dataclass
class StoryLoop:
    """The result of generate_stories_stoppable."""

    results: list
    failures: list[tuple[str, Exception]]
    stopped: bool = False
    aborted: Exception | None = None
    not_run: list[str] = field(default_factory=list)


def generate_stories_stoppable(
    generator: SecurityRequirementsGenerator,
    stories: list[UserStory],
    mode,
    system_context: str | None,
    on_progress: ProgressCallback | None = None,
    *,
    on_story_start: Callable[[str, int, int], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
    keep_finished: bool = False,
) -> StoryLoop:
    """Generate the requirements of each story, for a GUI.

    It works like generate_for_stories, with three additions. should_stop is
    asked before each story. on_story_start runs before each story. With
    keep_finished, any other error of a story ends the loop and goes to
    aborted. The finished results stay in the result. Without keep_finished,
    that error is raised.
    """
    from secreqgen.exceptions import GenerationError
    from secreqgen.retrieval.word_search import SearchError

    loop = StoryLoop(results=[], failures=[])
    total = len(stories)
    for done, story in enumerate(stories, start=1):
        if should_stop is not None and should_stop():
            loop.stopped = True
            loop.not_run = [s.id for s in stories[done - 1 :]]
            break
        if on_story_start is not None:
            on_story_start(story.id, done, total)
        error: Exception | None = None
        result = None
        try:
            result = generator.generate(
                user_story=story,
                mode=mode,
                system_context=system_context,
            )
            loop.results.append(result)
        except (GenerationError, SearchError) as e:
            loop.failures.append((story.id, e))
            error = e
            result = None
        except Exception as e:
            if not keep_finished:
                raise
            # The story that raised counts as failed. The rest did not run.
            loop.failures.append((story.id, e))
            loop.aborted = e
            loop.not_run = [s.id for s in stories[done:]]
            if on_progress is not None:
                on_progress(ProgressEvent(done, total, story.id, e))
            break
        if on_progress is not None:
            on_progress(ProgressEvent(done, total, story.id, error, result))
    return loop


def format_requirements(results: list, output_format: OutputFormat) -> str:
    """Format one result, or a batch of results, as text."""
    from secreqgen.output import RequirementsFormatter

    formatter = RequirementsFormatter()
    if len(results) == 1:
        return formatter.format(results[0], output_format)
    return formatter.format_batch(results, output_format)


@dataclass
class RequirementsOptions:
    """The options of run_requirements. Every path is explicit."""

    input_file: Path
    docs: Path | None = None
    product: str | None = None
    baseline: bool = False
    system_context: str | None = None
    output_format: str = "markdown"
    # Options for a GUI. Each default keeps the behaviour of today.
    strict_input: bool = False  # errors of the input become InputError
    keep_finished: bool = False  # a story that raises ends the loop, not the result
    model: str | None = None  # None: the model of the settings
    prompts_dir: Path | None = None  # a folder of user prompt files
    story_ids: tuple[str, ...] | None = None  # these stories only, in file order


@dataclass
class RequirementsRun:
    """The result of run_requirements."""

    results: list = field(default_factory=list)
    failures: list[tuple[str, Exception]] = field(default_factory=list)
    skipped: list[tuple[int, Exception]] = field(default_factory=list)
    text: str | None = None
    output_format: Any = None
    stopped: bool = False  # the stop request ended the run
    aborted: Exception | None = None  # the error that ended the run
    not_run: list[str] = field(default_factory=list)  # ids of stories not started
    model: str = ""  # the model that wrote the requirements
    prompt_set: str = ""  # the name of the prompt set
    tested_prompts: bool = True  # False when a user prompt set differs


def run_requirements(
    options: RequirementsOptions,
    *,
    cfg: SecReqGenConfig | None = None,
    make_client: ClientFactory | None = None,
    on_progress: ProgressCallback | None = None,
    should_stop: Callable[[], bool] | None = None,
    on_story_start: Callable[[str, int, int], None] | None = None,
    on_step: StepCallback | None = None,
) -> RequirementsRun:
    """Run the whole requirements pipeline. It writes no file.

    It raises InputError, NothingToDoError or ModelServerError. Errors of the
    domain packages pass through.

    The options strict_input and keep_finished are for a GUI. With
    strict_input, a SearchError before the first story is an InputError, and
    the retriever is built before the check of the model server. With
    keep_finished, an error inside the story loop ends the loop and goes to
    run.aborted, and the results that are done stay in the result. With
    on_step, the steps inside each story go to the callback as StepEvent.
    """
    from secreqgen.generation import GenerationMode
    from secreqgen.retrieval.word_search import SearchError

    check_input_file(options.input_file)
    check_docs_option(options.docs, options.baseline)
    output_format = requirements_format(options.output_format, allow_csv=True)
    if output_format is None:
        raise InputError(f"Invalid format: {options.output_format}")

    try:
        data = read_story_data(options.input_file, require_object=True)
    except Exception as e:
        raise InputError(f"Error parsing input file: {e}") from e
    stories_data = data.get("user_stories", [])
    if not stories_data:
        raise NothingToDoError("No user stories found in input file.")
    stories, skipped = build_user_stories(stories_data)
    if options.story_ids is not None:
        known = {story.id for story in stories}
        for story_id in options.story_ids:
            if story_id not in known:
                raise InputError(f"Story not found in the input file: {story_id}")
        wanted = set(options.story_ids)
        stories = [story for story in stories if story.id in wanted]
        if not stories:
            raise NothingToDoError("No user stories found in input file.")
    if options.model is not None and not options.model.strip():
        raise InputError("The model name is empty.")
    if options.prompts_dir is not None:
        from secreqgen.application.prompt_sets import check_prompt_folder

        check_prompt_folder("requirements", options.prompts_dir)
    product = options.product
    if product is None:
        product = data.get("project")

    if cfg is None:
        from secreqgen.config import get_config

        cfg = get_config()
    if make_client is None:
        from secreqgen.application.clients import build_ollama_client

        make_client = build_ollama_client
    mode = GenerationMode.ZERO_SHOT if options.baseline else GenerationMode.RAG

    tested_setup = options.docs is not None
    if options.model is None:
        llm_client = make_client(cfg, tested_setup=tested_setup)
    else:
        llm_client = make_client(
            cfg, tested_setup=tested_setup, model_name=options.model.strip()
        )

    def check_server() -> None:
        if not llm_client.check_health():
            raise ModelServerError(
                "Cannot connect to Ollama.",
                model=getattr(llm_client, "model_name", None),
            )

    if options.strict_input:
        # The cheap input checks come before the check of the model server.
        try:
            retriever = build_requirements_retriever(
                cfg, mode, docs=options.docs, product=product
            )
        except SearchError as e:
            raise InputError(str(e)) from e
        check_server()
    else:
        check_server()
        retriever = build_requirements_retriever(
            cfg, mode, docs=options.docs, product=product
        )

    prompt_set = ""
    tested_prompts = True
    if options.prompts_dir is None:
        prompt_builder = requirements_prompt_builder(options.docs)
        if options.docs is not None:
            from secreqgen.generation.prompt_sets import TESTED_PROMPT_SET

            prompt_set = Path(TESTED_PROMPT_SET).name
    else:
        from secreqgen.application.prompt_sets import is_tested
        from secreqgen.generation import SecurityRequirementsPromptBuilder

        prompt_builder = SecurityRequirementsPromptBuilder(
            prompts_dir=options.prompts_dir
        )
        prompt_set = f"user:{options.prompts_dir.name}"
        tested_prompts = is_tested("requirements", options.prompts_dir)
    generator = build_requirements_generator(llm_client, retriever, prompt_builder)
    if on_step is not None:
        from secreqgen.application.steps import watch_requirements_generator

        generator = watch_requirements_generator(generator, on_step)

    loop = generate_stories_stoppable(
        generator,
        stories,
        mode,
        options.system_context,
        on_progress,
        on_story_start=on_story_start,
        should_stop=should_stop,
        keep_finished=options.keep_finished,
    )
    results, failures = loop.results, loop.failures
    text = format_requirements(results, output_format) if results else None
    return RequirementsRun(
        results=results,
        failures=failures,
        skipped=skipped,
        text=text,
        output_format=output_format,
        stopped=loop.stopped,
        aborted=loop.aborted,
        not_run=loop.not_run,
        model=str(getattr(llm_client, "model_name", None) or options.model or ""),
        prompt_set=prompt_set,
        tested_prompts=tested_prompts,
    )
