# SecReqGen Implementation Log

## Project Status
- **Current Phase**: 2 - Document Processing Pipeline (COMPLETE)
- **Current Step**: Phase 2 Complete
- **Last Updated**: 2025-12-28

---

## Phase 2: Document Processing Pipeline

### 2025-12-28 Step 2.1: Document Loader
**Files**: src/secreqgen/ingestion/loader.py, src/secreqgen/ingestion/__init__.py, tests/unit/test_loader.py, tests/conftest.py
**What**: Created document loaders for multiple file formats (.md, .pdf, .docx, .yaml/.yml) with a registry pattern for extensibility
**Why**: The RAG pipeline needs to ingest documents from various sources; a unified loader interface simplifies the ingestion process
**Decisions**:
- Registry pattern for loader selection (open/closed principle, easy extension)
- `LoadedDocument` dataclass as standardized container with metadata
- Abstract `BaseLoader` class enforces consistent interface
- PDFLoader adds `[Page X]` markers for citation context
- DocxLoader converts Word heading styles to markdown format
- YAMLLoader auto-detects system context files by checking for 'system', 'application', 'project', 'context' keys
- Comprehensive error handling with `IngestionError` exceptions
**Issues**:
- venv Python executables were missing (recreated venv with Python 3.13)
- Reinstalled all dependencies via poetry
**Results**: 24 new tests, all 52 tests passing
**Next**: Step 2.2 - Architecture-Aware Chunking

---

### 2025-12-28 Step 2.2: Architecture-Aware Chunker
**Files**: src/secreqgen/ingestion/chunker.py, src/secreqgen/ingestion/__init__.py, tests/unit/test_chunker.py
**What**: Created intelligent document chunker with format-specific strategies for all supported document types
**Why**: RAG retrieval quality depends on chunks that maintain semantic coherence; different formats have different structure indicators that must be respected

**Format-Specific Chunking Strategies**:

1. **Markdown (.md)**:
   - Parses `#`/`##`/`###` headers to extract document structure
   - Builds section hierarchy (e.g., `["Security Policy", "Authentication", "MFA"]`)
   - Prepends hierarchy prefix to chunk content: `"Section: Security Policy > Authentication > MFA\n\n"`
   - Respects section boundaries - never splits mid-section if content fits
   - Large sections split at paragraph boundaries with token overlap
   - Preamble content before first header captured in separate chunk

2. **DOCX (.docx)**:
   - Routes to markdown chunker since DocxLoader converts Word heading styles to markdown format
   - `Heading 1` → `#`, `Heading 2` → `##`, etc.
   - Preserves full document hierarchy through converted headers
   - Same paragraph-boundary splitting as markdown

3. **PDF (.pdf)**:
   - Uses page-based chunking via `[Page X]` markers from PDFLoader
   - Each page becomes one or more chunks depending on size
   - Page number stored in section_hierarchy: `["Page 3"]`
   - Chunk content prefixed with `"Section: Page 3\n\n"` for embedding context
   - PDF metadata preserved: `page_number`, `total_pages`, `format`
   - Large pages split at paragraph boundaries with overlap
   - Empty pages automatically skipped
   - Fallback to markdown chunking if no page markers found

4. **YAML (.yaml/.yml)**:
   - Treated as single chunk (system context must stay intact)
   - Marked with `is_complete_document: True` in metadata
   - No splitting regardless of size

**Core Design Decisions**:
- `DocumentChunk` dataclass with section_hierarchy for context preservation
- tiktoken with cl100k_base encoding for accurate token counting (GPT-4 compatible)
- Default 512 max tokens per chunk, 50 token overlap between chunks
- Deterministic chunk IDs via SHA256 hash of source_path + index
- Large paragraphs split at sentence boundaries, then word boundaries if needed

**Issues**:
- Initial implementation had bug where large paragraphs weren't properly split across chunks
- Fixed by flattening all parts first, then iterating with proper boundary checks

**Results**: 26 chunker tests, all 78 tests passing
**Next**: Step 2.3 - Metadata Tagging

---

### 2025-12-28 Step 2.3: Metadata Tagging
**Files**: src/secreqgen/ingestion/metadata.py, src/secreqgen/ingestion/__init__.py, tests/unit/test_metadata.py, CLAUDE.md
**What**: Created metadata extraction system using keyword pattern matching for document classification and content tagging
**Why**: Rich metadata enables filtered retrieval in ChromaDB and provides context for LLM generation

