"""The Typer apps, the console and the document type map."""

import typer
from rich.console import Console

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
