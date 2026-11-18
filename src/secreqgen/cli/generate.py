"""Commands of the generate group."""

from pathlib import Path
from typing import Annotated, Optional

import typer
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
)

from secreqgen.cli._app import console, generate_app
from secreqgen.config import get_config


def _client(cfg, **kwargs):
    """Build the model client through the patch point in secreqgen.cli."""
    from secreqgen import cli

    return cli._ollama_client(cfg, **kwargs)


@generate_app.command("check-ollama")
def check_ollama() -> None:
    """Check if Ollama is running and model is available."""
    from secreqgen.application.clients import build_health_client

    cfg = get_config()

    console.print(f"[cyan]Checking Ollama at:[/cyan] {cfg.llm.base_url}")
    console.print(f"[cyan]Model:[/cyan] {cfg.llm.model_name}")

    client = build_health_client(cfg)

    if client.check_health():
        console.print("[green]✓[/green] Ollama is running and model is available!")
        try:
            models = client.list_models()
            console.print(f"[dim]Available models: {', '.join(models)}[/dim]")
        except Exception:
            pass
    else:
        console.print(
            "[red]✗[/red] Cannot connect to Ollama or model not available.\n"
            f"Make sure Ollama is running: [cyan]ollama serve[/cyan]\n"
            f"And model is pulled: [cyan]ollama pull {cfg.llm.model_name}[/cyan]"
        )
        raise typer.Exit(1)