**Enums Created**:
- `DocumentType`: policy, architecture, standard, system_context, unknown
- `SecurityDomain`: 13 domains (authentication, authorization, cryptography, input_validation, session_management, error_handling, data_protection, api_security, file_handling, configuration, logging_monitoring, network_security, secure_development)
- `ComplianceFramework`: owasp_asvs, gdpr, pci_dss, hipaa, soc2, iso27001, nist, cis

**Metadata Extraction**:

1. **Document Type Detection**:
   - Path-based patterns (high weight): `/policies/` → POLICY, `/architecture/` → ARCHITECTURE
   - Content-based patterns (lower weight): "security policy", "shall", "must" → POLICY
   - YAML files checked for system context indicators (system:, application:, security:)
   - Weighted scoring to pick best match

2. **Security Domain Detection**:
   - Word-boundary regex patterns (`\bauth\b` not matching "author")
   - Case-insensitive matching
   - Domain scores calculated by keyword frequency (for relevance ranking)

3. **Technology Detection**:
   - Cloud: AWS, Azure, GCP (and specific services like S3, EC2, Lambda)
   - Auth: Keycloak, Auth0, Okta, LDAP, Active Directory
   - Frameworks: Spring, Django, FastAPI, React, Angular
   - Databases: PostgreSQL, MySQL, MongoDB, Redis
   - Security tools: SonarQube, Snyk, OWASP ZAP

4. **Compliance Framework Detection**:
   - Pattern matching for GDPR, PCI-DSS, HIPAA, SOC2, ISO 27001, NIST, CIS

**Design Decisions**:
- Pre-compiled regex patterns in `__init__()` for performance (10x speedup)
- `DocumentMetadata.to_dict()` for ChromaDB metadata storage compatibility
- Word boundaries (`\b`) prevent false positives
- Normalized technology names (e.g., "postgres" → "PostgreSQL")

**Also Updated**: CLAUDE.md with troubleshooting section for ModuleNotFoundError

**Results**: 38 metadata tests, all 116 tests passing
**Next**: Step 2.4 - Embedding Generation

---

### 2025-12-28 Step 2.4: Embedding Generation
**Files**: src/secreqgen/ingestion/embedder.py, src/secreqgen/ingestion/__init__.py, tests/unit/test_embedder.py
**What**: Created document embedding system using sentence-transformers to generate dense vector representations for semantic search

**Why**: Vector embeddings enable semantic similarity search in the RAG pipeline. The embeddings capture the meaning of text chunks, allowing retrieval of contextually relevant documents even when exact keywords don't match.

**Key Classes**:

1. **DocumentEmbedder**:
   - Uses `all-mpnet-base-v2` model by default (768-dimensional embeddings)
   - Lazy model loading to defer expensive initialization until first use
   - Batch processing with configurable batch size (default 32) for efficiency
   - Normalized embeddings (L2 norm = 1) for cosine similarity via dot product
   - Handles empty/whitespace text gracefully with zero vectors

2. **EmbeddedChunk**:
   - Dataclass pairing a `DocumentChunk` with its `np.ndarray` embedding
   - Type validation to ensure embedding is numpy array

**API Methods**:
- `embed_text(text: str) -> np.ndarray` - Single text embedding
- `embed_texts(texts: list[str]) -> list[np.ndarray]` - Batch embedding
- `embed_chunk(chunk: DocumentChunk) -> EmbeddedChunk` - Single chunk
- `embed_chunks(chunks: list[DocumentChunk]) -> list[EmbeddedChunk]` - Batch chunks
- `embed_chunks_with_arrays(chunks) -> list[tuple[DocumentChunk, np.ndarray]]` - Tuple format for spec compatibility

**Design Decisions**:
- **No preprocessing needed**: Chunks already include section hierarchy prefix from chunker (e.g., `"Section: Security > Auth\n\n..."`) - we embed content as-is
- **Lazy loading**: Model not loaded until first embedding call to reduce startup time
- **Normalization**: All embeddings normalized to unit length, enabling cosine similarity via simple dot product
- **Empty handling**: Empty/whitespace texts return zero vectors (same dimension) to maintain consistent array shapes

**Integration Notes**:
- Works with existing chunker output - no modifications needed to chunker
- EmbeddingConfig in config.py already had model_name, device, batch_size settings
- Ready for ChromaDB integration in Step 2.5

**Results**: 25 embedder tests, all 141 tests passing
**Next**: Step 2.5 - ChromaDB Vector Store

---

### 2025-12-28 Step 2.5: ChromaDB Vector Store
**Files**: src/secreqgen/retrieval/vectorstore.py, src/secreqgen/retrieval/__init__.py, tests/unit/test_vectorstore.py
**What**: Created multi-collection vector store using ChromaDB for semantic search with document type filtering

