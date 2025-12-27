"""CLI interface for SecReqGen using Typer."""

from pathlib import Path
from typing import Annotated, Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from secreqgen import __version__
from secreqgen.config import get_config

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
    """Ingest documents from a directory into the vector store."""
    console.print(
        Panel(
            f"[yellow]Not yet implemented[/yellow]\n\n"
            f"Will ingest {doc_type} documents from: {directory}",
            title="Ingest Documents",
        )
    )


@ingest_app.command("system-context")
def ingest_system_context(
    file: Annotated[Path, typer.Argument(help="System context YAML file")],
) -> None:
    """Load system context from a YAML file."""
    console.print(
        Panel(
            f"[yellow]Not yet implemented[/yellow]\n\n"
            f"Will load system context from: {file}",
            title="Ingest System Context",
        )
    )


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