@generate_app.command("requirements")
def generate_requirements(
    input_file: Annotated[
        Path, typer.Argument(help="Input file with user stories (JSON/YAML)")
    ],
    output: Annotated[
        Optional[Path],
        typer.Option(
            "--output",
            "-o",
            help="Output file for generated requirements",
        ),
    ] = None,
    format: Annotated[
        str,
        typer.Option(
            "--format",
            "-f",
            help="Output format: json, yaml, markdown, csv",
        ),
    ] = "markdown",
    baseline: Annotated[
        bool,
        typer.Option(
            "--baseline",
            help="Generate baseline (zero-shot) requirements without RAG",
        ),
    ] = False,
    context: Annotated[
        Optional[Path],
        typer.Option(
            "--context",
            "-c",
            help="Optional system context YAML file (overrides stored context)",
        ),
    ] = None,
    docs: Annotated[
        Optional[Path],
        typer.Option(
            "--docs",
            help=(
                "Documentation folder for the word search (the tested setup). "
                "With this option the tool uses no vector store and no embedding model."
            ),
        ),
    ] = None,
    product: Annotated[
        Optional[str],
        typer.Option(
            "--product",
            help=(
                "Name of the product. 'keycloak' switches on the cleaning "
                "of Keycloak template text."
            ),
        ),
    ] = None,
    other_formats: Annotated[
        bool,
        typer.Option(
            "--other-formats",
            help=(
                "Also read .rst, .adoc, .asciidoc, .txt, .mdx, .markdown, .html, .htm, "
                ".pdf and .docx files (converted to Markdown). Outside the tested "
                "setup."
            ),
        ),
    ] = False,
    story_id: Annotated[
        list[str] | None,
        typer.Option(
            "--story-id",
            help=(
                "Write the requirements of these stories only. Give the option "
                "one time for each story"
            ),
        ),
    ] = None,
    model: Annotated[
        str | None,
        typer.Option(
            "--model",
            "-m",
            help=(
                "The model that writes the requirements. Default: the setting "
                "SECREQGEN_LLM_MODEL_NAME"
            ),
        ),
    ] = None,
    prompts_dir: Annotated[
        Path | None,
        typer.Option(
            "--prompts-dir",
            help=(
                "Folder with your own prompt files, in place of the tested "
                "prompts. Outside the tested setup"
            ),
        ),
    ] = None,
) -> None:
    """Generate security requirements from user stories.

    Input file should be JSON or YAML with format:
    {
      "user_stories": [
        {"id": "US-001", "title": "...", "story": "...", "acceptance_criteria": [...]}
      ]
    }
    """
    from rich.markup import escape

    from secreqgen.application.errors import InputError
    from secreqgen.application.inputs import (
        build_user_stories,
        check_docs_option,
        check_input_file,
        read_story_data,
    )
    from secreqgen.application.prompt_sets import check_prompt_folder
    from secreqgen.application.requirements import (
        build_requirements_generator,
        build_requirements_retriever,
        choose_requirements_prompts,
        format_requirements,
        generate_for_stories,
        requirements_format,
    )
    from secreqgen.application.threats import select_story_ids
    from secreqgen.exceptions import LLMConnectionError
    from secreqgen.generation import GenerationMode
    from secreqgen.retrieval.word_search import SearchError

    # Validate input file and the --docs option
    try:
        check_input_file(input_file)
        check_docs_option(docs, baseline)
        if other_formats and docs is None:
            raise InputError("--other-formats needs --docs.")
    except InputError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)

    # Validate format
    output_format = requirements_format(format, allow_csv=True)
    if output_format is None:
        console.print(
            f"[red]Error:[/red] Invalid format: {format}\n"
            "Valid formats: json, yaml, markdown, csv"
        )
        raise typer.Exit(1)

    # Get config
    cfg = get_config()
    mode = GenerationMode.ZERO_SHOT if baseline else GenerationMode.RAG

    # The line "Search" is shown only for the word search.
    # Without --docs the screen is as before.
    search_line = ""
    if docs is not None:
        search_line = f"[cyan]Search:[/cyan] word search over {docs}\n"
    console.print(
        Panel(
            f"[cyan]Input:[/cyan] {input_file}\n"
            f"[cyan]Mode:[/cyan] {'Baseline (zero-shot)' if baseline else 'RAG-enhanced'}\n"
            f"{search_line}"
            f"[cyan]Format:[/cyan] {output_format.value}\n"
            f"[cyan]Output:[/cyan] {output or 'stdout'}",
            title="Generating Security Requirements",
        )
    )

    # Load user stories
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        progress.add_task("Loading user stories...", total=None)

        try:
            data = read_story_data(input_file)
        except Exception as e:
            console.print(f"[red]Error parsing input file:[/red] {e}")
            raise typer.Exit(1)

    # Parse user stories
    stories_data = data.get("user_stories", [])
    if not stories_data:
        console.print("[yellow]No user stories found in input file.[/yellow]")
        raise typer.Exit(0)

    user_stories, skipped = build_user_stories(stories_data)
    for number, e in skipped:
        console.print(f"[yellow]Warning: Skipping invalid story {number}: {e}[/yellow]")

    console.print(f"[green]✓[/green] Loaded {len(user_stories)} user stories")

    # The same checks, in the same order, as run_requirements.
    try:
        if story_id:
            user_stories = select_story_ids(user_stories, story_id)
        if model is not None and not model.strip():
            raise InputError("The model name is empty.")
        if prompts_dir is not None:
            check_prompt_folder("requirements", prompts_dir)
    except InputError as e:
        console.print(f"[red]Error:[/red] {escape(str(e))}")
        raise typer.Exit(1)

    # Load optional system context from file
    system_context = None
    if context:
        if not context.exists():
            console.print(f"[red]Error:[/red] Context file not found: {context}")
            raise typer.Exit(1)
        system_context = context.read_text()
        console.print(f"[green]✓[/green] Loaded system context from {context.name}")
    elif docs is not None:
        console.print(
            "[yellow]Note:[/yellow] no system context file (--context). "
            "The tested runs had one."
        )

    # Documentation of other types: a temporary mirror, outside the tested setup.
    prepared = None
    search_docs = docs
    if other_formats and docs is not None:
        from secreqgen.application.documentation import (
            docs_note,
            prepare_documentation,
        )

        try:
            prepared = prepare_documentation(docs, other_formats=True)
        except InputError as e:
            console.print(f"[red]Error:[/red] {escape(str(e))}")
            raise typer.Exit(1)
        search_docs = prepared.folder
        note = docs_note(prepared.report)
        if note:
            console.print(f"[yellow]Note:[/yellow] {escape(note)}")

    try:
        # Initialize components
        try:
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                console=console,
            ) as progress:
                progress.add_task("Initializing components...", total=None)

                # Initialize LLM client
                if model is None:
                    llm_client = _client(cfg, tested_setup=docs is not None)
                else:
                    llm_client = _client(
                        cfg, tested_setup=docs is not None, model_name=model.strip()
                    )

                # Check Ollama connection
                if not llm_client.check_health():
                    console.print(
                        "[red]Error:[/red] Cannot connect to Ollama.\n"
                        f"Make sure Ollama is running: [cyan]ollama serve[/cyan]"
                    )
                    raise typer.Exit(1)

                # Initialize retriever for RAG mode.
                # The tested setup: word search over the documentation folder.
                # The product name comes from --product, or from the key
                # "project" of the input file.
                product_name = product
                if product_name is None and isinstance(data, dict):
                    product_name = data.get("project")
                retriever = build_requirements_retriever(
                    cfg, mode, docs=search_docs, product=product_name
                )

                # Initialize generator. With --docs the prompt is the tested prompt set.
                # With --prompts-dir it is the set of the person.
                prompt_builder, _prompt_set, _tested = choose_requirements_prompts(
                    docs, prompts_dir
                )
                generator = build_requirements_generator(
                    llm_client, retriever, prompt_builder
                )

            console.print("[green]✓[/green] Components initialized")

        except LLMConnectionError as e:
            console.print(f"[red]LLM Connection Error:[/red] {e}")
            raise typer.Exit(1)
        except SearchError as e:
            console.print(f"[red]Documentation Error:[/red] {e}")
            raise typer.Exit(1)

        # Generate requirements
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            console=console,
        ) as progress:
            task = progress.add_task(
                "Generating requirements...", total=len(user_stories)
            )

            def on_progress(event) -> None:
                if event.error is not None:
                    console.print(
                        f"[yellow]Warning: Failed to generate for {event.story_id}: "
                        f"{event.error}[/yellow]"
                    )
                progress.advance(task)

            results, _failures = generate_for_stories(
                generator, user_stories, mode, system_context, on_progress
            )

        if not results:
            console.print("[red]No requirements generated.[/red]")
            raise typer.Exit(1)

        total_reqs = sum(len(r.requirements) for r in results)
        console.print(f"[green]✓[/green] Generated {total_reqs} requirements from {len(results)} stories")

        # Format output
        formatted_output = format_requirements(results, output_format)

        # Output
        if output:
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(formatted_output, encoding="utf-8")
            console.print(f"[green]✓[/green] Written to {output}")
        else:
            console.print("\n" + formatted_output)
    finally:
        if prepared is not None:
            prepared.close()