**Why**: The vector store is the persistence layer for the RAG pipeline. It stores document chunks with their embeddings and enables efficient semantic similarity search with metadata filtering.

**Key Classes**:

1. **SecReqGenVectorStore**:
   - Multi-collection architecture with one collection per DocumentType
   - Collection mapping: POLICY→policies, ARCHITECTURE→architecture, STANDARD→standards, SYSTEM_CONTEXT→system_context
   - Uses `chromadb.PersistentClient` for disk persistence (or in-memory for tests)
   - Configurable collection prefix (default: "secreqgen")

2. **QueryResult**:
   - Dataclass for query results with chunk_id, content, source_document, section_hierarchy, distance, metadata
   - `similarity` property converts L2 distance to [0,1] similarity score

**API Methods**:
- `add_chunks(chunks, embeddings, doc_type)` - Upserts chunks with embeddings to appropriate collection
- `query(query_embedding, doc_types, n_results, where)` - Semantic search across collections with optional filtering
- `get_stats()` - Returns collection statistics (total chunks, per-collection counts)
- `get_chunk_by_id(chunk_id, doc_type)` - Direct retrieval by ID
- `delete_collection(doc_type)` / `clear()` - Collection management

**Design Decisions**:
- **Multi-collection**: Separate collections by DocumentType enables efficient filtered queries without scanning irrelevant data
- **L2 distance with normalized vectors**: Using L2 distance (hnsw:space = "l2") with normalized embeddings gives equivalent ranking to cosine similarity
- **Metadata storage**: Section hierarchy stored as " > " joined string (ChromaDB metadata must be primitives), chunk metadata flattened for filterability
- **Upsert behavior**: `add_chunks` uses upsert to handle re-indexing gracefully
- **In-memory option**: `persist_directory=None` creates ephemeral store for testing (proper isolation via `clear()` in test fixtures)

**ChromaDB Specifics**:
- Uses `get_or_create_collection` for lazy collection initialization
- `where` clauses support metadata filtering (e.g., `{"security_domains": {"$contains": "authentication"}}`)
- Query results include distances (L2), converted to similarity via `1 - (distance/2)`

**Test Isolation Issue**: ChromaDB's in-memory client shares state across instances. Solved by calling `store.clear()` in test fixtures to ensure fresh state per test.

**Results**: 29 vectorstore tests, all 170 tests passing
**Next**: Step 2.6 - Wire Ingestion CLI

---

### 2025-12-28 Step 2.6: Wire Ingestion CLI
**Files**: src/secreqgen/cli.py, tests/unit/test_cli.py
**What**: Connected all ingestion components into functional CLI commands

**Why**: The CLI is the user-facing interface for the RAG pipeline. Wiring the components together makes the system usable for ingesting security documents.

**CLI Commands Implemented**:

1. **`secreqgen ingest documents <directory> [--type TYPE]`**:
   - Full pipeline: load → chunk → tag → embed → store
   - Validates directory exists
   - Maps CLI type (policies/architecture/standards) to DocumentType enum
   - Shows Rich progress bars for each step
   - Reports detected security domains
   - Displays final statistics

2. **`secreqgen ingest system-context <file.yaml>`**:
   - Loads YAML system context file
   - Validates file is YAML format
   - Stores in SYSTEM_CONTEXT collection
   - YAML becomes single chunk (stays intact)

3. **`secreqgen ingest stats`**:
   - Shows vector store statistics in Rich table
   - Displays chunk counts per collection
   - Shows total chunks and store path

4. **`secreqgen ingest clear [--force]`**:
   - Clears all data from vector store
   - Requires confirmation unless --force flag used

**Design Decisions**:
- **Lazy imports**: Heavy imports (sentence-transformers, chromadb) done inside commands to keep CLI startup fast
- **Environment variable override**: Tests use `SECREQGEN_VECTORSTORE_PERSIST_DIRECTORY` env var to isolate test data
- **Progress feedback**: Rich spinners and progress bars for user feedback during slow operations
- **Graceful error handling**: Catches IngestionError and VectorStoreError with user-friendly messages
- **Doc type mapping**: Flexible mapping (e.g., "policies", "policy" both map to POLICY)

**Test Updates**:
- Updated existing CLI tests for new functionality
- Added 10 new tests for ingest commands
- Tests use temp directories for isolation

**Results**: 18 CLI tests, all 180 tests passing

---

