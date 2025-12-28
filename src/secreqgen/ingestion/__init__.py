"""Document ingestion module for SecReqGen."""

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
    "BaseLoader",
    "DocxLoader",
    "DocumentLoaderRegistry",
    "LoadedDocument",
    "MarkdownLoader",
    "PDFLoader",
    "YAMLLoader",
]
