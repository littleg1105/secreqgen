# Phase 2: Document Processing Pipeline (Steps 2.1-2.5)

## Step 2.1: Document Loader
**Create `src/secreqgen/ingestion/loader.py`**

Support formats: `.md`, `.pdf`, `.docx`, `.yaml`

```python
@dataclass
class LoadedDocument:
    source_path: Path
    content: str
    format: str
    metadata: dict

class DocumentLoaderRegistry:
    """Manages loaders for different formats."""
    
    def load(self, path: Path) -> LoadedDocument: ...
    def load_directory(self, directory: Path) -> list[LoadedDocument]: ...
```

Loaders:
- `MarkdownLoader` - direct text read
- `PDFLoader` - pypdf, preserve page markers
- `DocxLoader` - python-docx, preserve headers as markdown
- `YAMLLoader` - for system context files

---

## Step 2.2: Architecture-Aware Chunking
**Create `src/secreqgen/ingestion/chunker.py`**

```python
@dataclass
class DocumentChunk:
    chunk_id: str
    content: str
    source_document: str
    section_hierarchy: list[str]  # ["Security Policy", "Authentication", "MFA"]
    chunk_index: int
    token_count: int
    metadata: dict

class ArchitectureAwareChunker:
    def __init__(self, max_chunk_tokens=512, overlap_tokens=50): ...
    def chunk_document(self, document: LoadedDocument) -> list[DocumentChunk]: ...
```

Key behaviors:
- Respect markdown headers (don't split mid-section)
- Preserve section hierarchy for context
- Use tiktoken for accurate token counting
- Overlap for retrieval coherence
- YAML files → single chunk (system context)

---

## Step 2.3: Metadata Tagging
**Create `src/secreqgen/ingestion/metadata.py`**

```python
class DocumentType(str, Enum):
    POLICY = "policy"
    ARCHITECTURE = "architecture"
    STANDARD = "standard"
    SYSTEM_CONTEXT = "system_context"

class SecurityDomain(str, Enum):
    AUTHENTICATION = "authentication"
    AUTHORIZATION = "authorization"
    CRYPTOGRAPHY = "cryptography"
    # ... etc

class MetadataExtractor:
    def extract(self, content: str, source_path: str) -> DocumentMetadata: ...
```

Extract via keyword patterns:
- Document type from path + content keywords
- Security domains (auth, crypto, validation, etc.)
- Technologies mentioned (AWS, Keycloak, Spring, etc.)
- Compliance frameworks (GDPR, PCI-DSS, etc.)

---

## Step 2.4: Embedding Generation
**Create `src/secreqgen/ingestion/embedder.py`**

```python
class DocumentEmbedder:
    def __init__(self, model_name="all-mpnet-base-v2", device="cpu"): ...
    def embed_text(self, text: str) -> np.ndarray: ...
    def embed_chunks(self, chunks: list[DocumentChunk]) -> list[tuple[DocumentChunk, np.ndarray]]: ...
```

Key: Prepend section hierarchy to chunk content before embedding for better context.

---

## Step 2.5: ChromaDB Vector Store
**Create `src/secreqgen/retrieval/vectorstore.py`**

```python
class SecReqGenVectorStore:
    """Multi-collection store for different doc types."""
    
    COLLECTIONS = {
        DocumentType.POLICY: "secreqgen_policies",
        DocumentType.ARCHITECTURE: "secreqgen_architecture",
        DocumentType.STANDARD: "secreqgen_standards",
        DocumentType.SYSTEM_CONTEXT: "secreqgen_system_context",
    }
    
    def add_chunks(self, chunks, embeddings, doc_type): ...
    def query(self, query_embedding, doc_types=None, n_results=5, where=None): ...
    def get_stats(self) -> dict: ...
```

Use `chromadb.PersistentClient` for persistence.

---

## Step 2.6: Wire Ingestion CLI
Update `cli.py` to connect:
- `ingest documents` → load → chunk → tag → embed → store
- `ingest system-context` → load YAML → store in system_context collection

---

## Completion Checklist
- [ ] All loaders working (test with sample files)
- [ ] Chunking respects structure
- [ ] Metadata extraction finding domains/techs
- [ ] Embeddings generating correctly
- [ ] ChromaDB persisting and querying
- [ ] CLI ingest commands functional
- [ ] Integration test: ingest → query round-trip
- [ ] docs/log.md updated