@generate_app.command("single")
def generate_single(
    title: Annotated[
        str,
        typer.Option("--title", "-t", help="User story title"),
    ],
    story: Annotated[
        str,
        typer.Option("--story", "-s", help="User story text"),
    ],
    acceptance_criteria: Annotated[
        Optional[list[str]],
        typer.Option("--ac", help="Acceptance criteria (can be repeated)"),
    ] = None,
    baseline: Annotated[
        bool,
        typer.Option("--baseline", help="Use baseline (zero-shot) mode"),
    ] = False,
    format: Annotated[
        str,
        typer.Option("--format", "-f", help="Output format: json, yaml, markdown"),
    ] = "markdown",
    docs: Annotated[
        Optional[Path],
        typer.Option(
            "--docs",
            help=(
                "Documentation folder for the word search (the tested setup). "
                "With this option the tool uses no vector store and no embedding model."
            ),
        ),
    ] = None,
    product: Annotated[
        Optional[str],
        typer.Option(
            "--product",
            help=(
                "Name of the product. 'keycloak' switches on the cleaning "
                "of Keycloak template text."
            ),
        ),
    ] = None,
    context: Annotated[
        Optional[Path],
        typer.Option("--context", "-c", help="Optional system context YAML file"),
    ] = None,
) -> None:
    """Generate requirements for a single user story (interactive mode)."""
    import uuid

    from secreqgen.application.errors import InputError
    from secreqgen.application.inputs import check_docs_option
    from secreqgen.application.requirements import (
        build_requirements_generator,
        build_requirements_retriever,
        format_requirements,
        requirements_format,
        requirements_prompt_builder,
    )
    from secreqgen.exceptions import GenerationError, LLMConnectionError
    from secreqgen.generation import GenerationMode
    from secreqgen.generation.schemas import UserStory
    from secreqgen.retrieval.word_search import SearchError

    # Validate format
    output_format = requirements_format(format, allow_csv=False)
    if output_format is None:
        console.print(f"[red]Error:[/red] Invalid format: {format}")
        raise typer.Exit(1)

    try:
        check_docs_option(docs, baseline)
    except InputError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)
    if context is not None and not context.is_file():
        console.print(f"[red]Error:[/red] Context file not found: {context}")
        raise typer.Exit(1)

    cfg = get_config()
    mode = GenerationMode.ZERO_SHOT if baseline else GenerationMode.RAG

    # Create user story
    user_story = UserStory(
        id=f"US-{uuid.uuid4().hex[:6].upper()}",
        title=title,
        story=story,
        acceptance_criteria=acceptance_criteria or [],
    )

    console.print(
        Panel(
            f"[cyan]Title:[/cyan] {title}\n"
            f"[cyan]Story:[/cyan] {story}\n"
            f"[cyan]Mode:[/cyan] {'Baseline' if baseline else 'RAG'}",
            title="Generating Requirements",
        )
    )

    try:
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            progress.add_task("Initializing...", total=None)

            llm_client = _client(cfg, tested_setup=docs is not None)

            if not llm_client.check_health():
                console.print("[red]Cannot connect to Ollama.[/red]")
                raise typer.Exit(1)

            # The tested setup: word search over the documentation folder,
            # and the tested prompt set.
            prompt_builder = requirements_prompt_builder(docs)
            retriever = build_requirements_retriever(
                cfg, mode, docs=docs, product=product, system_context_file=context
            )

            generator = build_requirements_generator(
                llm_client, retriever, prompt_builder
            )

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            progress.add_task("Generating requirements...", total=None)
            single_context = None
            if context is not None:
                single_context = context.read_text(encoding="utf-8")
            result = generator.generate(
                user_story=user_story, mode=mode, system_context=single_context
            )

        console.print(f"[green]✓[/green] Generated {len(result.requirements)} requirements")

        formatted_output = format_requirements([result], output_format)
        console.print("\n" + formatted_output)

    except LLMConnectionError as e:
        console.print(f"[red]LLM Connection Error:[/red] {e}")
        raise typer.Exit(1)
    except SearchError as e:
        console.print(f"[red]Documentation Error:[/red] {e}")
        raise typer.Exit(1)
    except GenerationError as e:
        console.print(f"[red]Generation Error:[/red] {e}")
        raise typer.Exit(1)


