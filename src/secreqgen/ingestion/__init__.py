"""Document ingestion module for SecReqGen."""

from secreqgen.ingestion.chunker import (
    ArchitectureAwareChunker,
    DocumentChunk,
    Section,
)
from secreqgen.ingestion.embedder import (
    DocumentEmbedder,
    EmbeddedChunk,
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
from secreqgen.ingestion.metadata import (
    ComplianceFramework,
    DocumentMetadata,
    DocumentType,
    MetadataExtractor,
    SecurityDomain,
)

__all__ = [
    # Chunker
    "ArchitectureAwareChunker",
    "DocumentChunk",
    "Section",
    # Embedder
    "DocumentEmbedder",
    "EmbeddedChunk",
    # Loader
    "BaseLoader",
    "DocxLoader",
    "DocumentLoaderRegistry",
    "LoadedDocument",
    "MarkdownLoader",
    "PDFLoader",
    "YAMLLoader",
    # Metadata
    "ComplianceFramework",
    "DocumentMetadata",
    "DocumentType",
    "MetadataExtractor",
    "SecurityDomain",
]
