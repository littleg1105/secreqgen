"""Unit tests for architecture-aware chunker."""

from pathlib import Path

import pytest

from secreqgen.ingestion.chunker import (
    ArchitectureAwareChunker,
    DocumentChunk,
    Section,
)
from secreqgen.ingestion.loader import LoadedDocument


class TestDocumentChunk:
    """Tests for DocumentChunk dataclass."""

    def test_create_document_chunk(self) -> None:
        """Test creating a DocumentChunk with required fields."""
        chunk = DocumentChunk(
            chunk_id="chunk_abc123",
            content="Test content",
            source_document="/path/to/doc.md",
            section_hierarchy=["Security", "Authentication"],
            chunk_index=0,
            token_count=10,
        )
        assert chunk.chunk_id == "chunk_abc123"
        assert chunk.content == "Test content"
        assert chunk.section_hierarchy == ["Security", "Authentication"]
        assert chunk.metadata == {}

    def test_chunk_with_metadata(self) -> None:
        """Test DocumentChunk with custom metadata."""
        chunk = DocumentChunk(
            chunk_id="chunk_abc123",
            content="Content",
            source_document="/path/to/doc.md",
            section_hierarchy=[],
            chunk_index=0,
            token_count=5,
            metadata={"section_title": "Introduction"},
        )
        assert chunk.metadata["section_title"] == "Introduction"

    def test_chunk_none_hierarchy_becomes_list(self) -> None:
        """Test that None hierarchy is converted to empty list."""
        chunk = DocumentChunk(
            chunk_id="chunk_abc123",
            content="Content",
            source_document="/path/to/doc.md",
            section_hierarchy=None,  # type: ignore[arg-type]
            chunk_index=0,
            token_count=5,
        )
        assert chunk.section_hierarchy == []


class TestSection:
    """Tests for Section dataclass."""

    def test_create_section(self) -> None:
        """Test creating a Section."""
        section = Section(
            title="Authentication",
            level=2,
            content="Auth content here",
            hierarchy=["Security", "Authentication"],
        )
        assert section.title == "Authentication"
        assert section.level == 2
        assert section.hierarchy == ["Security", "Authentication"]


