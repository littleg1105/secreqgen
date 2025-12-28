"""Unit tests for document loaders."""

from pathlib import Path

import pytest

from secreqgen.exceptions import IngestionError
from secreqgen.ingestion.loader import (
    BaseLoader,
    DocxLoader,
    DocumentLoaderRegistry,
    LoadedDocument,
    MarkdownLoader,
    PDFLoader,
    YAMLLoader,
)


class TestLoadedDocument:
    """Tests for LoadedDocument dataclass."""

    def test_create_loaded_document(self, tmp_path: Path) -> None:
        """Test creating a LoadedDocument with basic fields."""
        doc = LoadedDocument(
            source_path=tmp_path / "test.md",
            content="# Test",
            format="markdown",
        )
        assert doc.source_path == tmp_path / "test.md"
        assert doc.content == "# Test"
        assert doc.format == "markdown"
        assert doc.metadata == {}

    def test_loaded_document_with_metadata(self, tmp_path: Path) -> None:
        """Test LoadedDocument with custom metadata."""
        doc = LoadedDocument(
            source_path=tmp_path / "test.pdf",
            content="PDF content",
            format="pdf",
            metadata={"page_count": 5, "author": "Test"},
        )
        assert doc.metadata["page_count"] == 5
        assert doc.metadata["author"] == "Test"

    def test_string_path_converted_to_path(self) -> None:
        """Test that string paths are converted to Path objects."""
        doc = LoadedDocument(
            source_path="/path/to/file.md",  # type: ignore[arg-type]
            content="content",
            format="markdown",
        )
        assert isinstance(doc.source_path, Path)
        assert doc.source_path == Path("/path/to/file.md")


class TestMarkdownLoader:
    """Tests for MarkdownLoader."""

    def test_supports_md_extension(self) -> None:
        """Test that MarkdownLoader supports .md files."""
        loader = MarkdownLoader()
        assert loader.supports(Path("test.md"))
        assert loader.supports(Path("test.markdown"))
        assert not loader.supports(Path("test.txt"))
        assert not loader.supports(Path("test.pdf"))

    def test_load_markdown_file(self, sample_markdown_file: Path) -> None:
        """Test loading a markdown file."""
        loader = MarkdownLoader()
        doc = loader.load(sample_markdown_file)

        assert doc.format == "markdown"
        assert doc.source_path == sample_markdown_file
        assert "# Security Policy" in doc.content
        assert "## Authentication" in doc.content
        assert doc.metadata["filename"] == "sample.md"
        assert doc.metadata["size_bytes"] > 0

    def test_load_nonexistent_file_raises_error(self, tmp_path: Path) -> None:
        """Test that loading a nonexistent file raises IngestionError."""
        loader = MarkdownLoader()
        with pytest.raises(IngestionError):
            loader.load(tmp_path / "nonexistent.md")


class TestYAMLLoader:
    """Tests for YAMLLoader."""

    def test_supports_yaml_extensions(self) -> None:
        """Test that YAMLLoader supports .yaml and .yml files."""
        loader = YAMLLoader()
        assert loader.supports(Path("test.yaml"))
        assert loader.supports(Path("test.yml"))
        assert not loader.supports(Path("test.json"))

    def test_load_yaml_file(self, sample_yaml_file: Path) -> None:
        """Test loading a YAML file."""
        loader = YAMLLoader()
        doc = loader.load(sample_yaml_file)

        assert doc.format == "yaml"
        assert doc.source_path == sample_yaml_file
        assert "parsed_data" in doc.metadata
        assert doc.metadata["parsed_data"]["system"]["name"] == "Test Application"
        assert doc.metadata["is_system_context"] is True

    def test_load_yaml_detects_system_context(self, tmp_path: Path) -> None:
        """Test that system context files are detected."""
        loader = YAMLLoader()

        # File with 'system' key is system context
        sys_file = tmp_path / "with_system.yaml"
        sys_file.write_text("system:\n  name: Test")
        doc = loader.load(sys_file)
        assert doc.metadata["is_system_context"] is True

        # File without system keys is not system context
        other_file = tmp_path / "other.yaml"
        other_file.write_text("config:\n  setting: value")
        doc = loader.load(other_file)
        assert doc.metadata["is_system_context"] is False

    def test_load_invalid_yaml_raises_error(self, tmp_path: Path) -> None:
        """Test that invalid YAML raises IngestionError."""
        loader = YAMLLoader()
        invalid_file = tmp_path / "invalid.yaml"
        invalid_file.write_text("invalid: yaml: content: [")

        with pytest.raises(IngestionError) as exc_info:
            loader.load(invalid_file)
        assert "Invalid YAML syntax" in str(exc_info.value)


