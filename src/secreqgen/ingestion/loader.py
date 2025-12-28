"""Document loaders for various file formats.

Supports: .md, .pdf, .docx, .yaml/.yml
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar

import yaml
from docx import Document as DocxDocument
from pypdf import PdfReader

from secreqgen.exceptions import IngestionError


@dataclass
class LoadedDocument:
    """A document loaded from disk with its content and metadata."""

    source_path: Path
    content: str
    format: str
    metadata: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Convert source_path to Path if string."""
        if isinstance(self.source_path, str):
            self.source_path = Path(self.source_path)


class BaseLoader(ABC):
    """Abstract base class for document loaders."""

    SUPPORTED_EXTENSIONS: ClassVar[list[str]] = []

    @abstractmethod
    def load(self, path: Path) -> LoadedDocument:
        """Load a document from the given path.

        Args:
            path: Path to the document file.

        Returns:
            LoadedDocument with content and metadata.

        Raises:
            IngestionError: If the file cannot be loaded.
        """
        pass

    @classmethod
    def supports(cls, path: Path) -> bool:
        """Check if this loader supports the given file extension."""
        return path.suffix.lower() in cls.SUPPORTED_EXTENSIONS


class MarkdownLoader(BaseLoader):
    """Loader for Markdown (.md) files."""

    SUPPORTED_EXTENSIONS: ClassVar[list[str]] = [".md", ".markdown"]

    def load(self, path: Path) -> LoadedDocument:
        """Load a markdown file as plain text."""
        try:
            content = path.read_text(encoding="utf-8")
            return LoadedDocument(
                source_path=path,
                content=content,
                format="markdown",
                metadata={
                    "filename": path.name,
                    "size_bytes": path.stat().st_size,
                },
            )
        except Exception as e:
            raise IngestionError(
                f"Failed to load markdown file: {path}",
                details=str(e),
            ) from e


class PDFLoader(BaseLoader):
    """Loader for PDF (.pdf) files.

    Preserves page markers in the extracted text.
    """

    SUPPORTED_EXTENSIONS: ClassVar[list[str]] = [".pdf"]

    def load(self, path: Path) -> LoadedDocument:
        """Load a PDF file, extracting text with page markers."""
        try:
            reader = PdfReader(path)
            pages: list[str] = []

            for i, page in enumerate(reader.pages, start=1):
                text = page.extract_text() or ""
                if text.strip():
                    pages.append(f"[Page {i}]\n{text}")

            content = "\n\n".join(pages)

            return LoadedDocument(
                source_path=path,
                content=content,
                format="pdf",
                metadata={
                    "filename": path.name,
                    "size_bytes": path.stat().st_size,
                    "page_count": len(reader.pages),
                },
            )
        except Exception as e:
            raise IngestionError(
                f"Failed to load PDF file: {path}",
                details=str(e),
            ) from e


class DocxLoader(BaseLoader):
    """Loader for Word (.docx) files.

    Converts document structure to markdown-style headers.
    """

    SUPPORTED_EXTENSIONS: ClassVar[list[str]] = [".docx"]

    # Mapping of Word heading styles to markdown levels
    HEADING_MAP: ClassVar[dict[str, str]] = {
        "Heading 1": "#",
        "Heading 2": "##",
        "Heading 3": "###",
        "Heading 4": "####",
        "Heading 5": "#####",
        "Heading 6": "######",
        "Title": "#",
    }

    def load(self, path: Path) -> LoadedDocument:
        """Load a DOCX file, converting headings to markdown format."""
        try:
            doc = DocxDocument(path)
            paragraphs: list[str] = []

            for para in doc.paragraphs:
                text = para.text.strip()
                if not text:
                    continue

                # Check if it's a heading style
                style_name = para.style.name if para.style else None
                if style_name and style_name in self.HEADING_MAP:
                    prefix = self.HEADING_MAP[style_name]
                    paragraphs.append(f"{prefix} {text}")
                else:
                    paragraphs.append(text)

            content = "\n\n".join(paragraphs)

            return LoadedDocument(
                source_path=path,
                content=content,
                format="docx",
                metadata={
                    "filename": path.name,
                    "size_bytes": path.stat().st_size,
                    "paragraph_count": len(doc.paragraphs),
                },
            )
        except Exception as e:
            raise IngestionError(
                f"Failed to load DOCX file: {path}",
                details=str(e),
            ) from e