@generate_app.command("threats")
def generate_threats(
    input_file: Annotated[
        Path, typer.Argument(help="Input file with user stories (JSON/YAML)")
    ],
    docs: Annotated[
        Path,
        typer.Option("--docs", help="Documentation folder for the word search"),
    ],
    product: Annotated[
        Optional[str],
        typer.Option("--product", help="Name of the product (default: key 'project')"),
    ] = None,
    story_id: Annotated[
        list[str] | None,
        typer.Option(
            "--story-id",
            help=(
                "Write the threat list of these stories only. Give the option "
                "one time for each story"
            ),
        ),
    ] = None,
    labels: Annotated[
        bool,
        typer.Option(
            "--labels/--no-labels",
            help="One short call for each story: a threat actor and a STRIDE category for each row",
        ),
    ] = True,
    writer_model: Annotated[
        Optional[str],
        typer.Option(
            "--model",
            "-m",
            help=(
                "The model that writes the rows. Default: the setting "
                "SECREQGEN_LLM_MODEL_NAME when it is set, else gemma4:26b"
            ),
        ),
    ] = None,
    check: Annotated[
        bool,
        typer.Option(
            "--check/--no-check",
            help=(
                "A second local model checks each row. "
                "The rows that it accepts come first on the page"
            ),
        ),
    ] = True,
    checker_model: Annotated[
        Optional[str],
        typer.Option(
            "--checker-model",
            help=(
                "The second model. Default: qwen3.8:27b "
                "(gemma4:26b when qwen3.8:27b writes the rows)"
            ),
        ),
    ] = None,
    about_file: Annotated[
        Optional[Path],
        typer.Option(
            "--about-file",
            help=(
                "Text file with one paragraph about the product (120 words at "
                "most). The model that writes the rows reads it as background, "
                "not as a source"
            ),
        ),
    ] = None,
    other_formats: Annotated[
        bool,
        typer.Option(
            "--other-formats",
            help=(
                "Also read .rst, .adoc, .asciidoc, .txt, .mdx, .markdown, .html, .htm, "
                ".pdf and .docx files (converted to Markdown). Outside the tested "
                "setup."
            ),
        ),
    ] = False,
    prompts_dir: Annotated[
        Path | None,
        typer.Option(
            "--prompts-dir",
            help=(
                "Folder with your own prompt files, in place of the tested "
                "prompts. Outside the tested setup"
            ),
        ),
    ] = None,
    output: Annotated[
        Path,
        typer.Option("--output", "-o", help="Output folder"),
    ] = Path("threat_lists"),
) -> None:
    """Write a DRAFT threat list for each user story, from the documentation.

    The list holds the attacks and the security rules that the documentation
    states. Default setup (the tested one): gemma4:26b writes the rows and
    qwen3.8:27b checks them.
    """
    from rich.markup import escape

    from secreqgen.application.errors import InputError
    from secreqgen.application.inputs import (
        build_user_stories,
        check_docs_folder,
        check_input_file,
        read_story_data,
    )
    from secreqgen.application.prompt_sets import check_prompt_folder
    from secreqgen.application.threats import (
        ThreatOptions,
        build_threat_generator,
        real_file_names,
        resolve_threat_models,
        select_story_ids,
        summarise_threat_list,
        threat_folder_name,
        threat_product_name,
        write_threat_files,
    )
    from secreqgen.exceptions import GenerationError, LLMConnectionError
    from secreqgen.retrieval.word_search import SearchError
    from secreqgen.threats import ThreatListError
    from secreqgen.threats.pipeline import DRAFT_NOTICE

    try:
        check_input_file(input_file)
        check_docs_folder(docs)
    except InputError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)
    try:
        data = read_story_data(input_file, encoding="utf-8", require_object=True)
    except Exception as e:
        console.print(f"[red]Error parsing input file:[/red] {e}")
        raise typer.Exit(1)

    user_stories, skipped = build_user_stories(data.get("user_stories", []) or [])
    for number, e in skipped:
        console.print(f"[yellow]Warning: Skipping invalid story {number}: {e}[/yellow]")
    if story_id:
        try:
            user_stories = select_story_ids(user_stories, story_id)
        except InputError as e:
            console.print(f"[red]Error:[/red] {escape(str(e))}")
            raise typer.Exit(1)
    if not user_stories:
        console.print("[yellow]No user stories found in input file.[/yellow]")
        raise typer.Exit(0)

    product_name = threat_product_name(product, data)
    cfg = get_config()
    if prompts_dir is not None:
        try:
            check_prompt_folder("threats", prompts_dir)
        except InputError as e:
            console.print(f"[red]Error:[/red] {escape(str(e))}")
            raise typer.Exit(1)
    try:
        writer_name, checker_name = resolve_threat_models(
            cfg, writer_model, checker_model, check
        )
    except ThreatListError as e:
        console.print(f"[red]Error:[/red] {escape(str(e))}")
        raise typer.Exit(1)
    # Documentation of other types: a temporary mirror, outside the tested setup.
    prepared = None
    search_docs = docs
    if other_formats:
        from secreqgen.application.documentation import (
            docs_note,
            prepare_documentation,
        )

        try:
            prepared = prepare_documentation(docs, other_formats=True)
        except InputError as e:
            console.print(f"[red]Error:[/red] {escape(str(e))}")
            raise typer.Exit(1)
        search_docs = prepared.folder
        note = docs_note(prepared.report)
        if note:
            console.print(f"[yellow]Note:[/yellow] {escape(note)}")

    try:
        try:
            llm_client = _client(cfg, tested_setup=True, model_name=writer_name)
            if not llm_client.check_health():
                console.print(
                    "[red]Error:[/red] Ollama does not answer, or it does not have "
                    f"the model {escape(writer_name)}.\n"
                    "Start Ollama: [cyan]ollama serve[/cyan]. Get the model: "
                    f"[cyan]ollama pull {escape(writer_name)}[/cyan]"
                )
                raise typer.Exit(1)
            checker = None
            if checker_name:
                checker = _client(cfg, tested_setup=True, model_name=checker_name)
                if not checker.check_health():
                    console.print(
                        "[red]Error:[/red] Ollama does not have the checker model "
                        f"{escape(checker_name)}.\n"
                        f"Get it: [cyan]ollama pull {escape(checker_name)}[/cyan]. "
                        "Or run with [cyan]--no-check[/cyan] (no second model)."
                    )
                    raise typer.Exit(1)
            about = None
            if about_file is not None:
                about_name = escape(str(about_file))
                if not about_file.is_file():
                    problem = "Not a file" if about_file.exists() else "File not found"
                    console.print(f"[red]Error:[/red] {problem}: {about_name}")
                    raise typer.Exit(1)
                try:
                    # utf-8-sig: a byte order mark at the start of the file is not text
                    about = about_file.read_text(encoding="utf-8-sig")
                except (OSError, UnicodeError) as e:
                    console.print(
                        f"[red]Error:[/red] Cannot read {about_name}: {escape(str(e))}"
                    )
                    raise typer.Exit(1)
                if not about.strip():
                    console.print(
                        f"[red]Error:[/red] The file has no text: {about_name}"
                    )
                    raise typer.Exit(1)
            generator = build_threat_generator(
                ThreatOptions(
                    input_file=input_file,
                    docs=docs,
                    labels=labels,
                    prompts_dir=prompts_dir,
                ),
                llm_client,
                checker,
                product_name,
                about,
                False,
                search_docs,
            )
        except LLMConnectionError as e:
            console.print(f"[red]LLM Connection Error:[/red] {e}")
            raise typer.Exit(1)
        except (SearchError, ThreatListError, InputError) as e:
            console.print(f"[red]Error:[/red] {escape(str(e))}")
            raise typer.Exit(1)

        console.print(
            f"Writer: {escape(writer_name)}. Checker: "
            f"{escape(checker_name) if checker_name else 'none (--no-check)'}."
        )
        written = 0
        used_names: set[str] = set()
        for story in user_stories:
            try:
                model = generator.generate(story)
            except LLMConnectionError as e:
                console.print(f"[red]LLM Connection Error:[/red] {escape(str(e))}")
                raise typer.Exit(1)
            except (ThreatListError, SearchError, GenerationError) as e:
                console.print(
                    f"[yellow]Warning: no threat list for {escape(story.id)}: {escape(str(e))}[/yellow]"
                )
                continue
            if prepared is not None and prepared.report is not None:
                model = real_file_names(model)
            # Two story ids can give the same folder name ("A/1" and "A_1"): the second one gets a number.
            folder = output / threat_folder_name(story.id, used_names)
            try:
                write_threat_files(folder, model)
            except OSError as e:
                console.print(f"[red]Error:[/red] Cannot write to {escape(str(folder))}: {escape(str(e))}")
                raise typer.Exit(1)
            written += 1
            summary = summarise_threat_list(model)
            checked = (
                f", {summary.accepted} accepted by {checker_name}"
                if checker_name
                else ""
            )
            console.print(
                f"[green]✓[/green] {escape(story.id)}: {summary.rows} rows ({summary.attacks} attacks, "
                f"{summary.rules} rules), {summary.clean} with no weak sign{checked}, "
                f"{summary.dropped} removed by the script, {summary.calls} calls, {summary.seconds} s"
            )

        if written == 0:
            console.print("[red]No threat list was written.[/red]")
            raise typer.Exit(1)
        console.print(f"\n[bold]{DRAFT_NOTICE}[/bold]")
    finally:
        if prepared is not None:
            prepared.close()



