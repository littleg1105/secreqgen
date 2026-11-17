"""Document retrieval module for SecReqGen.

The names below load on first use. This keeps "import secreqgen.threats" and
the word search light: they do not load chromadb or torch.
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from secreqgen.retrieval.retriever import RetrievalResult, SimpleRetriever
    from secreqgen.retrieval.vectorstore import QueryResult, SecReqGenVectorStore
    from secreqgen.retrieval.word_search import (
        WordRetrievalResult,
        WordSearchRetriever,
    )

_LAZY = {
    "QueryResult": "secreqgen.retrieval.vectorstore",
    "SecReqGenVectorStore": "secreqgen.retrieval.vectorstore",
    "SimpleRetriever": "secreqgen.retrieval.retriever",
    "RetrievalResult": "secreqgen.retrieval.retriever",
    "WordSearchRetriever": "secreqgen.retrieval.word_search",
    "WordRetrievalResult": "secreqgen.retrieval.word_search",
}

__all__ = [
    # Vector Store
    "QueryResult",
    "SecReqGenVectorStore",
    # Retriever
    "SimpleRetriever",
    "RetrievalResult",
    # Word search (the tested configuration)
    "WordSearchRetriever",
    "WordRetrievalResult",
]


def __getattr__(name: str):
    """Import a public name from its defining module on first use."""
    module_name = _LAZY.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(importlib.import_module(module_name), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
