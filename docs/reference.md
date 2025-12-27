# SecReqGen Quick Reference

## Common Patterns

### Pydantic Schema Pattern
```python
from pydantic import BaseModel, Field
from enum import Enum

class MyEnum(str, Enum):
    VALUE_A = "value_a"
    VALUE_B = "value_b"

class MySchema(BaseModel):
    id: str = Field(description="Unique identifier")
    name: str
    category: MyEnum
    items: list[str] = Field(default_factory=list)
    
    model_config = {"json_schema_extra": {"examples": [...]}}
```

### CLI Command Pattern
```python
@app.command()
def my_command(
    input_file: Annotated[Path, typer.Argument(help="Input file")],
    output: Annotated[Optional[Path], typer.Option("--output", "-o")] = None,
    verbose: Annotated[bool, typer.Option("--verbose")] = False
) -> None:
    """Command description."""
    console.print(Panel(f"Processing: {input_file}", title="Status"))
```

### ChromaDB Query Pattern
```python
results = collection.query(
    query_embeddings=[embedding.tolist()],
    n_results=5,
    where={"doc_type": "policy"},
    include=["documents", "metadatas", "distances"]
)
```

### Ollama Generation Pattern
```python
response = httpx.post(
    f"{base_url}/api/chat",
    json={
        "model": "llama3:8b",
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt}
        ],
        "stream": False,
        "options": {"temperature": 0.1}
    },
    timeout=300.0
)
```

---

## Key Constants

| Constant | Value | Purpose |
|----------|-------|---------|
| CHUNK_SIZE | 512 tokens | Max tokens per chunk |
| CHUNK_OVERLAP | 50 tokens | Overlap between chunks |
| MATCH_THRESHOLD | 0.85 | Cosine sim for True Positive |
| PARTIAL_THRESHOLD | 0.70 | Cosine sim for manual review |
| TOP_K | 5 | Default retrieval count |
| LLM_TIMEOUT | 300s | Generation timeout |

---

## ASVS Categories
```python
V1_ARCHITECTURE = "V1 - Architecture, Design and Threat Modeling"
V2_AUTHENTICATION = "V2 - Authentication"
V3_SESSION = "V3 - Session Management"
V4_ACCESS_CONTROL = "V4 - Access Control"
V5_VALIDATION = "V5 - Validation, Sanitization and Encoding"
V6_CRYPTOGRAPHY = "V6 - Stored Cryptography"
V7_ERROR_LOGGING = "V7 - Error Handling and Logging"
V8_DATA_PROTECTION = "V8 - Data Protection"
V9_COMMUNICATION = "V9 - Communication"
V10_MALICIOUS_CODE = "V10 - Malicious Code"
V11_BUSINESS_LOGIC = "V11 - Business Logic"
V12_FILES_RESOURCES = "V12 - Files and Resources"
V13_API = "V13 - API and Web Service"
V14_CONFIGURATION = "V14 - Configuration"
```

---

## File Locations

| What | Where |
|------|-------|
| Main config | `src/secreqgen/config.py` |
| CLI entry | `src/secreqgen/cli.py` |
| Schemas | `src/secreqgen/generation/schemas.py` |
| Prompts | `prompts/*.md` |
| Test fixtures | `tests/fixtures/` |
| Phase docs | `docs/phases/` |
| Impl log | `docs/log.md` |
| Vector DB | `data/vectordb/` |
| Ground truth | `data/ground_truth/` |

---

## Test Commands
```bash
# All tests
poetry run pytest tests/ -v

# Specific module
poetry run pytest tests/unit/test_config.py -v

# With coverage
poetry run pytest tests/ --cov=secreqgen --cov-report=html

# Integration only
poetry run pytest tests/integration/ -v
```