## Phase 2 Completion Checklist
- [x] All loaders working (test with sample files)
- [x] Chunking respects structure
- [x] Metadata extraction finding domains/techs
- [x] Embeddings generating correctly
- [x] ChromaDB persisting and querying
- [x] CLI ingest commands functional
- [x] Integration test: ingest → query round-trip
- [x] docs/log.md updated

**Phase 2 Complete!** Ready for Phase 3: RAG Retrieval Pipeline

---

## Implementation History

### 2025-12-27 Step 1.1: Project Initialization
**Files**: pyproject.toml, src/secreqgen/__init__.py, README.md
**What**: Created project structure with Poetry, set up all directories
**Why**: Foundation for all subsequent development
**Decisions**:
- Using Poetry for dependency management
- Python 3.11-3.13 for compatibility (3.14 not supported by dependencies)
- Using venv for environment isolation
**Next**: Step 1.2 - Configuration Management

---

### 2025-12-27 Step 1.2: Configuration Management
**Files**: src/secreqgen/config.py, configs/system_context_template.yaml
**What**: Created Pydantic Settings-based configuration management
**Why**: Type-safe configuration with environment variable support
**Decisions**:
- Separate config classes for embedding, vectorstore, LLM, retrieval, evaluation
- Environment variable prefix: SECREQGEN_
- all-mpnet-base-v2 as default embedding model
- llama3:8b as default LLM
**Next**: Step 1.3 - CLI Foundation

---

### 2025-12-27 Step 1.3: CLI Foundation
**Files**: src/secreqgen/cli.py
**What**: Created Typer-based CLI with subcommands for ingest, generate, evaluate
**Why**: User-friendly command-line interface with Rich formatting
**Decisions**:
- Typer 0.15 for Python 3.13 compatibility
- Rich for formatted console output
- Sub-apps for logical command grouping
**Issues**:
- Typer 0.9/0.12 had compatibility issues with Python 3.13/Click
- Resolved by upgrading to Typer 0.15
- Known issue: --help flag crashes due to Typer/Click compatibility (non-blocking)
**Next**: Step 1.4 - Pydantic Schemas

---

### 2025-12-27 Step 1.4: Pydantic Schemas
**Files**: src/secreqgen/generation/schemas.py, src/secreqgen/generation/__init__.py
**What**: Created core Pydantic models for the application
**Why**: Type-safe data structures for requirements generation and evaluation
**Decisions**:
- ASVSCategory enum with all 14 OWASP ASVS v4.0 categories
- Priority enum (critical, high, medium, low)
- UserStory, SecurityRequirement, ThreatAgentMapping, GenerationResult, EvaluationResult
**Next**: Step 1.5 - Logging & Exceptions

---

### 2025-12-27 Step 1.5: Logging & Exceptions
**Files**: src/secreqgen/exceptions.py, src/secreqgen/logging.py
**What**: Created custom exception hierarchy and Rich-based logging
**Why**: Consistent error handling and formatted console output
**Decisions**:
- Base SecReqGenError with message and details
- Specific exceptions: ConfigurationError, IngestionError, RetrievalError, GenerationError, ValidationError, LLMConnectionError, VectorStoreError
- Rich console handler with tracebacks
- Optional file logging
**Next**: Step 1.6 - Test Infrastructure

---

### 2025-12-27 Step 1.6: Test Infrastructure
**Files**: tests/conftest.py, tests/unit/test_cli.py, tests/unit/test_config.py, tests/unit/test_schemas.py
**What**: Created pytest fixtures and unit tests for all Phase 1 components
**Why**: Ensure code quality and catch regressions
**Decisions**:
- pytest with coverage support
- Fixtures for sample config, user stories, security requirements, system context
- 28 tests covering CLI, config, and schemas
**Issues**:
- tiktoken 0.5/0.7 required Rust compiler; upgraded to 0.8+
- Python 3.14 not supported; restricted to 3.11-3.13
**Results**: All 28 tests passing
**Next**: Phase 2 - Document Ingestion

---

## Phase 1 Completion Checklist
- [x] Poetry project initialized
- [x] All directories created with __init__.py
- [x] Config loading working
- [x] CLI skeleton responds to commands
- [x] Schemas validate correctly
- [x] Basic tests passing (28/28)
- [x] docs/log.md updated

---

<!-- Template for new entries:

### [DATE] Step X.Y: [Step Name]
**Files**: [list of files created/modified]
**What**: [brief description of what was implemented]
**Why**: [rationale for approach taken]
**Decisions**:
- [key decision 1]
- [key decision 2]
**Issues**: [any problems encountered and solutions]
**Next**: [what comes next]

-->