class TestArchitectureAwareChunker:
    """Tests for ArchitectureAwareChunker."""

    @pytest.fixture
    def chunker(self) -> ArchitectureAwareChunker:
        """Create a chunker with default settings."""
        return ArchitectureAwareChunker(max_chunk_tokens=512, overlap_tokens=50)

    @pytest.fixture
    def small_chunker(self) -> ArchitectureAwareChunker:
        """Create a chunker with small chunk size for testing splits."""
        return ArchitectureAwareChunker(max_chunk_tokens=50, overlap_tokens=10)

    def test_count_tokens(self, chunker: ArchitectureAwareChunker) -> None:
        """Test token counting."""
        # Simple text
        tokens = chunker.count_tokens("Hello world")
        assert tokens > 0
        assert tokens < 10  # Should be ~2-3 tokens

        # Longer text should have more tokens
        long_text = "This is a longer piece of text with more words and tokens."
        long_tokens = chunker.count_tokens(long_text)
        assert long_tokens > tokens

    def test_chunk_yaml_document(self, chunker: ArchitectureAwareChunker) -> None:
        """Test that YAML documents become single chunks."""
        yaml_content = """system:
  name: Test App
  description: A test application
security:
  authentication: oauth2
"""
        doc = LoadedDocument(
            source_path=Path("/test/context.yaml"),
            content=yaml_content,
            format="yaml",
            metadata={"is_system_context": True},
        )

        chunks = chunker.chunk_document(doc)

        assert len(chunks) == 1
        assert chunks[0].content == yaml_content.strip()
        assert chunks[0].metadata["is_complete_document"] is True
        assert chunks[0].section_hierarchy == []

    def test_chunk_simple_markdown(self, chunker: ArchitectureAwareChunker) -> None:
        """Test chunking a simple markdown document."""
        md_content = """# Security Policy

## Authentication
Users must authenticate with strong credentials.

## Authorization
Access is controlled by RBAC.
"""
        doc = LoadedDocument(
            source_path=Path("/test/policy.md"),
            content=md_content,
            format="markdown",
        )

        chunks = chunker.chunk_document(doc)

        # Should have 2 chunks (one for each section with content)
        assert len(chunks) >= 2

        # Check that hierarchies are preserved
        hierarchies = [c.section_hierarchy for c in chunks]
        assert ["Security Policy", "Authentication"] in hierarchies
        assert ["Security Policy", "Authorization"] in hierarchies

    def test_chunk_preserves_hierarchy(self, chunker: ArchitectureAwareChunker) -> None:
        """Test that nested headers create proper hierarchy."""
        md_content = """# Root

## Level 2

### Level 3
Content at level 3.

### Another Level 3
More content.

## Different Level 2

### Nested Here
Final content.
"""
        doc = LoadedDocument(
            source_path=Path("/test/nested.md"),
            content=md_content,
            format="markdown",
        )

        chunks = chunker.chunk_document(doc)

        # Find chunk with Level 3 content
        level3_chunk = next(c for c in chunks if "Content at level 3" in c.content)
        assert level3_chunk.section_hierarchy == ["Root", "Level 2", "Level 3"]

        # Find chunk with nested content
        nested_chunk = next(c for c in chunks if "Final content" in c.content)
        assert nested_chunk.section_hierarchy == ["Root", "Different Level 2", "Nested Here"]

    def test_chunk_includes_hierarchy_prefix(
        self, chunker: ArchitectureAwareChunker
    ) -> None:
        """Test that chunk content includes hierarchy prefix for embedding."""
        md_content = """# Policy

## Auth
Authentication info here.
"""
        doc = LoadedDocument(
            source_path=Path("/test/doc.md"),
            content=md_content,
            format="markdown",
        )

        chunks = chunker.chunk_document(doc)
        auth_chunk = next(c for c in chunks if "Authentication info" in c.content)

        # Should have hierarchy prefix
        assert "Section: Policy > Auth" in auth_chunk.content

    def test_chunk_large_section_splits(
        self, small_chunker: ArchitectureAwareChunker
    ) -> None:
        """Test that large sections are split into multiple chunks."""
        # Create content that exceeds 50 tokens
        long_content = " ".join(["word"] * 100)  # ~100 tokens
        md_content = f"""# Doc

## Large Section
{long_content}
"""
        doc = LoadedDocument(
            source_path=Path("/test/large.md"),
            content=md_content,
            format="markdown",
        )

        chunks = small_chunker.chunk_document(doc)

        # Should have multiple chunks due to size
        assert len(chunks) > 1

        # All chunks should have same hierarchy
        for chunk in chunks:
            if chunk.section_hierarchy:  # Skip any preamble
                assert chunk.section_hierarchy == ["Doc", "Large Section"]

    def test_chunk_ids_are_deterministic(
        self, chunker: ArchitectureAwareChunker
    ) -> None:
        """Test that chunk IDs are deterministic (same input = same ID)."""
        md_content = "# Test\n\nContent here."
        doc = LoadedDocument(
            source_path=Path("/test/doc.md"),
            content=md_content,
            format="markdown",
        )

        chunks1 = chunker.chunk_document(doc)
        chunks2 = chunker.chunk_document(doc)

        assert chunks1[0].chunk_id == chunks2[0].chunk_id

    def test_chunk_ids_are_unique(self, chunker: ArchitectureAwareChunker) -> None:
        """Test that different chunks have different IDs."""
        md_content = """# Doc

## Section 1
Content 1.

## Section 2
Content 2.
"""
        doc = LoadedDocument(
            source_path=Path("/test/doc.md"),
            content=md_content,
            format="markdown",
        )

        chunks = chunker.chunk_document(doc)
        chunk_ids = [c.chunk_id for c in chunks]

        # All IDs should be unique
        assert len(chunk_ids) == len(set(chunk_ids))

    def test_chunk_empty_document(self, chunker: ArchitectureAwareChunker) -> None:
        """Test chunking an empty document."""
        doc = LoadedDocument(
            source_path=Path("/test/empty.md"),
            content="",
            format="markdown",
        )

        chunks = chunker.chunk_document(doc)
        assert len(chunks) == 0

    def test_chunk_empty_yaml(self, chunker: ArchitectureAwareChunker) -> None:
        """Test chunking an empty YAML document."""
        doc = LoadedDocument(
            source_path=Path("/test/empty.yaml"),
            content="   ",  # Whitespace only
            format="yaml",
        )

        chunks = chunker.chunk_document(doc)
        assert len(chunks) == 0

    def test_chunk_no_headers(self, chunker: ArchitectureAwareChunker) -> None:
        """Test chunking a document with no headers."""
        content = "Just plain text without any headers.\n\nAnother paragraph."
        doc = LoadedDocument(
            source_path=Path("/test/plain.md"),
            content=content,
            format="markdown",
        )

        chunks = chunker.chunk_document(doc)

        # Should still create chunks
        assert len(chunks) >= 1
        assert chunks[0].section_hierarchy == []

    def test_chunk_preamble_before_first_header(
        self, chunker: ArchitectureAwareChunker
    ) -> None:
        """Test that content before the first header is captured."""
        md_content = """This is preamble content before any headers.

# First Section
Section content.
"""
        doc = LoadedDocument(
            source_path=Path("/test/preamble.md"),
            content=md_content,
            format="markdown",
        )

        chunks = chunker.chunk_document(doc)

        # Should have preamble chunk
        preamble = next((c for c in chunks if "preamble content" in c.content), None)
        assert preamble is not None
        assert preamble.section_hierarchy == []

    def test_chunk_token_count_accuracy(
        self, chunker: ArchitectureAwareChunker
    ) -> None:
        """Test that token counts in chunks are accurate."""
        md_content = "# Test\n\nSome content here."
        doc = LoadedDocument(
            source_path=Path("/test/doc.md"),
            content=md_content,
            format="markdown",
        )

        chunks = chunker.chunk_document(doc)

        for chunk in chunks:
            expected_tokens = chunker.count_tokens(chunk.content)
            assert chunk.token_count == expected_tokens

    def test_chunk_metadata_preserved(self, chunker: ArchitectureAwareChunker) -> None:
        """Test that section metadata is preserved in chunks."""
        md_content = """# Policy

## Authentication
Auth content.
"""
        doc = LoadedDocument(
            source_path=Path("/test/doc.md"),
            content=md_content,
            format="markdown",
        )

        chunks = chunker.chunk_document(doc)
        auth_chunk = next(c for c in chunks if "Auth content" in c.content)

        assert auth_chunk.metadata["section_title"] == "Authentication"
        assert auth_chunk.metadata["section_level"] == 2


