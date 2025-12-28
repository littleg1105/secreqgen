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
@generate_app.command("requirements")
def generate_requirements(
    input_file: Annotated[
        Path, typer.Argument(help="Input file with user stories")
    ],
    output: Annotated[
        Optional[Path],
        typer.Option(
            "--output",
            "-o",
            help="Output file for generated requirements",
        ),
    ] = None,
    baseline: bool = typer.Option(
        False,
        "--baseline",
        help="Generate baseline (zero-shot) requirements without RAG",
    ),
) -> None:
    """Generate security requirements from user stories."""
    mode = "baseline (zero-shot)" if baseline else "RAG-enhanced"
    console.print(
        Panel(
            f"[yellow]Not yet implemented[/yellow]\n\n"
            f"Mode: {mode}\n"
            f"Input: {input_file}\n"
            f"Output: {output or 'stdout'}",
            title="Generate Requirements",
        )
    )


# Evaluate commands
@evaluate_app.command("compare")
def evaluate_compare(
    generated: Annotated[
        Path, typer.Argument(help="Generated requirements file")
    ],
    ground_truth: Annotated[
        Path, typer.Argument(help="Ground truth requirements file")
    ],
) -> None:
    """Compare generated requirements against ground truth."""
    console.print(
        Panel(
            f"[yellow]Not yet implemented[/yellow]\n\n"
            f"Generated: {generated}\n"
            f"Ground Truth: {ground_truth}",
            title="Evaluate Requirements",
        )
    )


if __name__ == "__main__":
    app()
