"""Command line of SecReqGen, built with Typer.

The apps, the console and DOC_TYPE_MAP live in _app.py. The commands live in
root.py (version, config), ingest.py, generate.py and evaluate.py.
_ollama_client is the patch point of the tests: the generate commands look it
up here at call time.
"""

from typing import Optional

from secreqgen import __version__
from secreqgen.application.clients import (
    DEFAULT_TIMEOUT_S,
    WORD_SEARCH_NUM_CTX,
    WORD_SEARCH_TIMEOUT_S,
    build_ollama_client,
)
from secreqgen.cli._app import (
    DOC_TYPE_MAP,
    app,
    console,
    evaluate_app,
    generate_app,
    ingest_app,
)
from secreqgen.cli.evaluate import (
    evaluate_compare,
    evaluate_compare_runs,
    evaluate_report,
    evaluate_run,
)
from secreqgen.cli.generate import (
    check_ollama,
    generate_context,
    generate_requirements,
    generate_single,
    generate_threats,
)
from secreqgen.cli.ingest import (
    ingest_clear,
    ingest_documents,
    ingest_stats,
    ingest_system_context,
)
from secreqgen.cli.root import config, version
from secreqgen.config import get_config


def _ollama_client(cfg, tested_setup: bool, model_name: Optional[str] = None):
    """Build the Ollama client of a generate command.

    A value in the settings (SECREQGEN_LLM_NUM_CTX, SECREQGEN_LLM_TIMEOUT,
    SECREQGEN_LLM_STRUCTURED_OUTPUT) wins. Without one, the word search uses
    the values of the tested runs, and the vector store path keeps the
    behaviour that it had before.
    """
    return build_ollama_client(cfg, tested_setup, model_name)


__all__ = [
    "DEFAULT_TIMEOUT_S",
    "DOC_TYPE_MAP",
    "WORD_SEARCH_NUM_CTX",
    "WORD_SEARCH_TIMEOUT_S",
    "__version__",
    "_ollama_client",
    "app",
    "check_ollama",
    "config",
    "console",
    "evaluate_app",
    "evaluate_compare",
    "evaluate_compare_runs",
    "evaluate_report",
    "evaluate_run",
    "generate_app",
    "generate_context",
    "generate_requirements",
    "generate_single",
    "generate_threats",
    "get_config",
    "ingest_app",
    "ingest_clear",
    "ingest_documents",
    "ingest_stats",
    "ingest_system_context",
    "version",
]
