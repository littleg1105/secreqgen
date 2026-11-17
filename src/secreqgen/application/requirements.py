"""Use case: generate security requirements from user stories.

The stage functions do one job each. The command line calls them in its own
order and keeps its own messages. run_requirements calls the same stages in
order, for a GUI or an HTTP API. It writes no file.
"""

from __future__ import annotations

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


@dataclass
class RequirementsRun:
    """The result of run_requirements."""

    results: list = field(default_factory=list)
    failures: list[tuple[str, Exception]] = field(default_factory=list)
    skipped: list[tuple[int, Exception]] = field(default_factory=list)
    text: str | None = None
    output_format: Any = None


def run_requirements(
    options: RequirementsOptions,
    *,
    cfg: SecReqGenConfig | None = None,
    make_client: ClientFactory | None = None,
    on_progress: ProgressCallback | None = None,
) -> RequirementsRun:
    """Run the whole requirements pipeline. It writes no file.

    It raises InputError, NothingToDoError or ModelServerError. Errors of the
    domain packages pass through.
    """
    from secreqgen.generation import GenerationMode

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

    llm_client = make_client(cfg, tested_setup=options.docs is not None)
    if not llm_client.check_health():
        raise ModelServerError(
            "Cannot connect to Ollama.",
            model=getattr(llm_client, "model_name", None),
        )
    retriever = build_requirements_retriever(
        cfg, mode, docs=options.docs, product=product
    )
    prompt_builder = requirements_prompt_builder(options.docs)
    generator = build_requirements_generator(llm_client, retriever, prompt_builder)

    results, failures = generate_for_stories(
        generator, stories, mode, options.system_context, on_progress
    )
    text = format_requirements(results, output_format) if results else None
    return RequirementsRun(
        results=results,
        failures=failures,
        skipped=skipped,
        text=text,
        output_format=output_format,
    )
