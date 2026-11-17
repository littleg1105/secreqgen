"""CLI interface for SecReqGen using Typer."""

from pathlib import Path
from typing import Annotated, Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn
from rich.table import Table

from secreqgen import __version__
from secreqgen.config import get_config
from secreqgen.exceptions import IngestionError, VectorStoreError

# Main app
app = typer.Typer(
    name="secreqgen",
    help="RAG-enhanced security requirements generator",
    no_args_is_help=True,
)

# The tested setup (word search) calls the model as the runs of Study 2 did
# (scripts/run_study2.py): a context window that holds the documentation, the
# prompt and the answer, a long time limit, and no JSON-mode option.
WORD_SEARCH_NUM_CTX = 16384
WORD_SEARCH_TIMEOUT_S = 900.0
DEFAULT_TIMEOUT_S = 120.0

# Sub-apps
ingest_app = typer.Typer(help="Ingest documents and system context")
generate_app = typer.Typer(help="Generate security requirements")
evaluate_app = typer.Typer(help="Evaluate generated requirements")

app.add_typer(ingest_app, name="ingest")
app.add_typer(generate_app, name="generate")
app.add_typer(evaluate_app, name="evaluate")

console = Console()

# Document type mapping from CLI option to DocumentType enum
DOC_TYPE_MAP = {
    "policies": "POLICY",
    "policy": "POLICY",
    "architecture": "ARCHITECTURE",
    "arch": "ARCHITECTURE",
    "standards": "STANDARD",
    "standard": "STANDARD",
}


def _ollama_client(cfg, tested_setup: bool):
    """Build the Ollama client of a generate command.

    A value in the settings (SECREQGEN_LLM_NUM_CTX, SECREQGEN_LLM_TIMEOUT,
    SECREQGEN_LLM_STRUCTURED_OUTPUT) wins. Without one, the word search uses
    the values of the tested runs, and the vector store path keeps the
    behaviour that it had before.
    """
    from secreqgen.generation import OllamaClient

    num_ctx = cfg.llm.num_ctx
    timeout = cfg.llm.timeout
    structured_output = cfg.llm.structured_output
    if tested_setup:
        num_ctx = num_ctx or WORD_SEARCH_NUM_CTX
        timeout = timeout or WORD_SEARCH_TIMEOUT_S
        if structured_output is None:
            structured_output = False
    return OllamaClient(
        base_url=cfg.llm.base_url,
        model_name=cfg.llm.model_name,
        temperature=cfg.llm.temperature,
        max_tokens=cfg.llm.max_tokens,
        timeout=timeout or DEFAULT_TIMEOUT_S,
        num_ctx=num_ctx,
        structured_output=True if structured_output is None else structured_output,
    )


@app.command()
def version() -> None:
    """Show version and exit."""
    console.print(f"SecReqGen version: {__version__}")


@app.command()
def config() -> None:
    """Display current configuration."""
    cfg = get_config()

    table = Table(title="SecReqGen Configuration")
    table.add_column("Setting", style="cyan")
    table.add_column("Value", style="green")

    # Embedding config
    table.add_row("Embedding Model", cfg.embedding.model_name)
    table.add_row("Embedding Device", cfg.embedding.device)

    # Vector store config
    table.add_row("Vector Store Path", str(cfg.vectorstore.persist_directory))
    table.add_row("Collection Prefix", cfg.vectorstore.collection_prefix)

    # LLM config
    table.add_row("LLM Model", cfg.llm.model_name)
    table.add_row("LLM Base URL", cfg.llm.base_url)
    table.add_row("LLM Temperature", str(cfg.llm.temperature))

    # Retrieval config
    table.add_row("Retrieval Top K", str(cfg.retrieval.top_k))
    table.add_row("Similarity Threshold", str(cfg.retrieval.similarity_threshold))

    console.print(table)


# Ingest commands
@ingest_app.command("documents")
def ingest_documents(
    directory: Annotated[Path, typer.Argument(help="Directory containing documents")],
    doc_type: Annotated[
        str,
        typer.Option(
            "--type",
            "-t",
            help="Document type: policies, architecture, or standards",
        ),
    ] = "policies",
) -> None:
    """Ingest documents from a directory into the vector store.

    Pipeline: load → chunk → tag → embed → store
    """
    # Lazy imports to avoid slow startup
    from secreqgen.ingestion import (
        ArchitectureAwareChunker,
        DocumentEmbedder,
        DocumentLoaderRegistry,
        MetadataExtractor,
    )
    from secreqgen.ingestion.metadata import DocumentType
    from secreqgen.retrieval import SecReqGenVectorStore

    # Validate directory
    if not directory.exists():
        console.print(f"[red]Error:[/red] Directory not found: {directory}")
        raise typer.Exit(1)

    if not directory.is_dir():
        console.print(f"[red]Error:[/red] Not a directory: {directory}")
        raise typer.Exit(1)

    # Map CLI doc_type to DocumentType enum
    doc_type_key = doc_type.lower()
    if doc_type_key not in DOC_TYPE_MAP:
        console.print(
            f"[red]Error:[/red] Invalid document type: {doc_type}\n"
            f"Valid types: policies, architecture, standards"
        )
        raise typer.Exit(1)

    document_type = DocumentType[DOC_TYPE_MAP[doc_type_key]]

    # Get config
    cfg = get_config()

    console.print(
        Panel(
            f"[cyan]Directory:[/cyan] {directory}\n"
            f"[cyan]Type:[/cyan] {document_type.value}\n"
            f"[cyan]Vector Store:[/cyan] {cfg.vectorstore.persist_directory}",
            title="Ingesting Documents",
        )
    )

    # Initialize components
    loader_registry = DocumentLoaderRegistry()
    chunker = ArchitectureAwareChunker()
    metadata_extractor = MetadataExtractor()
    embedder = DocumentEmbedder(
        model_name=cfg.embedding.model_name,
        device=cfg.embedding.device,
        batch_size=cfg.embedding.batch_size,
    )
    vector_store = SecReqGenVectorStore(
        persist_directory=cfg.vectorstore.persist_directory,
        collection_prefix=cfg.vectorstore.collection_prefix,
    )

    try:
        # Step 1: Load documents
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            progress.add_task("Loading documents...", total=None)
            documents = loader_registry.load_directory(directory)

        if not documents:
            console.print("[yellow]No supported documents found in directory.[/yellow]")
            raise typer.Exit(0)

        console.print(f"[green]✓[/green] Loaded {len(documents)} documents")

        # Step 2: Chunk documents
        all_chunks = []
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            console=console,
        ) as progress:
            task = progress.add_task("Chunking documents...", total=len(documents))
            for doc in documents:
                chunks = chunker.chunk_document(doc)
                all_chunks.extend(chunks)
                progress.advance(task)

        console.print(f"[green]✓[/green] Created {len(all_chunks)} chunks")

        if not all_chunks:
            console.print("[yellow]No chunks created from documents.[/yellow]")
            raise typer.Exit(0)

        # Step 3: Extract metadata (for logging/debugging)
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            progress.add_task("Extracting metadata...", total=None)
            # Extract from first document for summary
            if documents:
                sample_metadata = metadata_extractor.extract(
                    documents[0].content, str(documents[0].source_path)
                )

        domains_found = set()
        for doc in documents:
            meta = metadata_extractor.extract(doc.content, str(doc.source_path))
            domains_found.update(d.value for d in meta.security_domains)

        console.print(
            f"[green]✓[/green] Detected domains: {', '.join(sorted(domains_found)) or 'none'}"
        )

        # Step 4: Generate embeddings
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            console=console,
        ) as progress:
            task = progress.add_task("Generating embeddings...", total=len(all_chunks))

            # Process in batches for progress updates
            batch_size = cfg.embedding.batch_size
            all_embeddings = []

            for i in range(0, len(all_chunks), batch_size):
                batch = all_chunks[i:i + batch_size]
                embedded = embedder.embed_chunks(batch)
                all_embeddings.extend([e.embedding for e in embedded])
                progress.advance(task, len(batch))

        console.print(f"[green]✓[/green] Generated {len(all_embeddings)} embeddings")

        # Step 5: Store in vector database
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            progress.add_task("Storing in vector database...", total=None)
            count = vector_store.add_chunks(all_chunks, all_embeddings, document_type)

        console.print(f"[green]✓[/green] Stored {count} chunks in {document_type.value} collection")

        # Show final stats
        stats = vector_store.get_stats()
        console.print(
            Panel(
                f"[green]Ingestion complete![/green]\n\n"
                f"Documents processed: {len(documents)}\n"
                f"Chunks created: {len(all_chunks)}\n"
                f"Total chunks in store: {stats['total_chunks']}",
                title="Summary",
            )
        )

    except IngestionError as e:
        console.print(f"[red]Ingestion error:[/red] {e}")
        raise typer.Exit(1)
    except VectorStoreError as e:
        console.print(f"[red]Vector store error:[/red] {e}")
        raise typer.Exit(1)