class YAMLLoader(BaseLoader):
    """Loader for YAML (.yaml, .yml) files.

    Used primarily for system context files.
    Stores both raw content and parsed data.
    """

    SUPPORTED_EXTENSIONS: ClassVar[list[str]] = [".yaml", ".yml"]

    def load(self, path: Path) -> LoadedDocument:
        """Load a YAML file, preserving raw content and parsing data."""
        try:
            raw_content = path.read_text(encoding="utf-8")

            # Parse YAML to validate and extract structure
            parsed_data = yaml.safe_load(raw_content)

            return LoadedDocument(
                source_path=path,
                content=raw_content,
                format="yaml",
                metadata={
                    "filename": path.name,
                    "size_bytes": path.stat().st_size,
                    "parsed_data": parsed_data,
                    "is_system_context": self._is_system_context(parsed_data),
                },
            )
        except yaml.YAMLError as e:
            raise IngestionError(
                f"Invalid YAML syntax in file: {path}",
                details=str(e),
            ) from e
        except Exception as e:
            raise IngestionError(
                f"Failed to load YAML file: {path}",
                details=str(e),
            ) from e

    def _is_system_context(self, data: dict | None) -> bool:
        """Check if the YAML file appears to be a system context file."""
        if not isinstance(data, dict):
            return False
        # System context files typically have these top-level keys
        context_keys = {"system", "application", "project", "context"}
        return bool(context_keys & set(data.keys()))


class DocumentLoaderRegistry:
    """Registry that routes documents to appropriate loaders based on file extension."""

    def __init__(self) -> None:
        """Initialize the registry with default loaders."""
        self._loaders: list[BaseLoader] = [
            MarkdownLoader(),
            PDFLoader(),
            DocxLoader(),
            YAMLLoader(),
        ]

    def register_loader(self, loader: BaseLoader) -> None:
        """Register a custom loader.

        Args:
            loader: A loader instance to add to the registry.
        """
        self._loaders.insert(0, loader)  # Custom loaders take priority

    def get_loader(self, path: Path) -> BaseLoader:
        """Get the appropriate loader for a file.

        Args:
            path: Path to the file.

        Returns:
            A loader that can handle the file.

        Raises:
            IngestionError: If no loader supports the file format.
        """
        for loader in self._loaders:
            if loader.supports(path):
                return loader

        supported = []
        for loader in self._loaders:
            supported.extend(loader.SUPPORTED_EXTENSIONS)

        raise IngestionError(
            f"Unsupported file format: {path.suffix}",
            details=f"Supported formats: {', '.join(sorted(set(supported)))}",
        )

    def load(self, path: Path) -> LoadedDocument:
        """Load a document using the appropriate loader.

        Args:
            path: Path to the document file.

        Returns:
            LoadedDocument with content and metadata.

        Raises:
            IngestionError: If the file cannot be loaded or format is unsupported.
        """
        if not path.exists():
            raise IngestionError(f"File not found: {path}")

        if not path.is_file():
            raise IngestionError(f"Path is not a file: {path}")

        loader = self.get_loader(path)
        return loader.load(path)

    def load_directory(
        self,
        directory: Path,
        recursive: bool = True,
    ) -> list[LoadedDocument]:
        """Load all supported documents from a directory.

        Args:
            directory: Path to the directory.
            recursive: If True, search subdirectories.

        Returns:
            List of loaded documents.

        Raises:
            IngestionError: If the directory doesn't exist.
        """
        if not directory.exists():
            raise IngestionError(f"Directory not found: {directory}")

        if not directory.is_dir():
            raise IngestionError(f"Path is not a directory: {directory}")

        documents: list[LoadedDocument] = []
        errors: list[str] = []

        # Collect all supported extensions
        supported_extensions = set()
        for loader in self._loaders:
            supported_extensions.update(loader.SUPPORTED_EXTENSIONS)

        # Find all matching files
        pattern = "**/*" if recursive else "*"
        for file_path in directory.glob(pattern):
            if not file_path.is_file():
                continue
            if file_path.suffix.lower() not in supported_extensions:
                continue

            try:
                doc = self.load(file_path)
                documents.append(doc)
            except IngestionError as e:
                errors.append(str(e))

        if errors and not documents:
            raise IngestionError(
                "Failed to load any documents from directory",
                details="\n".join(errors),
            )

        return documents

    @property
    def supported_formats(self) -> list[str]:
        """Get list of all supported file extensions."""
        extensions = set()
        for loader in self._loaders:
            extensions.update(loader.SUPPORTED_EXTENSIONS)
        return sorted(extensions)