MAX_DROPPED_LINES = 10


@generate_app.command(
    "context",
    help=(
        "Write a draft system context file for a codebase. "
        "A person must check it before use."
    ),
)
def generate_context(
    folder: Annotated[Path, typer.Argument(help="The folder of the codebase")],
    output: Annotated[
        Path | None,
        typer.Option(
            "--output",
            "-o",
            help=(
                "Write the YAML text to this new file (a file that exists is "
                "never replaced). Without it the text goes to the console."
            ),
        ),
    ] = None,
    writer_model: Annotated[
        str | None,
        typer.Option(
            "--model",
            "-m",
            help="The model that writes (default: the tested writer).",
        ),
    ] = None,
    product: Annotated[
        str | None,
        typer.Option(
            "--product",
            "-p",
            help="The name of the product, if the files do not say it.",
        ),
    ] = None,
) -> None:
    """Write a draft system context file for a codebase."""
    from rich.markup import escape

    from secreqgen.application.errors import InputError, ModelServerError
    from secreqgen.application.system_context import (
        ContextOptions,
        resolve_context_model,
        run_system_context,
    )
    from secreqgen.context import DRAFT_NOTICE, ContextError, source_lines
    from secreqgen.exceptions import LLMConnectionError

    cfg = get_config()
    try:
        writer_name = resolve_context_model(cfg, writer_model)
    except InputError as e:
        console.print(f"[red]Error:[/red] {escape(str(e))}")
        raise typer.Exit(1)
    console.print(
        f"Writing a system context for {escape(folder.name or str(folder))} "
        f"with {escape(writer_name)}"
    )
    options = ContextOptions(
        folder=folder, product=product, writer_model=writer_model, output=output
    )
    try:
        run = run_system_context(options, cfg=cfg, make_client=_client)
    except InputError as e:
        console.print(f"[red]Error:[/red] {escape(str(e))}")
        raise typer.Exit(1)
    except ModelServerError as e:
        console.print(
            f"[red]Error:[/red] {escape(str(e))}\n"
            "Start Ollama: [cyan]ollama serve[/cyan]. Get the model: "
            f"[cyan]ollama pull {escape(writer_name)}[/cyan]"
        )
        raise typer.Exit(1)
    except LLMConnectionError as e:
        console.print(f"[red]LLM Connection Error:[/red] {escape(str(e))}")
        raise typer.Exit(1)
    except ContextError as e:
        console.print(f"[red]Error:[/red] {escape(str(e))}")
        raise typer.Exit(1)
    except OSError as e:
        console.print(
            f"[red]Error:[/red] Cannot write to {escape(str(output))}: "
            f"{escape(str(e))}"
        )
        raise typer.Exit(1)

    result = run.result
    facts = result["facts"]
    def count(number: int, one: str, many: str) -> str:
        return f"{number} {one if number == 1 else many}"

    scanned = (
        f"{count(facts['files_seen'], 'file', 'files')} scanned. "
        f"{count(len(facts['parts']), 'part', 'parts')} read."
    )
    secret = facts["secret_files_skipped"]
    if secret:
        one, many = "file that can hold secrets", "files that can hold secrets"
        verb = "was" if secret == 1 else "were"
        scanned += f" {count(secret, one, many)} {verb} skipped."
    if facts.get("stopped_early"):
        scanned += " The folder has more files: the scan stopped at this number."
    console.print(scanned)
    context = result["context"]
    features = sum(len(g["items"]) for g in context["features"])
    data_kinds = sum(len(v) for v in context["data_sensitivity"].values())
    console.print(
        f"Kept: {count(len(context['tech_stack']), 'technology', 'technologies')}, "
        f"{count(features, 'feature', 'features')} in "
        f"{count(len(context['features']), 'group', 'groups')}, "
        f"{count(data_kinds, 'kind of data', 'kinds of data')}."
    )
    dropped = result["dropped"]
    if dropped:
        console.print(f"Dropped: {count(len(dropped), 'item', 'items')}.")
        for item in dropped[:MAX_DROPPED_LINES]:
            console.print(f"- {escape(str(item['what']))}: {escape(str(item['why']))}")
    console.print("Each line and its source:")
    for line in source_lines(context):
        console.print(
            f"- {line['where']}: {line['text']}  <- {line['path']}",
            markup=False,
            highlight=False,
            soft_wrap=True,
        )
    if run.output is not None:
        console.print(f"Wrote {escape(str(run.output))}")
    else:
        # soft_wrap: a long line stays one line, so that a copy is valid YAML
        console.print(result["yaml"], markup=False, highlight=False, soft_wrap=True)
    console.print(f"\n[bold]{DRAFT_NOTICE}[/bold]")


