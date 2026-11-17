"""Document retrieval module for SecReqGen."""

from secreqgen.retrieval.retriever import RetrievalResult, SimpleRetriever
from secreqgen.retrieval.vectorstore import (
    QueryResult,
    SecReqGenVectorStore,
)
from secreqgen.retrieval.word_search import WordRetrievalResult, WordSearchRetriever

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
