# Phase 1: Foundation (Steps 1.1-1.6)

## Step 1.1: Project Init
**Create project structure and dependencies**

```
secreqgen/
├── src/secreqgen/
│   ├── __init__.py
│   ├── cli.py
│   ├── config.py
│   ├── exceptions.py
│   ├── ingestion/
│   ├── retrieval/
│   ├── generation/
│   ├── output/
│   └── evaluation/
├── tests/
├── docs/
├── prompts/
├── configs/
└── data/
```

**pyproject.toml dependencies:**
```toml
[tool.poetry.dependencies]
python = "^3.11"
typer = {extras = ["all"], version = "^0.9.0"}
langchain = "^0.2.0"
langchain-community = "^0.2.0"
chromadb = "^0.4.0"
sentence-transformers = "^2.2.0"
pydantic = "^2.0.0"
pydantic-settings = "^2.0.0"
python-dotenv = "^1.0.0"
rich = "^13.0.0"
pyyaml = "^6.0"
pypdf = "^4.0.0"
python-docx = "^1.0.0"
tiktoken = "^0.5.0"
httpx = "^0.27.0"

[tool.poetry.group.dev.dependencies]
pytest = "^8.0.0"
pytest-cov = "^4.0.0"
black = "^24.0.0"
ruff = "^0.3.0"
mypy = "^1.8.0"

[tool.poetry.scripts]
secreqgen = "secreqgen.cli:app"
```

---

## Step 1.2: Config Management
**Create `src/secreqgen/config.py`**

```python
from pathlib import Path
from pydantic import Field
from pydantic_settings import BaseSettings

class EmbeddingConfig(BaseSettings):
    model_name: str = "all-mpnet-base-v2"
    device: str = "cpu"
    batch_size: int = 32

class VectorStoreConfig(BaseSettings):
    persist_directory: Path = Path("./data/vectordb")
    collection_prefix: str = "secreqgen"

class LLMConfig(BaseSettings):
    model_name: str = "llama3:8b"
    base_url: str = "http://localhost:11434"
    temperature: float = 0.1
    max_tokens: int = 4096

class RetrievalConfig(BaseSettings):
    top_k: int = 5
    similarity_threshold: float = 0.7

class SecReqGenConfig(BaseSettings):
    embedding: EmbeddingConfig = Field(default_factory=EmbeddingConfig)
    vectorstore: VectorStoreConfig = Field(default_factory=VectorStoreConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    retrieval: RetrievalConfig = Field(default_factory=RetrievalConfig)
    
    class Config:
        env_prefix = "SECREQGEN_"
```

**Create `configs/system_context_template.yaml`** - Template for target system architecture (cloud, tech stack, IAM, data classification).

---

## Step 1.3: CLI Foundation
**Create `src/secreqgen/cli.py` with Typer**

Subcommands needed:
- `secreqgen ingest documents <dir> --type policies|architecture|standards`
- `secreqgen ingest system-context <file>`
- `secreqgen generate requirements <input> --output <file> --baseline`
- `secreqgen evaluate compare <generated> <ground_truth>`
- `secreqgen config`

Use Rich for formatted output. All commands print status but return "Not yet implemented" until Phase 2-4.

---

## Step 1.4: Pydantic Schemas
**Create `src/secreqgen/generation/schemas.py`**

Key schemas:
- `UserStory`: id, title, story, acceptance_criteria
- `SecurityRequirement`: id, user_story_id, title, description, rationale, acceptance_criteria, asvs_category, priority, implementation_notes, source_references
- `ThreatAgentMapping`: category, attack_vector, complexity_capability, relevance_ranking
- `GenerationResult`: user_story, requirements, generated_at, model_used, rag_enabled
- `EvaluationResult`: precision, recall, f1_score, hallucination_rate

Include `ASVSCategory` enum with all 14 ASVS categories.

---

## Step 1.5: Logging & Exceptions
**Create `src/secreqgen/exceptions.py`**
- `SecReqGenError` (base)
- `ConfigurationError`, `IngestionError`, `RetrievalError`, `GenerationError`, `ValidationError`

**Create `src/secreqgen/logging.py`**
- Use Rich for console output
- Optional file logging

---

## Step 1.6: Test Infrastructure
**Create `tests/conftest.py`**

Fixtures needed:
- `sample_config` → SecReqGenConfig
- `sample_user_story` → UserStory
- `sample_user_stories` → list of 3 test stories
- `temp_vectordb` → tmp_path for ChromaDB
- `sample_system_context` → dict

**Create `tests/unit/test_cli.py`** - Smoke tests for --version, --help, config command.

---

## Completion Checklist
- [ ] Poetry project initialized
- [ ] All directories created with __init__.py
- [ ] Config loading working
- [ ] CLI skeleton responds to commands
- [ ] Schemas validate correctly
- [ ] Basic tests passing
- [ ] docs/log.md updated