@ingest_app.command("system-context")
def ingest_system_context(
    file: Annotated[Path, typer.Argument(help="System context YAML file")],
) -> None:
    """Load system context from a YAML file into the vector store."""
    # Lazy imports
    from secreqgen.ingestion import (
        ArchitectureAwareChunker,
        DocumentEmbedder,
        DocumentLoaderRegistry,
    )
    from secreqgen.ingestion.metadata import DocumentType
    from secreqgen.retrieval import SecReqGenVectorStore

    # Validate file
    if not file.exists():
        console.print(f"[red]Error:[/red] File not found: {file}")
        raise typer.Exit(1)

    if not file.is_file():
        console.print(f"[red]Error:[/red] Not a file: {file}")
        raise typer.Exit(1)

    if file.suffix.lower() not in [".yaml", ".yml"]:
        console.print(f"[red]Error:[/red] File must be YAML (.yaml or .yml): {file}")
        raise typer.Exit(1)

    # Get config
    cfg = get_config()

    console.print(
        Panel(
            f"[cyan]File:[/cyan] {file}\n"
            f"[cyan]Vector Store:[/cyan] {cfg.vectorstore.persist_directory}",
            title="Loading System Context",
        )
    )

    # Initialize components
    loader_registry = DocumentLoaderRegistry()
    chunker = ArchitectureAwareChunker()
    embedder = DocumentEmbedder(
        model_name=cfg.embedding.model_name,
        device=cfg.embedding.device,
        batch_size=cfg.embedding.batch_size,
    )
    vector_store = SecReqGenVectorStore(
        persist_directory=cfg.vectorstore.persist_directory,
        collection_prefix=cfg.vectorstore.collection_prefix,
    )

    try:
        # Load YAML file
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            progress.add_task("Loading YAML file...", total=None)
            doc = loader_registry.load(file)

        console.print(f"[green]✓[/green] Loaded {file.name}")

        # Chunk (YAML becomes single chunk)
        chunks = chunker.chunk_document(doc)
        console.print(f"[green]✓[/green] Created {len(chunks)} chunk(s)")

        if not chunks:
            console.print("[yellow]No content in YAML file.[/yellow]")
            raise typer.Exit(0)

        # Embed
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            progress.add_task("Generating embedding...", total=None)
            embedded = embedder.embed_chunks(chunks)
            embeddings = [e.embedding for e in embedded]

        console.print(f"[green]✓[/green] Generated embedding")

        # Store in system_context collection
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            progress.add_task("Storing in vector database...", total=None)
            count = vector_store.add_chunks(
                chunks, embeddings, DocumentType.SYSTEM_CONTEXT
            )

        console.print(
            Panel(
                f"[green]System context loaded![/green]\n\n"
                f"File: {file.name}\n"
                f"Chunks stored: {count}",
                title="Summary",
            )
        )

    except IngestionError as e:
        console.print(f"[red]Ingestion error:[/red] {e}")
        raise typer.Exit(1)
    except VectorStoreError as e:
        console.print(f"[red]Vector store error:[/red] {e}")
        raise typer.Exit(1)


@ingest_app.command("stats")
def ingest_stats() -> None:
    """Show vector store statistics."""
    from secreqgen.retrieval import SecReqGenVectorStore

    cfg = get_config()

    # Check if vector store directory exists
    if not cfg.vectorstore.persist_directory.exists():
        console.print(
            "[yellow]Vector store not initialized yet.[/yellow]\n"
            "Run 'secreqgen ingest documents' or 'secreqgen ingest system-context' first."
        )
        raise typer.Exit(0)

    vector_store = SecReqGenVectorStore(
        persist_directory=cfg.vectorstore.persist_directory,
        collection_prefix=cfg.vectorstore.collection_prefix,
    )

    stats = vector_store.get_stats()

    table = Table(title="Vector Store Statistics")
    table.add_column("Collection", style="cyan")
    table.add_column("Chunks", style="green", justify="right")

    for doc_type, info in stats["collections"].items():
        table.add_row(doc_type, str(info["count"]))

    table.add_row("─" * 15, "─" * 6)
    table.add_row("[bold]Total[/bold]", f"[bold]{stats['total_chunks']}[/bold]")

    console.print(table)
    console.print(f"\n[dim]Store path: {cfg.vectorstore.persist_directory}[/dim]")


@ingest_app.command("clear")
def ingest_clear(
    force: Annotated[
        bool,
        typer.Option("--force", "-f", help="Skip confirmation prompt"),
    ] = False,
) -> None:
    """Clear all data from the vector store."""
    from secreqgen.retrieval import SecReqGenVectorStore

    cfg = get_config()

    if not cfg.vectorstore.persist_directory.exists():
        console.print("[yellow]Vector store not initialized yet.[/yellow]")
        raise typer.Exit(0)

    if not force:
        confirm = typer.confirm(
            "This will delete ALL ingested documents. Are you sure?"
        )
        if not confirm:
            console.print("[yellow]Aborted.[/yellow]")
            raise typer.Exit(0)

    vector_store = SecReqGenVectorStore(
        persist_directory=cfg.vectorstore.persist_directory,
        collection_prefix=cfg.vectorstore.collection_prefix,
    )

    vector_store.clear()
    console.print("[green]✓[/green] Vector store cleared.")