class TestPDFChunking:
    """Tests for PDF page-based chunking."""

    @pytest.fixture
    def chunker(self) -> ArchitectureAwareChunker:
        """Create a chunker with default settings."""
        return ArchitectureAwareChunker(max_chunk_tokens=512, overlap_tokens=50)

    @pytest.fixture
    def small_chunker(self) -> ArchitectureAwareChunker:
        """Create a chunker with small chunk size for testing splits."""
        return ArchitectureAwareChunker(max_chunk_tokens=50, overlap_tokens=10)

    def test_chunk_pdf_with_pages(self, chunker: ArchitectureAwareChunker) -> None:
        """Test that PDFs are chunked by page markers."""
        pdf_content = """[Page 1]
This is content from page one.
It contains important security information.

[Page 2]
This is content from page two.
More security policies here.

[Page 3]
Final page with conclusions.
"""
        doc = LoadedDocument(
            source_path=Path("/test/document.pdf"),
            content=pdf_content,
            format="pdf",
            metadata={"page_count": 3},
        )

        chunks = chunker.chunk_document(doc)

        # Should have 3 chunks (one per page)
        assert len(chunks) == 3

        # Check page numbers in hierarchy
        assert chunks[0].section_hierarchy == ["Page 1"]
        assert chunks[1].section_hierarchy == ["Page 2"]
        assert chunks[2].section_hierarchy == ["Page 3"]

        # Check content includes hierarchy prefix
        assert "Section: Page 1" in chunks[0].content
        assert "Section: Page 2" in chunks[1].content

    def test_chunk_pdf_preserves_metadata(
        self, chunker: ArchitectureAwareChunker
    ) -> None:
        """Test that PDF metadata is preserved in chunks."""
        pdf_content = """[Page 1]
Some content here.
"""
        doc = LoadedDocument(
            source_path=Path("/test/doc.pdf"),
            content=pdf_content,
            format="pdf",
            metadata={"page_count": 5, "filename": "doc.pdf"},
        )

        chunks = chunker.chunk_document(doc)

        assert len(chunks) == 1
        assert chunks[0].metadata["page_number"] == 1
        assert chunks[0].metadata["total_pages"] == 5
        assert chunks[0].metadata["format"] == "pdf"

    def test_chunk_pdf_large_page_splits(
        self, small_chunker: ArchitectureAwareChunker
    ) -> None:
        """Test that large PDF pages are split into multiple chunks."""
        # Create content that exceeds 50 tokens
        long_content = " ".join(["word"] * 100)
        pdf_content = f"""[Page 1]
{long_content}
"""
        doc = LoadedDocument(
            source_path=Path("/test/large.pdf"),
            content=pdf_content,
            format="pdf",
            metadata={"page_count": 1},
        )

        chunks = small_chunker.chunk_document(doc)

        # Should have multiple chunks due to size
        assert len(chunks) > 1

        # All chunks should have same page number
        for chunk in chunks:
            assert chunk.metadata["page_number"] == 1
            assert chunk.section_hierarchy == ["Page 1"]

    def test_chunk_pdf_without_markers_fallback(
        self, chunker: ArchitectureAwareChunker
    ) -> None:
        """Test that PDFs without page markers fall back to markdown chunking."""
        # PDF content without [Page X] markers (edge case)
        pdf_content = """This PDF has no page markers.
Just plain text content.
"""
        doc = LoadedDocument(
            source_path=Path("/test/nomarkers.pdf"),
            content=pdf_content,
            format="pdf",
        )

        chunks = chunker.chunk_document(doc)

        # Should still produce chunks (fallback to markdown)
        assert len(chunks) >= 1
        # No page hierarchy since no markers
        assert chunks[0].section_hierarchy == []

    def test_chunk_pdf_empty_pages_skipped(
        self, chunker: ArchitectureAwareChunker
    ) -> None:
        """Test that empty PDF pages are skipped."""
        pdf_content = """[Page 1]
Content on page one.

[Page 2]

[Page 3]
Content on page three.
"""
        doc = LoadedDocument(
            source_path=Path("/test/gaps.pdf"),
            content=pdf_content,
            format="pdf",
            metadata={"page_count": 3},
        )

        chunks = chunker.chunk_document(doc)

        # Should have 2 chunks (page 2 is empty)
        assert len(chunks) == 2

        page_numbers = [c.metadata["page_number"] for c in chunks]
        assert 1 in page_numbers
        assert 3 in page_numbers
        assert 2 not in page_numbers


