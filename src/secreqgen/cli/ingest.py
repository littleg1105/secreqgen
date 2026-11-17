"""Commands of the ingest group."""

from pathlib import Path
from typing import Annotated

import typer
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
)
from rich.table import Table

from secreqgen.cli._app import DOC_TYPE_MAP, console, ingest_app
from secreqgen.config import get_config
from secreqgen.exceptions import IngestionError, VectorStoreError


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
    from secreqgen.application.retrievers import build_embedder, build_vector_store
    from secreqgen.ingestion import (
        ArchitectureAwareChunker,
        DocumentLoaderRegistry,
        MetadataExtractor,
    )
    from secreqgen.ingestion.metadata import DocumentType

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
    embedder = build_embedder(cfg, batch_size=cfg.embedding.batch_size)
    vector_store = build_vector_store(cfg)

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
    from secreqgen.application.retrievers import build_embedder, build_vector_store
    from secreqgen.ingestion import (
        ArchitectureAwareChunker,
        DocumentLoaderRegistry,
    )
    from secreqgen.ingestion.metadata import DocumentType

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
    embedder = build_embedder(cfg, batch_size=cfg.embedding.batch_size)
    vector_store = build_vector_store(cfg)

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
    from secreqgen.application.retrievers import build_vector_store

    cfg = get_config()

    # Check if vector store directory exists
    if not cfg.vectorstore.persist_directory.exists():
        console.print(
            "[yellow]Vector store not initialized yet.[/yellow]\n"
            "Run 'secreqgen ingest documents' or 'secreqgen ingest system-context' first."
        )
        raise typer.Exit(0)

    vector_store = build_vector_store(cfg)

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
    from secreqgen.application.retrievers import build_vector_store

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

    vector_store = build_vector_store(cfg)

    vector_store.clear()
    console.print("[green]✓[/green] Vector store cleared.")


# Generate commands