# Generate commands
@generate_app.command("check-ollama")
def check_ollama() -> None:
    """Check if Ollama is running and model is available."""
    from secreqgen.generation import OllamaClient

    cfg = get_config()

    console.print(f"[cyan]Checking Ollama at:[/cyan] {cfg.llm.base_url}")
    console.print(f"[cyan]Model:[/cyan] {cfg.llm.model_name}")

    client = OllamaClient(
        base_url=cfg.llm.base_url,
        model_name=cfg.llm.model_name,
    )

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
) -> None:
    """Generate security requirements from user stories.

    Input file should be JSON or YAML with format:
    {
      "user_stories": [
        {"id": "US-001", "title": "...", "story": "...", "acceptance_criteria": [...]}
      ]
    }
    """
    import json
    import yaml as yaml_lib

    from secreqgen.exceptions import GenerationError, LLMConnectionError
    from secreqgen.generation import (
        GenerationMode,
        SecurityRequirementsGenerator,
        SecurityRequirementsPromptBuilder,
    )
    from secreqgen.generation.schemas import UserStory
    from secreqgen.ingestion import DocumentEmbedder
    from secreqgen.output import OutputFormat, RequirementsExporter, RequirementsFormatter
    from secreqgen.retrieval import SecReqGenVectorStore, SimpleRetriever
    from secreqgen.retrieval.word_search import SearchError

    # Validate input file
    if not input_file.exists():
        console.print(f"[red]Error:[/red] Input file not found: {input_file}")
        raise typer.Exit(1)
    if docs is not None and not docs.is_dir():
        console.print(f"[red]Error:[/red] Documentation folder not found: {docs}")
        raise typer.Exit(1)
    if docs is not None and baseline:
        # The tested setup has no zero-shot mode.
        # No test ran its prompt without the passages.
        console.print("[red]Error:[/red] --baseline cannot be used with --docs.")
        raise typer.Exit(1)

    # Validate format
    format_map = {
        "json": OutputFormat.JSON,
        "yaml": OutputFormat.YAML,
        "markdown": OutputFormat.MARKDOWN,
        "md": OutputFormat.MARKDOWN,
        "csv": OutputFormat.CSV,
    }
    output_format = format_map.get(format.lower())
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
            content = input_file.read_text()
            if input_file.suffix.lower() in [".yaml", ".yml"]:
                data = yaml_lib.safe_load(content)
            else:
                data = json.loads(content)
        except Exception as e:
            console.print(f"[red]Error parsing input file:[/red] {e}")
            raise typer.Exit(1)

    # Parse user stories
    stories_data = data.get("user_stories", [])
    if not stories_data:
        console.print("[yellow]No user stories found in input file.[/yellow]")
        raise typer.Exit(0)

    user_stories = []
    for i, story_data in enumerate(stories_data):
        try:
            story = UserStory(
                id=story_data.get("id", f"US-{i+1:03d}"),
                title=story_data.get("title", "Untitled"),
                story=story_data.get("story", ""),
                acceptance_criteria=story_data.get("acceptance_criteria", []),
            )
            user_stories.append(story)
        except Exception as e:
            console.print(f"[yellow]Warning: Skipping invalid story {i+1}: {e}[/yellow]")

    console.print(f"[green]✓[/green] Loaded {len(user_stories)} user stories")

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

    # Initialize components
    try:
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            progress.add_task("Initializing components...", total=None)

            # Initialize LLM client
            llm_client = _ollama_client(cfg, tested_setup=docs is not None)

            # Check Ollama connection
            if not llm_client.check_health():
                console.print(
                    "[red]Error:[/red] Cannot connect to Ollama.\n"
                    f"Make sure Ollama is running: [cyan]ollama serve[/cyan]"
                )
                raise typer.Exit(1)

            # Initialize retriever for RAG mode
            retriever = None
            if mode == GenerationMode.RAG and docs is not None:
                # The tested setup: word search over the documentation folder.
                # The product name comes from --product, or from the key
                # "project" of the input file.
                from secreqgen.retrieval.word_search import WordSearchRetriever

                product_name = product
                if product_name is None and isinstance(data, dict):
                    product_name = data.get("project")
                retriever = WordSearchRetriever(docs, product=product_name)
            elif mode == GenerationMode.RAG:
                embedder = DocumentEmbedder(
                    model_name=cfg.embedding.model_name,
                    device=cfg.embedding.device,
                )
                vectorstore = SecReqGenVectorStore(
                    persist_directory=cfg.vectorstore.persist_directory,
                    collection_prefix=cfg.vectorstore.collection_prefix,
                )
                retriever = SimpleRetriever(
                    vectorstore=vectorstore,
                    embedder=embedder,
                    top_k=cfg.retrieval.top_k,
                    similarity_threshold=cfg.retrieval.similarity_threshold,
                )

            # Initialize generator. With --docs the prompt is the tested prompt set.
            prompts_dir = None
            if docs is not None:
                from secreqgen.generation.prompt_sets import TESTED_PROMPT_SET

                prompts_dir = TESTED_PROMPT_SET
            generator = SecurityRequirementsGenerator(
                llm_client=llm_client,
                retriever=retriever,
                prompt_builder=SecurityRequirementsPromptBuilder(
                    prompts_dir=prompts_dir
                ),
            )

        console.print("[green]✓[/green] Components initialized")

    except LLMConnectionError as e:
        console.print(f"[red]LLM Connection Error:[/red] {e}")
        raise typer.Exit(1)
    except SearchError as e:
        console.print(f"[red]Documentation Error:[/red] {e}")
        raise typer.Exit(1)

    # Generate requirements
    results = []
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("Generating requirements...", total=len(user_stories))

        for story in user_stories:
            try:
                result = generator.generate(
                    user_story=story,
                    mode=mode,
                    system_context=system_context,
                )
                results.append(result)
                progress.advance(task)
            except (GenerationError, SearchError) as e:
                console.print(f"[yellow]Warning: Failed to generate for {story.id}: {e}[/yellow]")
                progress.advance(task)

    if not results:
        console.print("[red]No requirements generated.[/red]")
        raise typer.Exit(1)

    total_reqs = sum(len(r.requirements) for r in results)
    console.print(f"[green]✓[/green] Generated {total_reqs} requirements from {len(results)} stories")

    # Format output
    formatter = RequirementsFormatter()
    if len(results) == 1:
        formatted_output = formatter.format(results[0], output_format)
    else:
        formatted_output = formatter.format_batch(results, output_format)

    # Output
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(formatted_output, encoding="utf-8")
        console.print(f"[green]✓[/green] Written to {output}")
    else:
        console.print("\n" + formatted_output)


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

    from secreqgen.exceptions import GenerationError, LLMConnectionError
    from secreqgen.generation import (
        GenerationMode,
        SecurityRequirementsGenerator,
        SecurityRequirementsPromptBuilder,
    )
    from secreqgen.generation.schemas import UserStory
    from secreqgen.ingestion import DocumentEmbedder
    from secreqgen.output import OutputFormat, RequirementsFormatter
    from secreqgen.retrieval import SecReqGenVectorStore, SimpleRetriever
    from secreqgen.retrieval.word_search import SearchError

    # Validate format
    format_map = {
        "json": OutputFormat.JSON,
        "yaml": OutputFormat.YAML,
        "markdown": OutputFormat.MARKDOWN,
        "md": OutputFormat.MARKDOWN,
    }
    output_format = format_map.get(format.lower())
    if output_format is None:
        console.print(f"[red]Error:[/red] Invalid format: {format}")
        raise typer.Exit(1)

    if docs is not None and not docs.is_dir():
        console.print(f"[red]Error:[/red] Documentation folder not found: {docs}")
        raise typer.Exit(1)
    if docs is not None and baseline:
        # The tested setup has no zero-shot mode.
        # No test ran its prompt without the passages.
        console.print("[red]Error:[/red] --baseline cannot be used with --docs.")
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

            llm_client = _ollama_client(cfg, tested_setup=docs is not None)

            if not llm_client.check_health():
                console.print("[red]Cannot connect to Ollama.[/red]")
                raise typer.Exit(1)

            retriever = None
            prompt_builder = None
            if docs is not None:
                # The tested setup: word search over the documentation folder,
                # and the tested prompt set.
                from secreqgen.generation.prompt_sets import TESTED_PROMPT_SET

                prompt_builder = SecurityRequirementsPromptBuilder(
                    prompts_dir=TESTED_PROMPT_SET
                )
            if mode == GenerationMode.RAG and docs is not None:
                from secreqgen.retrieval.word_search import WordSearchRetriever

                retriever = WordSearchRetriever(
                    docs, product=product, system_context_file=context
                )
            elif mode == GenerationMode.RAG:
                embedder = DocumentEmbedder(
                    model_name=cfg.embedding.model_name,
                    device=cfg.embedding.device,
                )
                vectorstore = SecReqGenVectorStore(
                    persist_directory=cfg.vectorstore.persist_directory,
                    collection_prefix=cfg.vectorstore.collection_prefix,
                )
                retriever = SimpleRetriever(
                    vectorstore=vectorstore,
                    embedder=embedder,
                    top_k=cfg.retrieval.top_k,
                    similarity_threshold=cfg.retrieval.similarity_threshold,
                )

            generator = SecurityRequirementsGenerator(
                llm_client=llm_client,
                retriever=retriever,
                prompt_builder=prompt_builder,
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

        formatter = RequirementsFormatter()
        formatted_output = formatter.format(result, output_format)
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
        Optional[str],
        typer.Option("--story-id", help="Write the threat list of this story only"),
    ] = None,
    labels: Annotated[
        bool,
        typer.Option(
            "--labels/--no-labels",
            help="One short call for each story: a threat actor and a STRIDE category for each row",
        ),
    ] = True,
    output: Annotated[
        Path,
        typer.Option("--output", "-o", help="Output folder"),
    ] = Path("threat_lists"),
) -> None:
    """Write a DRAFT threat list for each user story: the attacks and the security rules that the documentation states."""
    import json
    import re

    import yaml as yaml_lib
    from rich.markup import escape

    from secreqgen.exceptions import GenerationError, LLMConnectionError
    from secreqgen.generation.schemas import UserStory
    from secreqgen.retrieval.word_search import SearchError
    from secreqgen.threats import ThreatListError, ThreatListGenerator, render_markdown
    from secreqgen.threats.pipeline import DRAFT_NOTICE

    if not input_file.exists():
        console.print(f"[red]Error:[/red] Input file not found: {input_file}")
        raise typer.Exit(1)
    if not docs.is_dir():
        console.print(f"[red]Error:[/red] Documentation folder not found: {docs}")
        raise typer.Exit(1)
    try:
        text = input_file.read_text(encoding="utf-8")
        if input_file.suffix.lower() in [".yaml", ".yml"]:
            data = yaml_lib.safe_load(text)
        else:
            data = json.loads(text)
        if not isinstance(data, dict):
            raise ValueError("the file must hold an object")
    except Exception as e:
        console.print(f"[red]Error parsing input file:[/red] {e}")
        raise typer.Exit(1)

    user_stories = []
    for i, story_data in enumerate(data.get("user_stories", []) or []):
        try:
            user_stories.append(
                UserStory(
                    id=story_data.get("id", f"US-{i+1:03d}"),
                    title=story_data.get("title", "Untitled"),
                    story=story_data.get("story", ""),
                    acceptance_criteria=story_data.get("acceptance_criteria", []),
                )
            )
        except Exception as e:
            console.print(f"[yellow]Warning: Skipping invalid story {i+1}: {e}[/yellow]")
    if story_id is not None:
        user_stories = [s for s in user_stories if s.id == story_id]
        if not user_stories:
            console.print(f"[red]Error:[/red] Story not found in the input file: {story_id}")
            raise typer.Exit(1)
    if not user_stories:
        console.print("[yellow]No user stories found in input file.[/yellow]")
        raise typer.Exit(0)

    product_name = product if product is not None else data.get("project")
    cfg = get_config()
    try:
        llm_client = _ollama_client(cfg, tested_setup=True)
        if not llm_client.check_health():
            console.print(
                "[red]Error:[/red] Cannot connect to Ollama.\n"
                "Make sure Ollama is running: [cyan]ollama serve[/cyan]"
            )
            raise typer.Exit(1)
        generator = ThreatListGenerator(llm_client, docs, product=product_name, name_labels=labels)
    except LLMConnectionError as e:
        console.print(f"[red]LLM Connection Error:[/red] {e}")
        raise typer.Exit(1)
    except (SearchError, ThreatListError) as e:
        console.print(f"[red]Error:[/red] {escape(str(e))}")
        raise typer.Exit(1)

    written = 0
    for story in user_stories:
        try:
            model = generator.generate(story)
        except (ThreatListError, SearchError, GenerationError) as e:
            console.print(
                f"[yellow]Warning: no threat list for {escape(story.id)}: {escape(str(e))}[/yellow]"
            )
            continue
        folder = output / (re.sub(r"[^A-Za-z0-9._-]", "_", story.id).strip(".") or "story")
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "threats.json").write_text(
            json.dumps(model, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        (folder / "threats.md").write_text(render_markdown(model), encoding="utf-8")
        written += 1
        rows = model["rows"]
        attacks = sum(1 for r in rows if r["type"] == "attack")
        clean = sum(1 for r in rows if not r.get("weak_signs"))
        console.print(
            f"[green]✓[/green] {escape(story.id)}: {len(rows)} rows ({attacks} attacks, "
            f"{len(rows) - attacks} rules), {clean} with no weak sign, "
            f"{len(model['dropped'])} removed by the script, {len(model['calls'])} calls, {model['seconds']} s"
        )

    if written == 0:
        console.print("[red]No threat list was written.[/red]")
        raise typer.Exit(1)
    console.print(f"\n[bold]{DRAFT_NOTICE}[/bold]")


# Evaluate commands
@evaluate_app.command("run")
def evaluate_run(
    stories: Annotated[
        Path, typer.Argument(help="User stories JSON file")
    ],
    ground_truth: Annotated[
        Path, typer.Argument(help="Ground truth JSON file")
    ],
    output_dir: Annotated[
        Optional[Path],
        typer.Option(
            "--output",
            "-o",
            help="Output directory for reports",
        ),
    ] = None,
    match_threshold: Annotated[
        float,
        typer.Option(
            "--match-threshold",
            help="Similarity threshold for match (default 0.85)",
        ),
    ] = 0.85,
    partial_threshold: Annotated[
        float,
        typer.Option(
            "--partial-threshold",
            help="Similarity threshold for partial match (default 0.70)",
        ),
    ] = 0.70,
    improvement_threshold: Annotated[
        float,
        typer.Option(
            "--improvement-threshold",
            help="F1 improvement factor for thesis success (default 1.20 = 20%)",
        ),
    ] = 1.20,
    context: Annotated[
        Optional[Path],
        typer.Option(
            "--context",
            "-c",
            help="Optional system context YAML file",
        ),
    ] = None,
    report_name: Annotated[
        str,
        typer.Option(
            "--name",
            "-n",
            help="Base name for report files",
        ),
    ] = "evaluation",
    model: Annotated[
        Optional[str],
        typer.Option(
            "--model",
            "-m",
            help="Ollama model for generation (default: llama3:8b). Try qwen3:30b for better results.",
        ),
    ] = None,
    with_verification: Annotated[
        bool,
        typer.Option(
            "--with-verification",
            help="Enable LLM-as-Judge verification layer to filter low-quality requirements",
        ),
    ] = False,
    verification_threshold: Annotated[
        float,
        typer.Option(
            "--verification-threshold",
            help="Minimum average score (1-10) to keep a requirement (default: 6.5)",
        ),
    ] = 6.5,
) -> None:
    """Run full evaluation pipeline: RAG vs baseline comparison.

    Runs both baseline (zero-shot) and RAG-enhanced generation on the input
    stories, matches results against ground truth, and generates comparison
    reports with Precision/Recall/F1 metrics.

    Example:
        secreqgen evaluate run stories.json ground_truth.json -o docs/evaluation_results
    """
    from secreqgen.evaluation import (
        EvaluationPipeline,
        EvaluationReportGenerator,
    )
    from secreqgen.exceptions import GenerationError, LLMConnectionError
    from secreqgen.generation import OllamaClient
    from secreqgen.ingestion import DocumentEmbedder
    from secreqgen.retrieval import SecReqGenVectorStore, SimpleRetriever

    # Validate files
    if not stories.exists():
        console.print(f"[red]Error:[/red] Stories file not found: {stories}")
        raise typer.Exit(1)

    if not ground_truth.exists():
        console.print(f"[red]Error:[/red] Ground truth file not found: {ground_truth}")
        raise typer.Exit(1)

    cfg = get_config()

    # Use specified model or default from config
    model_name = model if model else cfg.llm.model_name

    # Default output directory
    if output_dir is None:
        output_dir = Path("docs/evaluation_results")

    # Build status panel content
    panel_content = (
        f"[cyan]Stories:[/cyan] {stories}\n"
        f"[cyan]Ground Truth:[/cyan] {ground_truth}\n"
        f"[cyan]Output:[/cyan] {output_dir}\n"
        f"[cyan]Match Threshold:[/cyan] {match_threshold}\n"
        f"[cyan]Improvement Target:[/cyan] {(improvement_threshold - 1) * 100:.0f}%\n"
        f"[cyan]Model:[/cyan] {model_name}"
    )
    if with_verification:
        panel_content += f"\n[cyan]Verification:[/cyan] Enabled (threshold: {verification_threshold})"

    console.print(Panel(panel_content, title="Evaluation Pipeline"))

    # Load optional system context
    system_context = None
    if context:
        if not context.exists():
            console.print(f"[red]Error:[/red] Context file not found: {context}")
            raise typer.Exit(1)
        system_context = context.read_text()
        console.print(f"[green]✓[/green] Loaded system context from {context.name}")

    try:
        # Initialize components
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            progress.add_task("Initializing components...", total=None)

            # Initialize LLM client with specified model
            llm_client = OllamaClient(
                base_url=cfg.llm.base_url,
                model_name=model_name,
                temperature=cfg.llm.temperature,
                max_tokens=cfg.llm.max_tokens,
            )

            if not llm_client.check_health():
                console.print(
                    "[red]Error:[/red] Cannot connect to Ollama.\n"
                    f"Make sure Ollama is running: [cyan]ollama serve[/cyan]"
                )
                raise typer.Exit(1)

            # Initialize retriever
            embedder = DocumentEmbedder(
                model_name=cfg.embedding.model_name,
                device=cfg.embedding.device,
            )
            vectorstore = SecReqGenVectorStore(
                persist_directory=cfg.vectorstore.persist_directory,
                collection_prefix=cfg.vectorstore.collection_prefix,
            )
            retriever = SimpleRetriever(
                vectorstore=vectorstore,
                embedder=embedder,
                top_k=cfg.retrieval.top_k,
                similarity_threshold=cfg.retrieval.similarity_threshold,
            )

            # Initialize pipeline
            pipeline = EvaluationPipeline(
                llm_client=llm_client,
                retriever=retriever,
                match_threshold=match_threshold,
                partial_threshold=partial_threshold,
                improvement_threshold=improvement_threshold,
                system_context=system_context,
            )

        console.print("[green]✓[/green] Components initialized")

        # Run evaluation with progress tracking
        console.print("\n[bold]Running evaluation pipeline...[/bold]\n")

        def progress_callback(phase: str, current: int, total: int) -> None:
            """Display progress updates."""
            phase_names = {
                "loading": "Loading data",
                "baseline": "Baseline generation",
                "rag": "RAG generation",
                "matching": "Matching results",
            }
            phase_display = phase_names.get(phase, phase)
            if total > 0:
                console.print(
                    f"  {phase_display}: {current}/{total}",
                    end="\r",
                )

        result = pipeline.run(
            stories_path=stories,
            ground_truth_path=ground_truth,
            progress_callback=progress_callback,
        )

        console.print("\n")  # Clear progress line

        # Display summary
        console.print(result.summary())

        # Generate reports
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            progress.add_task("Generating reports...", total=None)

            report_generator = EvaluationReportGenerator(output_dir)
            report_paths = report_generator.generate_full_report(result, report_name)

        console.print("\n[bold]Reports generated:[/bold]")
        for path in report_paths:
            console.print(f"  • {path}")

        # Final verdict
        if result.meets_thesis_target:
            console.print(
                "\n[bold green]✓ THESIS SUCCESS:[/bold green] "
                f"RAG shows {result.comparison.f1_improvement_pct:+.1f}% F1 improvement!"
            )
        else:
            console.print(
                "\n[bold yellow]✗ THESIS TARGET NOT MET:[/bold yellow] "
                f"RAG shows {result.comparison.f1_improvement_pct:+.1f}% F1 change "
                f"(target: {(improvement_threshold - 1) * 100:.0f}%)"
            )

    except LLMConnectionError as e:
        console.print(f"[red]LLM Connection Error:[/red] {e}")
        raise typer.Exit(1)
    except GenerationError as e:
        console.print(f"[red]Generation Error:[/red] {e}")
        raise typer.Exit(1)
    except FileNotFoundError as e:
        console.print(f"[red]File Error:[/red] {e}")
        raise typer.Exit(1)
    except ValueError as e:
        console.print(f"[red]Validation Error:[/red] {e}")
        raise typer.Exit(1)


@evaluate_app.command("compare-runs")
def evaluate_compare_runs(
    stories: Annotated[
        Path, typer.Argument(help="User stories JSON file")
    ],
    ground_truth: Annotated[
        Path, typer.Argument(help="Ground truth JSON file")
    ],
    runs: Annotated[
        int,
        typer.Option(
            "--runs",
            help="Number of repeated runs (default 20). Use --pilot for N=10.",
        ),
    ] = 20,
    pilot: Annotated[
        bool,
        typer.Option(
            "--pilot",
            help="Pilot mode: run N=10 and mark output as pilot (not thesis-grade).",
        ),
    ] = False,
    output_dir: Annotated[
        Optional[Path],
        typer.Option(
            "--output",
            "-o",
            help="Output directory for the multi-run report",
        ),
    ] = None,
    match_threshold: Annotated[
        float,
        typer.Option(
            "--match-threshold",
            help="Similarity threshold for match (default 0.85)",
        ),
    ] = 0.85,
    partial_threshold: Annotated[
        float,
        typer.Option(
            "--partial-threshold",
            help="Similarity threshold for partial match (default 0.70)",
        ),
    ] = 0.70,
    context: Annotated[
        Optional[Path],
        typer.Option(
            "--context",
            "-c",
            help="Optional system context YAML file",
        ),
    ] = None,
    model: Annotated[
        Optional[str],
        typer.Option(
            "--model",
            "-m",
            help="Ollama model for generation (default from config)",
        ),
    ] = None,
) -> None:
    """Run the evaluation pipeline N times and report aggregate statistics.

    Default N=20 produces thesis-grade variance characterization. Use
    --pilot for N=10 during development; pilot reports are flagged as
    such and should not be cited as headline results.

    Reports two paired tests: per-story (PRIMARY) and per-run
    (SENSITIVITY), with Wilcoxon as the primary inferential test and
    paired t-test as a sensitivity check. Effect size is paired Cohen's
    d_z. A Shapiro-Wilk normality check on the paired differences is
    reported alongside so reviewers can interpret the sensitivity
    result.

    Example:
        secreqgen evaluate compare-runs stories.json ground_truth.json --runs 20
    """
    from secreqgen.evaluation import (
        EvaluationPipeline,
        build_multi_run_report,
        run_multi_evaluation,
        serialize_individual_result,
        serialize_report,
    )
    from secreqgen.exceptions import GenerationError, LLMConnectionError
    from secreqgen.generation import OllamaClient
    from secreqgen.ingestion import DocumentEmbedder
    from secreqgen.retrieval import SecReqGenVectorStore, SimpleRetriever

    # Validate input files BEFORE any heavy initialization so the command
    # fails fast on bad paths without spinning up Ollama or the embedder.
    if not stories.exists():
        console.print(f"[red]Error:[/red] Stories file not found: {stories}")
        raise typer.Exit(1)
    if not ground_truth.exists():
        console.print(
            f"[red]Error:[/red] Ground truth file not found: {ground_truth}"
        )
        raise typer.Exit(1)

    if pilot:
        runs = 10

    cfg = get_config()
    model_name = model if model else cfg.llm.model_name

    if output_dir is None:
        output_dir = Path("docs/evaluation_results")

    pilot_label = " (PILOT)" if pilot else ""
    panel_content = (
        f"[cyan]Stories:[/cyan] {stories}\n"
        f"[cyan]Ground Truth:[/cyan] {ground_truth}\n"
        f"[cyan]Output:[/cyan] {output_dir}\n"
        f"[cyan]Runs:[/cyan] {runs}{pilot_label}\n"
        f"[cyan]Match Threshold:[/cyan] {match_threshold}\n"
        f"[cyan]Partial Threshold:[/cyan] {partial_threshold}\n"
        f"[cyan]Model:[/cyan] {model_name}"
    )
    console.print(Panel(panel_content, title="Multi-Run Evaluation Pipeline"))

    system_context = None
    if context:
        if not context.exists():
            console.print(f"[red]Error:[/red] Context file not found: {context}")
            raise typer.Exit(1)
        system_context = context.read_text()
        console.print(
            f"[green]✓[/green] Loaded system context from {context.name}"
        )

    try:
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            progress.add_task("Initializing components...", total=None)

            llm_client = OllamaClient(
                base_url=cfg.llm.base_url,
                model_name=model_name,
                temperature=cfg.llm.temperature,
                max_tokens=cfg.llm.max_tokens,
            )

            if not llm_client.check_health():
                console.print(
                    "[red]Error:[/red] Cannot connect to Ollama.\n"
                    f"Make sure Ollama is running: [cyan]ollama serve[/cyan]"
                )
                raise typer.Exit(1)

            embedder = DocumentEmbedder(
                model_name=cfg.embedding.model_name,
                device=cfg.embedding.device,
            )
            vectorstore = SecReqGenVectorStore(
                persist_directory=cfg.vectorstore.persist_directory,
                collection_prefix=cfg.vectorstore.collection_prefix,
            )
            retriever = SimpleRetriever(
                vectorstore=vectorstore,
                embedder=embedder,
                top_k=cfg.retrieval.top_k,
                similarity_threshold=cfg.retrieval.similarity_threshold,
            )

            pipeline = EvaluationPipeline(
                llm_client=llm_client,
                retriever=retriever,
                match_threshold=match_threshold,
                partial_threshold=partial_threshold,
                system_context=system_context,
            )

        console.print("[green]✓[/green] Components initialized")

        console.print(f"\n[bold]Running {runs} evaluations...[/bold]\n")

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            console=console,
        ) as progress:
            task = progress.add_task("Multi-run evaluation", total=runs)

            def progress_callback(current: int, total: int, phase: str) -> None:
                if phase.startswith("completed"):
                    progress.update(
                        task,
                        completed=current,
                        description=f"Run {current}/{total} complete",
                    )
                else:
                    progress.update(
                        task, description=f"Run {current}/{total}: {phase}"
                    )

            individual_results = run_multi_evaluation(
                pipeline=pipeline,
                stories_path=stories,
                ground_truth_path=ground_truth,
                n_runs=runs,
                progress_callback=progress_callback,
            )

        console.print()

        report_config = {
            "stories": str(stories),
            "ground_truth": str(ground_truth),
            "model": model_name,
            "match_threshold": match_threshold,
            "partial_threshold": partial_threshold,
            "n_runs": runs,
            "pilot": pilot,
        }
        report = build_multi_run_report(individual_results, report_config)

        output_dir.mkdir(parents=True, exist_ok=True)
        timestamp = report.run_timestamp.strftime("%Y-%m-%d_%H-%M-%S")
        run_dir_name = (
            f"run_comparison_{timestamp}_pilot"
            if pilot
            else f"run_comparison_{timestamp}"
        )
        run_dir = output_dir / run_dir_name
        run_dir.mkdir(parents=True, exist_ok=True)

        report_json = serialize_report(report)
        json_path = run_dir / "multi_run_report.json"
        json_path.write_text(report_json, encoding="utf-8")

        # Persist the raw per-run payloads (generated text, retrieval
        # context, match records) for qualitative audit. serialize_report
        # intentionally drops these to keep the aggregate file small; this
        # is the side-channel for spot-checking the F1 numbers and
        # examining what the model actually produced.
        raw_runs_dir = run_dir / "raw_runs"
        raw_runs_dir.mkdir(parents=True, exist_ok=True)
        for idx, result in enumerate(individual_results, start=1):
            raw_path = raw_runs_dir / f"run_{idx:03d}.json"
            raw_path.write_text(
                serialize_individual_result(result), encoding="utf-8"
            )

        console.print(
            f"\n[bold]Multi-Run Summary{' (PILOT)' if pilot else ''}[/bold]\n"
        )

        primary = report.paired_test_per_story_f1
        console.print(
            "[bold cyan]Primary inferential result (per-story F1)[/bold cyan]"
        )
        console.print(
            f"  N pairs (stories): {primary.n_pairs}\n"
            f"  Wilcoxon: W = {primary.primary_statistic:.3f}, "
            f"p = {primary.primary_p_value:.4f}\n"
            f"  Paired t-test: t = {primary.sensitivity_statistic:.3f}, "
            f"p = {primary.sensitivity_p_value:.4f}\n"
            f"  Shapiro-Wilk on diffs: p = {primary.shapiro_wilk_p:.4f}\n"
            f"  Cohen's d_z: {primary.d_z:.3f} ({primary.d_z_interpretation})"
        )

        sensitivity = report.paired_test_per_run_f1
        console.print(
            "\n[bold cyan]Sensitivity result (per-run aggregate F1)[/bold cyan]"
        )
        console.print(
            f"  N pairs (runs): {sensitivity.n_pairs}\n"
            f"  Wilcoxon: W = {sensitivity.primary_statistic:.3f}, "
            f"p = {sensitivity.primary_p_value:.4f}\n"
            f"  Paired t-test: t = {sensitivity.sensitivity_statistic:.3f}, "
            f"p = {sensitivity.sensitivity_p_value:.4f}\n"
            f"  Cohen's d_z: {sensitivity.d_z:.3f} "
            f"({sensitivity.d_z_interpretation})"
        )

        console.print(f"\n[green]✓[/green] Report written to {json_path}")

    except LLMConnectionError as e:
        console.print(f"[red]LLM Connection Error:[/red] {e}")
        raise typer.Exit(1)
    except GenerationError as e:
        console.print(f"[red]Generation Error:[/red] {e}")
        raise typer.Exit(1)
    except FileNotFoundError as e:
        console.print(f"[red]File Error:[/red] {e}")
        raise typer.Exit(1)
    except ValueError as e:
        console.print(f"[red]Validation Error:[/red] {e}")
        raise typer.Exit(1)


@evaluate_app.command("compare")
def evaluate_compare(
    generated: Annotated[
        Path, typer.Argument(help="Generated requirements file (JSON)")
    ],
    ground_truth: Annotated[
        Path, typer.Argument(help="Ground truth requirements file (JSON)")
    ],
    match_threshold: Annotated[
        float,
        typer.Option(
            "--threshold",
            "-t",
            help="Similarity threshold for match (default 0.85)",
        ),
    ] = 0.85,
    partial_threshold: Annotated[
        float,
        typer.Option(
            "--partial",
            "-p",
            help="Similarity threshold for partial match (default 0.70)",
        ),
    ] = 0.70,
    output: Annotated[
        Optional[Path],
        typer.Option(
            "--output",
            "-o",
            help="Output file for match results (JSON)",
        ),
    ] = None,
) -> None:
    """Compare generated requirements against ground truth.

    Quick evaluation of already-generated requirements without running the
    full pipeline. Useful for comparing different generation runs.

    Expected generated format:
    {"requirements": [{"id": "...", "title": "...", "description": "..."}]}

    Expected ground truth format:
    {"requirements": [{"id": "...", "title": "...", "description": "..."}]}
    """
    import json

    from secreqgen.evaluation import (
        MatchType,
        MetricsCalculator,
        SemanticMatcher,
        load_ground_truth,
    )
    from secreqgen.generation.schemas import SecurityRequirement

    # Validate files
    if not generated.exists():
        console.print(f"[red]Error:[/red] Generated file not found: {generated}")
        raise typer.Exit(1)

    if not ground_truth.exists():
        console.print(f"[red]Error:[/red] Ground truth file not found: {ground_truth}")
        raise typer.Exit(1)

    console.print(
        Panel(
            f"[cyan]Generated:[/cyan] {generated}\n"
            f"[cyan]Ground Truth:[/cyan] {ground_truth}\n"
            f"[cyan]Match Threshold:[/cyan] {match_threshold}\n"
            f"[cyan]Partial Threshold:[/cyan] {partial_threshold}",
            title="Comparing Requirements",
        )
    )

    try:
        # Load generated requirements
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            progress.add_task("Loading files...", total=None)

            with open(generated, encoding="utf-8") as f:
                gen_data = json.load(f)

            with open(ground_truth, encoding="utf-8") as f:
                gt_data = json.load(f)

        # Parse generated requirements
        # For compare command, we accept simplified requirement format without all fields
        gen_reqs = []
        requirements_data = gen_data.get("requirements", [])
        for i, req_data in enumerate(requirements_data):
            try:
                # Use default values for optional fields to allow simpler input format
                req = SecurityRequirement(
                    id=req_data.get("id", f"GEN-{i+1:03d}"),
                    user_story_id=req_data.get("user_story_id", "unknown"),  # Default for compare
                    title=req_data.get("title", ""),
                    description=req_data.get("description", ""),
                    rationale=req_data.get("rationale", ""),  # Default for compare
                    asvs_category=req_data.get("asvs_category", "V1: Architecture, Design and Threat Modeling"),
                    priority=req_data.get("priority", "medium"),
                    implementation_notes=req_data.get("implementation_notes", ""),
                    acceptance_criteria=req_data.get("acceptance_criteria", []),
                )
                gen_reqs.append(req)
            except Exception as e:
                console.print(f"[yellow]Warning: Skipping invalid requirement {i+1}: {e}[/yellow]")

        console.print(f"[green]✓[/green] Loaded {len(gen_reqs)} generated requirements")

        # Parse ground truth
        gt_reqs = load_ground_truth(gt_data.get("requirements", []))
        console.print(f"[green]✓[/green] Loaded {len(gt_reqs)} ground truth requirements")

        if not gen_reqs:
            console.print("[yellow]No generated requirements to compare.[/yellow]")
            raise typer.Exit(0)

        # Match requirements
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            progress.add_task("Computing semantic similarity...", total=None)

            matcher = SemanticMatcher(
                match_threshold=match_threshold,
                partial_threshold=partial_threshold,
            )
            matches = matcher.match_requirements(gen_reqs, gt_reqs)

        # Calculate metrics
        calculator = MetricsCalculator()
        metrics = calculator.calculate(matches, len(gt_reqs))

        # Display results
        console.print("\n[bold]Match Results:[/bold]\n")

        match_table = Table()
        match_table.add_column("Generated", style="cyan")
        match_table.add_column("Ground Truth", style="green")
        match_table.add_column("Similarity", justify="right")
        match_table.add_column("Status", justify="center")

        for m in matches:
            status_style = {
                MatchType.MATCH: "green",
                MatchType.PARTIAL: "yellow",
                MatchType.NO_MATCH: "red",
            }[m.match_type]
            status_text = {
                MatchType.MATCH: "✓ Match",
                MatchType.PARTIAL: "~ Partial",
                MatchType.NO_MATCH: "✗ No Match",
            }[m.match_type]

            gt_display = m.ground_truth_id if m.ground_truth_id else "-"
            match_table.add_row(
                m.generated_id,
                gt_display,
                f"{m.similarity_score:.3f}",
                f"[{status_style}]{status_text}[/{status_style}]",
            )

        console.print(match_table)

        # Display metrics
        console.print("\n[bold]Metrics:[/bold]\n")
        metrics_table = Table()
        metrics_table.add_column("Metric", style="cyan")
        metrics_table.add_column("Value", justify="right")

        metrics_table.add_row("True Positives", str(metrics.true_positives))
        metrics_table.add_row("False Positives", str(metrics.false_positives))
        metrics_table.add_row("False Negatives", str(metrics.false_negatives))
        metrics_table.add_row("Partial Matches", str(metrics.partial_matches))
        metrics_table.add_row("─" * 15, "─" * 10)
        metrics_table.add_row("Precision", f"{metrics.precision:.2%}")
        metrics_table.add_row("Recall", f"{metrics.recall:.2%}")
        metrics_table.add_row("[bold]F1 Score[/bold]", f"[bold]{metrics.f1_score:.2%}[/bold]")
        metrics_table.add_row("Hallucination Rate", f"{metrics.hallucination_rate:.2%}")

        console.print(metrics_table)

        # Save results if output specified
        if output:
            output_data = {
                "config": {
                    "match_threshold": match_threshold,
                    "partial_threshold": partial_threshold,
                    "generated_file": str(generated),
                    "ground_truth_file": str(ground_truth),
                },
                "metrics": {
                    "true_positives": metrics.true_positives,
                    "false_positives": metrics.false_positives,
                    "false_negatives": metrics.false_negatives,
                    "partial_matches": metrics.partial_matches,
                    "precision": metrics.precision,
                    "recall": metrics.recall,
                    "f1_score": metrics.f1_score,
                    "hallucination_rate": metrics.hallucination_rate,
                },
                "matches": [
                    {
                        "generated_id": m.generated_id,
                        "ground_truth_id": m.ground_truth_id,
                        "similarity_score": m.similarity_score,
                        "match_type": m.match_type.value,
                    }
                    for m in matches
                ],
            }
            output.parent.mkdir(parents=True, exist_ok=True)
            with open(output, "w", encoding="utf-8") as f:
                json.dump(output_data, f, indent=2)
            console.print(f"\n[green]✓[/green] Results saved to {output}")

    except json.JSONDecodeError as e:
        console.print(f"[red]JSON Parse Error:[/red] {e}")
        raise typer.Exit(1)


@evaluate_app.command("report")
def evaluate_report(
    results_file: Annotated[
        Path, typer.Argument(help="Evaluation results JSON file")
    ],
    output_dir: Annotated[
        Optional[Path],
        typer.Option(
            "--output",
            "-o",
            help="Output directory for reports",
        ),
    ] = None,
    report_name: Annotated[
        str,
        typer.Option(
            "--name",
            "-n",
            help="Base name for report files",
        ),
    ] = "evaluation",
) -> None:
    """Generate reports from saved evaluation results.

    Takes a JSON file from a previous evaluation run and generates
    Markdown and JSON reports for analysis.

    The input file should be the JSON output from 'evaluate run' command.
    """
    import json
    from datetime import datetime

    from secreqgen.evaluation import (
        ComparisonResult,
        EvaluationPipelineResult,
        EvaluationReportGenerator,
        GroundTruthRequirement,
        MatchResult,
        MatchType,
        MetricsBreakdown,
        StoryEvaluationResult,
    )
    from secreqgen.evaluation.baseline import ComparativeResults

    # Validate file
    if not results_file.exists():
        console.print(f"[red]Error:[/red] Results file not found: {results_file}")
        raise typer.Exit(1)

    # Default output directory
    if output_dir is None:
        output_dir = results_file.parent

    console.print(
        Panel(
            f"[cyan]Results:[/cyan] {results_file}\n"
            f"[cyan]Output:[/cyan] {output_dir}",
            title="Generate Reports",
        )
    )

    try:
        # Load results
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            progress.add_task("Loading results...", total=None)

            with open(results_file, encoding="utf-8") as f:
                data = json.load(f)

        # Reconstruct EvaluationPipelineResult from JSON
        # This requires parsing the nested structures
        def parse_metrics(d: dict) -> MetricsBreakdown:
            tp = d["true_positives"]
            fp = d["false_positives"]
            fn = d["false_negatives"]
            partial = d["partial_matches"]
            total_gen = d.get("total_generated", tp + fp)
            total_gt = d.get("total_ground_truth", tp + fn)

            # Compute metrics if not provided (for backwards compatibility)
            precision = d.get("precision", tp / (tp + fp) if (tp + fp) > 0 else 0.0)
            recall = d.get("recall", tp / (tp + fn) if (tp + fn) > 0 else 0.0)
            f1 = d.get("f1_score", 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0)
            hallucination = d.get("hallucination_rate", fp / total_gen if total_gen > 0 else 0.0)

            return MetricsBreakdown(
                true_positives=tp,
                false_positives=fp,
                false_negatives=fn,
                partial_matches=partial,
                total_generated=total_gen,
                total_ground_truth=total_gt,
                precision=precision,
                recall=recall,
                f1_score=f1,
                hallucination_rate=hallucination,
            )

        def parse_match(d: dict) -> MatchResult:
            return MatchResult(
                generated_id=d["generated_id"],
                generated_text=d.get("generated_text", ""),
                ground_truth_id=d.get("ground_truth_id"),
                ground_truth_text=d.get("ground_truth_text"),
                similarity_score=d["similarity_score"],
                match_type=MatchType(d["match_type"]),
            )

        def parse_story_result(d: dict) -> StoryEvaluationResult:
            return StoryEvaluationResult(
                story_id=d["story_id"],
                story_title=d["story_title"],
                ground_truth=[
                    GroundTruthRequirement(id="", title="", description="")
                ] * d["ground_truth_count"],  # Placeholder
                baseline_result=None,
                rag_result=None,
                baseline_matches=[parse_match(m) for m in d.get("baseline_matches", [])],
                rag_matches=[parse_match(m) for m in d.get("rag_matches", [])],
                baseline_metrics=parse_metrics(d["baseline_metrics"]),
                rag_metrics=parse_metrics(d["rag_metrics"]),
            )

        # Parse the data
        aggregate_baseline = parse_metrics(data["aggregate_metrics"]["baseline"])
        aggregate_rag = parse_metrics(data["aggregate_metrics"]["rag"])

        # Compute meets_threshold from improvement_pct and threshold
        f1_improvement = data["comparison"]["f1_improvement_pct"]
        threshold = data["comparison"]["improvement_threshold"]
        meets_threshold = (1 + f1_improvement / 100.0) >= threshold

        comparison = ComparisonResult(
            baseline_metrics=aggregate_baseline,
            rag_metrics=aggregate_rag,
            precision_delta=data["comparison"]["precision_delta"],
            recall_delta=data["comparison"]["recall_delta"],
            f1_delta=data["comparison"]["f1_delta"],
            hallucination_delta=data["comparison"]["hallucination_delta"],
            precision_improvement_pct=data["comparison"]["precision_improvement_pct"],
            recall_improvement_pct=data["comparison"]["recall_improvement_pct"],
            f1_improvement_pct=f1_improvement,
            meets_threshold=meets_threshold,
            improvement_threshold=threshold,
        )

        per_story = [parse_story_result(sr) for sr in data.get("per_story_results", [])]

        # Create a minimal ComparativeResults (for compatibility)
        comparative = ComparativeResults(
            user_stories=[],  # Not available from saved results
            baseline_results=[],
            rag_results=[],
            baseline_time_ms=0,
            rag_time_ms=0,
        )

        result = EvaluationPipelineResult(
            per_story_results=per_story,
            aggregate_baseline_metrics=aggregate_baseline,
            aggregate_rag_metrics=aggregate_rag,
            comparison=comparison,
            comparative_results=comparative,
            run_timestamp=datetime.fromisoformat(data.get("run_timestamp", datetime.now().isoformat())),
            config=data.get("config", {}),
        )

        console.print(f"[green]✓[/green] Loaded results with {len(per_story)} stories")

        # Generate reports
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            progress.add_task("Generating reports...", total=None)

            report_generator = EvaluationReportGenerator(output_dir)
            report_paths = report_generator.generate_full_report(result, report_name)

        console.print("\n[bold]Reports generated:[/bold]")
        for path in report_paths:
            console.print(f"  • {path}")

    except json.JSONDecodeError as e:
        console.print(f"[red]JSON Parse Error:[/red] {e}")
        raise typer.Exit(1)
    except KeyError as e:
        console.print(f"[red]Invalid results format:[/red] Missing key {e}")
        raise typer.Exit(1)


if __name__ == "__main__":
    app()