class TestDOCXChunking:
    """Tests for DOCX chunking (uses markdown path since headings are converted)."""

    def test_docx_uses_markdown_chunking(self) -> None:
        """Test that DOCX format routes to markdown chunking."""
        # DOCX content with markdown-style headers (as produced by DocxLoader)
        docx_content = """# Document Title

## Section One
Content in section one.

## Section Two
Content in section two.
"""
        doc = LoadedDocument(
            source_path=Path("/test/document.docx"),
            content=docx_content,
            format="docx",
        )

        chunker = ArchitectureAwareChunker()
        chunks = chunker.chunk_document(doc)

        # Should have chunks with proper hierarchy
        assert len(chunks) >= 2

        # Check hierarchies are preserved
        hierarchies = [c.section_hierarchy for c in chunks]
        assert ["Document Title", "Section One"] in hierarchies
        assert ["Document Title", "Section Two"] in hierarchies


class TestChunkerIntegration:
    """Integration tests for chunker with loader."""

    def test_chunk_loaded_markdown_file(self, sample_markdown_file: Path) -> None:
        """Test chunking a markdown file loaded from disk."""
        from secreqgen.ingestion.loader import DocumentLoaderRegistry

        registry = DocumentLoaderRegistry()
        doc = registry.load(sample_markdown_file)

        chunker = ArchitectureAwareChunker()
        chunks = chunker.chunk_document(doc)

        # Should produce chunks
        assert len(chunks) > 0

        # All chunks should reference the source
        for chunk in chunks:
            assert str(sample_markdown_file) in chunk.source_document

    def test_chunk_loaded_yaml_file(self, sample_yaml_file: Path) -> None:
        """Test chunking a YAML file loaded from disk."""
        from secreqgen.ingestion.loader import DocumentLoaderRegistry

        registry = DocumentLoaderRegistry()
        doc = registry.load(sample_yaml_file)

        chunker = ArchitectureAwareChunker()
        chunks = chunker.chunk_document(doc)

        # YAML should be single chunk
        assert len(chunks) == 1
        assert chunks[0].metadata["is_complete_document"] is True