@generate_app.command(
    "about",
    help=(
        "Write a draft paragraph about the product, for --about-file. "
        "A person must check it before use."
    ),
)
def generate_about(
    source: Annotated[
        Path,
        typer.Argument(
            help=(
                "The folder of the codebase (a model writes the facts), or a "
                "system context file in YAML (no model runs)"
            )
        ),
    ],
    output: Annotated[
        Path | None,
        typer.Option(
            "--output",
            "-o",
            help=(
                "Write the paragraph to this new file (a file that exists is "
                "never replaced). Without it the text goes to the console."
            ),
        ),
    ] = None,
    writer_model: Annotated[
        str | None,
        typer.Option(
            "--model",
            "-m",
            help=(
                "The model that writes (default: the tested writer). "
                "It has no use with a system context file."
            ),
        ),
    ] = None,
    product: Annotated[
        str | None,
        typer.Option(
            "--product",
            "-p",
            help="The name of the product, if the files do not say it.",
        ),
    ] = None,
) -> None:
    """Write a draft paragraph about the product."""
    from rich.markup import escape

    from secreqgen.application.about import AboutOptions, run_about
    from secreqgen.application.errors import InputError, ModelServerError
    from secreqgen.application.system_context import resolve_context_model
    from secreqgen.context import (
        ABOUT_DRAFT_NOTICE,
        ABOUT_FILE_NOTICE,
        ContextError,
    )
    from secreqgen.exceptions import LLMConnectionError

    def count(number: int, one: str, many: str) -> str:
        return f"{number} {one if number == 1 else many}"

    cfg = get_config()
    writer_name = ""
    if source.is_dir():
        try:
            writer_name = resolve_context_model(cfg, writer_model)
        except InputError as e:
            console.print(f"[red]Error:[/red] {escape(str(e))}")
            raise typer.Exit(1)
        console.print(
            f"Writing a paragraph about {escape(source.name or str(source))} "
            f"with {escape(writer_name)}"
        )
    options = AboutOptions(
        source=source, product=product, writer_model=writer_model, output=output
    )
    try:
        run = run_about(options, cfg=cfg, make_client=_client)
    except InputError as e:
        console.print(f"[red]Error:[/red] {escape(str(e))}")
        raise typer.Exit(1)
    except ModelServerError as e:
        console.print(
            f"[red]Error:[/red] {escape(str(e))}\n"
            "Start Ollama: [cyan]ollama serve[/cyan]. Get the model: "
            f"[cyan]ollama pull {escape(writer_name)}[/cyan]"
        )
        raise typer.Exit(1)
    except LLMConnectionError as e:
        console.print(f"[red]LLM Connection Error:[/red] {escape(str(e))}")
        raise typer.Exit(1)
    except ContextError as e:
        console.print(f"[red]Error:[/red] {escape(str(e))}")
        raise typer.Exit(1)
    except OSError as e:
        console.print(
            f"[red]Error:[/red] Cannot write to {escape(str(output))}: "
            f"{escape(str(e))}"
        )
        raise typer.Exit(1)

    draft = run.draft
    if run.result is not None:
        facts = run.result["facts"]
        scanned = (
            f"{count(facts['files_seen'], 'file', 'files')} scanned. "
            f"{count(len(facts['parts']), 'part', 'parts')} read."
        )
        secret = facts["secret_files_skipped"]
        if secret:
            one, many = "file that can hold secrets", "files that can hold secrets"
            verb = "was" if secret == 1 else "were"
            scanned += f" {count(secret, one, many)} {verb} skipped."
        if facts.get("stopped_early"):
            scanned += " The folder has more files: the scan stopped at this number."
        console.print(scanned)
        console.print("Each line and its source:")
        for line in draft.lines:
            path = line["path"] or "The model named no source file."
            console.print(
                f"- {line['where']}: {line['text']}  <- {path}",
                markup=False,
                highlight=False,
                soft_wrap=True,
            )
        if draft.left_out:
            console.print(
                f"{count(len(draft.left_out), 'feature', 'features')} "
                "left out for length."
            )
        console.print()
    if run.output is not None:
        console.print(f"Wrote {escape(str(run.output))}")
    else:
        console.print(draft.paragraph, markup=False, highlight=False, soft_wrap=True)
    if run.result is not None:
        console.print(f"\n[bold]{ABOUT_DRAFT_NOTICE}[/bold]")
    else:
        console.print(ABOUT_FILE_NOTICE)
