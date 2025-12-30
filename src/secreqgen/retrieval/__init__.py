"""Document retrieval module for SecReqGen."""

from secreqgen.retrieval.retriever import RetrievalResult, SimpleRetriever
from secreqgen.retrieval.vectorstore import (
    QueryResult,
    SecReqGenVectorStore,
)

__all__ = [
    # Vector Store
    "QueryResult",
    "SecReqGenVectorStore",
    # Retriever
    "SimpleRetriever",
    "RetrievalResult",
]
