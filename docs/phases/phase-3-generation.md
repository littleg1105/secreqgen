# Phase 3: LLM Processing Layer (Steps 3.1-3.5)

## Step 3.1: Ollama LLM Client
**Create `src/secreqgen/generation/llm_client.py`**

```python
class OllamaClient:
    def __init__(self, base_url="http://localhost:11434", model="llama3:8b"): ...
    def generate(self, prompt: str, system_prompt: str = None) -> str: ...
    def generate_structured(self, prompt: str, output_schema: Type[BaseModel]) -> BaseModel: ...
    def check_health(self) -> bool: ...
```

Key features:
- Use httpx with 5min timeout
- `generate_structured`: inject JSON schema into prompt, parse response, validate with Pydantic
- Retry logic (3 attempts) for structured output
- Extract JSON from markdown code blocks if present

---

## Step 3.2: Prompt Templates
**Create files in `prompts/` directory:**

`prompts/system_prompt.md`:
```
You are a Security Requirements Engineer expert in OWASP ASVS, SSDLC, and cloud security.
Generate specific, actionable requirements grounded in organizational context.
Prioritize specificity over generality.
```

`prompts/requirement_generation.md`:
```
## System Context
{system_context}

## Retrieved Context
{retrieved_context}

## User Story
ID: {story_id}
Title: {story_title}
Story: {story_text}
Acceptance Criteria: {acceptance_criteria}

## Task
Generate security requirements. For each include:
- title, description, rationale
- acceptance_criteria (testable)
- asvs_category, asvs_requirement_id
- priority (critical/high/medium/low)
- implementation_notes (specific to system context)

Respond with JSON: {"requirements": [...]}
```

`prompts/threat_mapping.md`: Similar structure for threat agent mapping.

**Create `src/secreqgen/generation/prompts.py`** - Load and render templates.

---

## Step 3.3: RAG Retriever
**Create `src/secreqgen/retrieval/retriever.py`**

```python
@dataclass
class RetrievalResult:
    content: str
    source: str
    section: str
    relevance_score: float

class SecurityDocRetriever:
    def __init__(self, vectorstore, embedder, config): ...
    
    def retrieve(self, query: str, doc_types=None, n_results=5) -> list[RetrievalResult]: ...
    
    def retrieve_for_story(self, story_text: str, acceptance_criteria: list[str]) -> str:
        """Returns formatted context string for LLM injection."""
        # 1. ALWAYS include system context first
        # 2. Query policies, architecture, standards
        # 3. Format as numbered context blocks
```

Key: System context is ALWAYS retrieved and injected, regardless of query.

---

## Step 3.4: Requirements Generator
**Create `src/secreqgen/generation/generator.py`**

```python
@dataclass
class GenerationConfig:
    rag_enabled: bool = True
    generate_threat_mappings: bool = True
    max_requirements_per_story: int = 10

class SecurityRequirementsGenerator:
    def __init__(self, config, retriever=None, llm_client=None): ...
    
    def generate(self, story: UserStory, gen_config=None) -> GenerationResult:
        # 1. If RAG enabled: retrieve context
        # 2. Build prompt with context (or "no context" for baseline)
        # 3. Call LLM
        # 4. Parse and validate requirements
        # 5. Return GenerationResult
    
    def generate_batch(self, stories: list[UserStory]) -> list[GenerationResult]: ...
    def generate_threat_mapping(self, requirement) -> RequirementWithThreats: ...
```

**Baseline mode**: Same generator with `rag_enabled=False` and `retriever=None`.

---

## Step 3.5: Output Formatter
**Create `src/secreqgen/output/formatter.py` and `exporter.py`**

```python
class OutputFormatter:
    def to_json(self, result: GenerationResult) -> str: ...
    def to_markdown(self, result: GenerationResult) -> str: ...

class ResultExporter:
    def export_result(self, result, format="json", filename=None) -> Path: ...
    def export_batch(self, results, format="json") -> list[Path]: ...
```

---

## Step 3.6: Wire Generation CLI
Update `cli.py`:
- `generate requirements <input> --output <file> --format json|markdown --baseline`
- Load stories from JSON/YAML file
- Run generator (RAG or baseline mode)
- Export results

---

## Completion Checklist
- [ ] Ollama client connecting and generating
- [ ] Prompts loading and rendering correctly
- [ ] Retriever returning relevant chunks
- [ ] Generator producing valid SecurityRequirement objects
- [ ] Baseline mode working (no RAG)
- [ ] JSON and Markdown export working
- [ ] CLI generate command functional
- [ ] End-to-end test: story → requirements
- [ ] docs/log.md updated