class TestPDFLoader:
    """Tests for PDFLoader."""

    def test_supports_pdf_extension(self) -> None:
        """Test that PDFLoader supports .pdf files."""
        loader = PDFLoader()
        assert loader.supports(Path("test.pdf"))
        assert not loader.supports(Path("test.docx"))


class TestDocxLoader:
    """Tests for DocxLoader."""

    def test_supports_docx_extension(self) -> None:
        """Test that DocxLoader supports .docx files."""
        loader = DocxLoader()
        assert loader.supports(Path("test.docx"))
        assert not loader.supports(Path("test.doc"))
        assert not loader.supports(Path("test.pdf"))


class TestDocumentLoaderRegistry:
    """Tests for DocumentLoaderRegistry."""

    def test_default_loaders_registered(self) -> None:
        """Test that default loaders are registered."""
        registry = DocumentLoaderRegistry()
        supported = registry.supported_formats

        assert ".md" in supported
        assert ".markdown" in supported
        assert ".pdf" in supported
        assert ".docx" in supported
        assert ".yaml" in supported
        assert ".yml" in supported

    def test_get_loader_for_markdown(self) -> None:
        """Test getting the correct loader for markdown files."""
        registry = DocumentLoaderRegistry()
        loader = registry.get_loader(Path("test.md"))
        assert isinstance(loader, MarkdownLoader)

    def test_get_loader_for_yaml(self) -> None:
        """Test getting the correct loader for YAML files."""
        registry = DocumentLoaderRegistry()
        loader = registry.get_loader(Path("test.yaml"))
        assert isinstance(loader, YAMLLoader)

    def test_get_loader_for_unsupported_format_raises_error(self) -> None:
        """Test that unsupported formats raise IngestionError."""
        registry = DocumentLoaderRegistry()
        with pytest.raises(IngestionError) as exc_info:
            registry.get_loader(Path("test.xyz"))
        assert "Unsupported file format" in str(exc_info.value)

    def test_load_file(self, sample_markdown_file: Path) -> None:
        """Test loading a file through the registry."""
        registry = DocumentLoaderRegistry()
        doc = registry.load(sample_markdown_file)

        assert doc.format == "markdown"
        assert "# Security Policy" in doc.content

    def test_load_nonexistent_file_raises_error(self, tmp_path: Path) -> None:
        """Test that loading a nonexistent file raises IngestionError."""
        registry = DocumentLoaderRegistry()
        with pytest.raises(IngestionError) as exc_info:
            registry.load(tmp_path / "nonexistent.md")
        assert "File not found" in str(exc_info.value)

    def test_load_directory_raises_error(self, tmp_path: Path) -> None:
        """Test that trying to load a directory raises IngestionError."""
        registry = DocumentLoaderRegistry()
        with pytest.raises(IngestionError) as exc_info:
            registry.load(tmp_path)
        assert "not a file" in str(exc_info.value)

    def test_load_directory_with_files(self, sample_documents_mixed: Path) -> None:
        """Test loading all documents from a directory."""
        registry = DocumentLoaderRegistry()
        docs = registry.load_directory(sample_documents_mixed)

        # Should load markdown and yaml, skip txt
        assert len(docs) == 2
        formats = {doc.format for doc in docs}
        assert formats == {"markdown", "yaml"}

    def test_load_directory_nonexistent_raises_error(self, tmp_path: Path) -> None:
        """Test that loading a nonexistent directory raises IngestionError."""
        registry = DocumentLoaderRegistry()
        with pytest.raises(IngestionError) as exc_info:
            registry.load_directory(tmp_path / "nonexistent")
        assert "Directory not found" in str(exc_info.value)

    def test_load_directory_with_path_to_file_raises_error(
        self, sample_markdown_file: Path
    ) -> None:
        """Test that load_directory with a file path raises IngestionError."""
        registry = DocumentLoaderRegistry()
        with pytest.raises(IngestionError) as exc_info:
            registry.load_directory(sample_markdown_file)
        assert "not a directory" in str(exc_info.value)

    def test_register_custom_loader(self) -> None:
        """Test registering a custom loader."""

        class CustomLoader(BaseLoader):
            SUPPORTED_EXTENSIONS = [".custom"]

            def load(self, path: Path) -> LoadedDocument:
                return LoadedDocument(
                    source_path=path, content="custom", format="custom"
                )

        registry = DocumentLoaderRegistry()
        registry.register_loader(CustomLoader())

        assert ".custom" in registry.supported_formats
        loader = registry.get_loader(Path("test.custom"))
        assert isinstance(loader, CustomLoader)

    def test_supported_formats_property(self) -> None:
        """Test that supported_formats returns sorted unique extensions."""
        registry = DocumentLoaderRegistry()
        formats = registry.supported_formats

        assert isinstance(formats, list)
        assert formats == sorted(formats)  # Should be sorted
        assert len(formats) == len(set(formats))  # Should be unique
