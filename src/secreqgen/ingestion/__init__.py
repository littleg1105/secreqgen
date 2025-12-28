"""Document ingestion module for SecReqGen."""

from secreqgen.ingestion.chunker import (
    ArchitectureAwareChunker,
    DocumentChunk,
    Section,
)
from secreqgen.ingestion.loader import (
    BaseLoader,
    DocxLoader,
    DocumentLoaderRegistry,
    LoadedDocument,
    MarkdownLoader,
    PDFLoader,
    YAMLLoader,
)

__all__ = [
    # Chunker
    "ArchitectureAwareChunker",
    "DocumentChunk",
    "Section",
    # Loader
    "BaseLoader",
    "DocxLoader",
    "DocumentLoaderRegistry",
    "LoadedDocument",
    "MarkdownLoader",
    "PDFLoader",
    "YAMLLoader",
]
