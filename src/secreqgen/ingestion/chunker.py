"""Architecture-aware document chunking.

Chunks documents while respecting structure (markdown headers) and
preserving section hierarchy for better retrieval context.
"""

import hashlib
import re
from dataclasses import dataclass, field
import tiktoken

from secreqgen.ingestion.loader import LoadedDocument


@dataclass
class DocumentChunk:
    """A chunk of a document with context metadata."""

    chunk_id: str
    content: str
    source_document: str
    section_hierarchy: list[str]
    chunk_index: int
    token_count: int
    metadata: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Ensure section_hierarchy is a list."""
        if self.section_hierarchy is None:
            self.section_hierarchy = []


@dataclass
class Section:
    """Represents a section in a markdown document."""

    title: str
    level: int  # 1 for #, 2 for ##, etc.
    content: str
    hierarchy: list[str]


class ArchitectureAwareChunker:
    """Chunks documents while preserving structure and hierarchy.

    Key behaviors:
    - Respects markdown header boundaries (won't split mid-section if possible)
    - Preserves section hierarchy for retrieval context
    - Uses tiktoken for accurate token counting
    - Applies overlap between chunks for retrieval coherence
    - Treats YAML files as single chunks (system context)
    - PDF files are chunked by page markers
    """

    # Regex to match markdown headers
    HEADER_PATTERN = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)

    # Regex to match PDF page markers from PDFLoader: [Page X]
    PAGE_MARKER_PATTERN = re.compile(r"^\[Page\s+(\d+)\]$", re.MULTILINE)

    def __init__(
        self,
        max_chunk_tokens: int = 512,
        overlap_tokens: int = 50,
        encoding_name: str = "cl100k_base",
    ) -> None:
        """Initialize the chunker.

        Args:
            max_chunk_tokens: Maximum tokens per chunk (default 512).
            overlap_tokens: Tokens to overlap between chunks (default 50).
            encoding_name: Tiktoken encoding name (default cl100k_base for GPT-4).
        """
        self.max_chunk_tokens = max_chunk_tokens
        self.overlap_tokens = overlap_tokens
        self._encoding = tiktoken.get_encoding(encoding_name)

    def count_tokens(self, text: str) -> int:
        """Count tokens in text using tiktoken.

        Args:
            text: The text to count tokens for.

        Returns:
            Number of tokens.
        """
        return len(self._encoding.encode(text))

    def chunk_document(self, document: LoadedDocument) -> list[DocumentChunk]:
        """Chunk a document into pieces suitable for embedding.

        Routes to format-specific chunking strategies:
        - YAML: Single chunk (system context)
        - PDF: Page-based chunking
        - Markdown/DOCX: Header-based chunking

        Args:
            document: The loaded document to chunk.

        Returns:
            List of DocumentChunk objects.
        """
        # Handle empty content
        if not document.content or not document.content.strip():
            return []

        # Route by format
        if document.format == "yaml":
            # YAML files are treated as single chunks (system context)
            return self._chunk_yaml(document)
        elif document.format == "pdf":
            # PDF files use page-based chunking
            return self._chunk_pdf(document)
        else:
            # Markdown and DOCX use header-based chunking
            # (DocxLoader converts Word headings to markdown format)
            return self._chunk_markdown(document)

    def _chunk_yaml(self, document: LoadedDocument) -> list[DocumentChunk]:
        """Chunk a YAML document as a single chunk.

        YAML files (typically system context) should stay intact.

        Args:
            document: The loaded YAML document.

        Returns:
            List with a single DocumentChunk, or empty if no content.
        """
        return self._create_single_chunk(document)

    def _chunk_markdown(self, document: LoadedDocument) -> list[DocumentChunk]:
        """Chunk a markdown or DOCX document using header-based structure.

        Args:
            document: The loaded document (markdown or docx format).

        Returns:
            List of DocumentChunk objects.
        """
        # Parse markdown structure
        sections = self._parse_markdown_structure(document.content)

        # If no sections found, treat entire document as one section
        if not sections:
            sections = [
                Section(
                    title="",
                    level=0,
                    content=document.content,
                    hierarchy=[],
                )
            ]

        # Chunk each section
        chunks: list[DocumentChunk] = []
        chunk_index = 0

        for section in sections:
            section_chunks = self._chunk_section(
                section=section,
                source_path=str(document.source_path),
                start_index=chunk_index,
            )
            chunks.extend(section_chunks)
            chunk_index += len(section_chunks)

        return chunks

    def _chunk_pdf(self, document: LoadedDocument) -> list[DocumentChunk]:
        """Chunk a PDF document using page-based structure.

        PDFLoader outputs content with [Page X] markers. This method:
        1. Splits content by page markers
        2. Creates chunks per page (or splits large pages)
        3. Preserves page number in hierarchy and metadata

        Args:
            document: The loaded PDF document.

        Returns:
            List of DocumentChunk objects.
        """
        source_path = str(document.source_path)
        content = document.content

        # Parse pages from content
        pages = self._parse_pdf_pages(content)

        if not pages:
            # No page markers found, fall back to simple paragraph chunking
            return self._chunk_markdown(document)

        chunks: list[DocumentChunk] = []
        chunk_index = 0

        for page_num, page_content in pages:
            page_chunks = self._chunk_pdf_page(
                page_num=page_num,
                page_content=page_content,
                source_path=source_path,
                start_index=chunk_index,
                total_pages=document.metadata.get("page_count", len(pages)),
            )
            chunks.extend(page_chunks)
            chunk_index += len(page_chunks)

        return chunks

    def _parse_pdf_pages(self, content: str) -> list[tuple[int, str]]:
        """Parse PDF content into pages based on [Page X] markers.

        Args:
            content: PDF content with page markers.

        Returns:
            List of (page_number, page_content) tuples.
        """
        pages: list[tuple[int, str]] = []

        # Find all page markers
        markers = list(self.PAGE_MARKER_PATTERN.finditer(content))

        if not markers:
            return []

        # Extract content for each page
        for i, match in enumerate(markers):
            page_num = int(match.group(1))
            start = match.end()

            # Content goes until next marker or end of document
            if i + 1 < len(markers):
                end = markers[i + 1].start()
            else:
                end = len(content)

            page_content = content[start:end].strip()

            if page_content:
                pages.append((page_num, page_content))

        return pages

    def _chunk_pdf_page(
        self,
        page_num: int,
        page_content: str,
        source_path: str,
        start_index: int,
        total_pages: int,
    ) -> list[DocumentChunk]:
        """Chunk a single PDF page.

        If the page fits in max_tokens, returns a single chunk.
        Otherwise, splits at paragraph boundaries.

        Args:
            page_num: The page number.
            page_content: Content of the page.
            source_path: Path to source document.
            start_index: Starting chunk index.
            total_pages: Total pages in document (for metadata).

        Returns:
            List of DocumentChunk objects for this page.
        """
        hierarchy = [f"Page {page_num}"]
        hierarchy_prefix = self._format_hierarchy_prefix(hierarchy)
        full_content = hierarchy_prefix + page_content

        token_count = self.count_tokens(full_content)

        # If page fits in one chunk, return it
        if token_count <= self.max_chunk_tokens:
            chunk_id = self._generate_chunk_id(source_path, start_index)
            return [
                DocumentChunk(
                    chunk_id=chunk_id,
                    content=full_content,
                    source_document=source_path,
                    section_hierarchy=hierarchy,
                    chunk_index=start_index,
                    token_count=token_count,
                    metadata={
                        "page_number": page_num,
                        "total_pages": total_pages,
                        "format": "pdf",
                    },
                )
            ]

        # Page too large, split using Section mechanism
        section = Section(
            title=f"Page {page_num}",
            level=1,
            content=page_content,
            hierarchy=hierarchy,
        )

        chunks = self._split_large_section(
            section=section,
            source_path=source_path,
            start_index=start_index,
        )

        # Add PDF-specific metadata to each chunk
        for chunk in chunks:
            chunk.metadata["page_number"] = page_num
            chunk.metadata["total_pages"] = total_pages
            chunk.metadata["format"] = "pdf"

        return chunks

    def _create_single_chunk(self, document: LoadedDocument) -> list[DocumentChunk]:
        """Create a single chunk from an entire document.

        Used for YAML files and small documents.
        """
        content = document.content.strip()
        if not content:
            return []

        chunk_id = self._generate_chunk_id(str(document.source_path), 0)

        return [
            DocumentChunk(
                chunk_id=chunk_id,
                content=content,
                source_document=str(document.source_path),
                section_hierarchy=[],
                chunk_index=0,
                token_count=self.count_tokens(content),
                metadata={
                    "format": document.format,
                    "is_complete_document": True,
                    **document.metadata,
                },
            )
        ]

    def _parse_markdown_structure(self, content: str) -> list[Section]:
        """Parse markdown content into sections based on headers.

        Args:
            content: Markdown content to parse.

        Returns:
            List of Section objects with hierarchy information.
        """
        sections: list[Section] = []
        hierarchy: list[str] = []  # Current hierarchy stack

        # Find all headers and their positions
        headers: list[tuple[int, int, str, int]] = []  # (start, end, title, level)

        for match in self.HEADER_PATTERN.finditer(content):
            level = len(match.group(1))  # Number of # symbols
            title = match.group(2).strip()
            headers.append((match.start(), match.end(), title, level))

        if not headers:
            # No headers found, return empty (caller will handle)
            return []

        # Process content before first header (if any)
        if headers[0][0] > 0:
            preamble = content[: headers[0][0]].strip()
            if preamble:
                sections.append(
                    Section(
                        title="",
                        level=0,
                        content=preamble,
                        hierarchy=[],
                    )
                )

        # Process each header and its content
        for i, (start, end, title, level) in enumerate(headers):
            # Update hierarchy for this level
            # Truncate hierarchy to parent level, then add current title
            hierarchy = hierarchy[: level - 1]
            hierarchy.append(title)

            # Get content until next header or end of document
            if i + 1 < len(headers):
                next_start = headers[i + 1][0]
                section_content = content[end:next_start].strip()
            else:
                section_content = content[end:].strip()

            # Only add section if it has content
            if section_content:
                sections.append(
                    Section(
                        title=title,
                        level=level,
                        content=section_content,
                        hierarchy=hierarchy.copy(),
                    )
                )

        return sections

    def _chunk_section(
        self,
        section: Section,
        source_path: str,
        start_index: int,
    ) -> list[DocumentChunk]:
        """Chunk a single section, splitting if necessary.

        Args:
            section: The section to chunk.
            source_path: Path to source document.
            start_index: Starting chunk index.

        Returns:
            List of DocumentChunk objects for this section.
        """
        # Prepend hierarchy to content for embedding context
        hierarchy_prefix = self._format_hierarchy_prefix(section.hierarchy)
        full_content = hierarchy_prefix + section.content if hierarchy_prefix else section.content

        token_count = self.count_tokens(full_content)

        # If section fits in one chunk, return it
        if token_count <= self.max_chunk_tokens:
            chunk_id = self._generate_chunk_id(source_path, start_index)
            return [
                DocumentChunk(
                    chunk_id=chunk_id,
                    content=full_content,
                    source_document=source_path,
                    section_hierarchy=section.hierarchy,
                    chunk_index=start_index,
                    token_count=token_count,
                    metadata={"section_title": section.title, "section_level": section.level},
                )
            ]

        # Section too large, split with overlap
        return self._split_large_section(
            section=section,
            source_path=source_path,
            start_index=start_index,
        )

    def _split_large_section(
        self,
        section: Section,
        source_path: str,
        start_index: int,
    ) -> list[DocumentChunk]:
        """Split a large section into multiple chunks with overlap.

        Splits at paragraph boundaries when possible.

        Args:
            section: The section to split.
            source_path: Path to source document.
            start_index: Starting chunk index.

        Returns:
            List of DocumentChunk objects.
        """
        hierarchy_prefix = self._format_hierarchy_prefix(section.hierarchy)
        prefix_tokens = self.count_tokens(hierarchy_prefix) if hierarchy_prefix else 0

        # Split content into paragraphs
        paragraphs = self._split_into_paragraphs(section.content)

        # Flatten large paragraphs into smaller parts
        all_parts: list[str] = []
        for para in paragraphs:
            para_tokens = self.count_tokens(para)
            if para_tokens > self.max_chunk_tokens - prefix_tokens:
                # Split large paragraph
                all_parts.extend(self._split_large_paragraph(para))
            else:
                all_parts.append(para)

        chunks: list[DocumentChunk] = []
        current_chunk_parts: list[str] = []
        current_tokens = prefix_tokens

        for part in all_parts:
            part_tokens = self.count_tokens(part)

            # Check if adding this part would exceed limit
            if current_tokens + part_tokens > self.max_chunk_tokens:
                # Save current chunk if we have content
                if current_chunk_parts:
                    chunk_content = hierarchy_prefix + "\n\n".join(current_chunk_parts)
                    chunk_id = self._generate_chunk_id(source_path, start_index + len(chunks))

                    chunks.append(
                        DocumentChunk(
                            chunk_id=chunk_id,
                            content=chunk_content.strip(),
                            source_document=source_path,
                            section_hierarchy=section.hierarchy,
                            chunk_index=start_index + len(chunks),
                            token_count=self.count_tokens(chunk_content),
                            metadata={
                                "section_title": section.title,
                                "section_level": section.level,
                                "is_split": True,
                                "split_part": len(chunks) + 1,
                            },
                        )
                    )

                    # Prepare overlap for next chunk
                    overlap_text = self._get_overlap_text(current_chunk_parts)
                    current_chunk_parts = [overlap_text] if overlap_text else []
                    current_tokens = (
                        prefix_tokens + self.count_tokens(overlap_text)
                        if overlap_text
                        else prefix_tokens
                    )

            # Add the part to current chunk
            current_chunk_parts.append(part)
            current_tokens += part_tokens

        # Don't forget the last chunk
        if current_chunk_parts:
            chunk_content = hierarchy_prefix + "\n\n".join(current_chunk_parts)
            chunk_id = self._generate_chunk_id(source_path, start_index + len(chunks))

            chunks.append(
                DocumentChunk(
                    chunk_id=chunk_id,
                    content=chunk_content.strip(),
                    source_document=source_path,
                    section_hierarchy=section.hierarchy,
                    chunk_index=start_index + len(chunks),
                    token_count=self.count_tokens(chunk_content),
                    metadata={
                        "section_title": section.title,
                        "section_level": section.level,
                        "is_split": len(chunks) > 0,
                        "split_part": len(chunks) + 1 if len(chunks) > 0 else None,
                    },
                )
            )

        return chunks

    def _split_into_paragraphs(self, text: str) -> list[str]:
        """Split text into paragraphs.

        Args:
            text: Text to split.

        Returns:
            List of paragraph strings.
        """
        # Split on double newlines (paragraph breaks)
        paragraphs = re.split(r"\n\n+", text)
        # Filter empty and strip whitespace
        return [p.strip() for p in paragraphs if p.strip()]

    def _split_large_paragraph(self, paragraph: str) -> list[str]:
        """Split a paragraph that's too large into smaller pieces.

        Tries to split at sentence boundaries first.

        Args:
            paragraph: The paragraph to split.

        Returns:
            List of smaller text pieces.
        """
        # Try splitting by sentences first
        sentences = re.split(r"(?<=[.!?])\s+", paragraph)

        if len(sentences) > 1:
            return sentences

        # If single long sentence, split by words
        words = paragraph.split()
        target_words = self.max_chunk_tokens // 2  # Rough estimate: 1 token ≈ 0.75 words

        parts = []
        for i in range(0, len(words), target_words):
            part = " ".join(words[i : i + target_words])
            parts.append(part)

        return parts

    def _get_overlap_text(self, parts: list[str]) -> str:
        """Get overlap text from the end of a chunk.

        Args:
            parts: List of paragraph parts in current chunk.

        Returns:
            Text to use as overlap for next chunk.
        """
        if not parts:
            return ""

        # Take from the last parts until we have enough overlap tokens
        overlap_parts = []
        token_count = 0

        for part in reversed(parts):
            part_tokens = self.count_tokens(part)
            if token_count + part_tokens <= self.overlap_tokens:
                overlap_parts.insert(0, part)
                token_count += part_tokens
            else:
                # Take partial if needed
                words = part.split()
                partial = []
                for word in reversed(words):
                    word_tokens = self.count_tokens(" ".join([word] + partial))
                    if token_count + word_tokens <= self.overlap_tokens:
                        partial.insert(0, word)
                    else:
                        break
                if partial:
                    overlap_parts.insert(0, " ".join(partial))
                break

        return " ".join(overlap_parts)

    def _format_hierarchy_prefix(self, hierarchy: list[str]) -> str:
        """Format section hierarchy as a prefix for chunk content.

        This helps the embedding model understand the context.

        Args:
            hierarchy: List of section titles from root to current.

        Returns:
            Formatted prefix string, or empty string if no hierarchy.
        """
        if not hierarchy:
            return ""

        # Format as "Section: A > B > C\n\n"
        return f"Section: {' > '.join(hierarchy)}\n\n"

    def _generate_chunk_id(self, source_path: str, chunk_index: int) -> str:
        """Generate a deterministic chunk ID.

        Args:
            source_path: Path to source document.
            chunk_index: Index of this chunk.

        Returns:
            Unique chunk ID string.
        """
        # Create a hash from source + index for reproducibility
        content = f"{source_path}:{chunk_index}"
        hash_digest = hashlib.sha256(content.encode()).hexdigest()[:12]
        return f"chunk_{hash_digest}"
