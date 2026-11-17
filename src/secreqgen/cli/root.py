"""Root commands: version and config."""

from rich.table import Table

from secreqgen import __version__
from secreqgen.cli._app import app, console
from secreqgen.config import get_config


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
