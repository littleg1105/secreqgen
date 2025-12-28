# Phase 3: RAG Pipeline & Generation (Steps 3.1-3.6)

## Design Philosophy

**Keep it simple first, optimize based on data.**

This phase builds the minimum viable RAG pipeline to enable thesis comparison between:
- **Zero-shot**: LLM generates requirements without retrieved context
- **RAG-enhanced**: LLM generates requirements with retrieved security documentation

The goal is to get an end-to-end working system quickly, then measure. Retrieval optimizations (re-ranking, query expansion) are deferred to Phase 5, and only pursued if evaluation data shows retrieval is a bottleneck.

---

## Step 3.1: Ollama LLM Client
**Create `src/secreqgen/generation/llm_client.py`**

```python
from dataclasses import dataclass
from pydantic import BaseModel
import httpx

@dataclass
class LLMResponse:
    content: str
    model: str
    usage: dict  # token counts for analysis
    latency_ms: float

class OllamaClient:
    def __init__(self, base_url="http://localhost:11434", model="llama3:8b"):
        self.base_url = base_url
        self.model = model
        self._client = httpx.Client(timeout=120.0)

    def generate(self, prompt: str, system_prompt: str = None) -> LLMResponse: ...

    def generate_structured(
        self,
        prompt: str,
        output_schema: Type[BaseModel],
        max_retries: int = 2
    ) -> BaseModel:
        """Generate and parse as Pydantic model with retry on parse failure."""
        ...

    def health_check(self) -> bool:
        """Check if Ollama is running and model is available."""
        ...
```

**Key features:**
- Use httpx with 2min timeout (LLM can be slow)
- `generate_structured`: Inject JSON schema hint, parse response, validate with Pydantic
- Retry logic for structured output (JSON can be malformed)
- Extract JSON from markdown code blocks if present
- Track token usage and latency for thesis analysis

---

## Step 3.2: Prompt Templates
**Create `src/secreqgen/generation/prompts.py`**

```python
from dataclasses import dataclass
from string import Template

@dataclass
class PromptTemplate:
    name: str
    template: str

    def format(self, **kwargs) -> str:
        return Template(self.template).safe_substitute(**kwargs)

# Registry of prompts
PROMPTS = {
    "system": PromptTemplate(...),
    "generate_requirements": PromptTemplate(...),
    "generate_requirements_baseline": PromptTemplate(...),  # No context version
}
```

**Create prompt files in `prompts/` directory:**

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

## Retrieved Security Documentation
{retrieved_context}

## User Story
ID: {story_id}
Title: {story_title}
Story: {story_text}
Acceptance Criteria: {acceptance_criteria}

## Task
Generate security requirements for this user story. For each requirement include:
- title, description, rationale
- acceptance_criteria (testable)
- asvs_category, asvs_requirement_id (if applicable)
- priority (critical/high/medium/low)
- implementation_notes (specific to system context)

Respond with JSON: {"requirements": [...]}
```

`prompts/requirement_generation_baseline.md`:
```
(Same as above but WITHOUT the "Retrieved Security Documentation" section)
```

---

## Step 3.3: Simple Retriever
**Create `src/secreqgen/retrieval/retriever.py`**

```python
from dataclasses import dataclass

@dataclass
class RetrievalResult:
    chunks: list[str]           # Raw chunk content
    sources: list[str]          # Source document paths
    sections: list[str]         # Section hierarchies
    scores: list[float]         # Similarity scores

class SecurityRetriever:
    """Simple semantic retriever. No re-ranking, no query expansion."""

    def __init__(self, vectorstore, embedder):
        self.vectorstore = vectorstore
        self.embedder = embedder

    def retrieve(
        self,
        query: str,
        doc_types: list[DocumentType] | None = None,
        top_k: int = 5
    ) -> RetrievalResult:
        """Retrieve relevant chunks using semantic similarity."""
        embedding = self.embedder.embed_text(query)
        results = self.vectorstore.query(
            query_embedding=embedding,
            doc_types=doc_types,
            n_results=top_k
        )
        return RetrievalResult(
            chunks=[r.content for r in results],
            sources=[r.source_document for r in results],
            sections=[r.section_hierarchy for r in results],
            scores=[r.similarity for r in results]
        )

    def retrieve_for_story(
        self,
        user_story: str,
        acceptance_criteria: list[str] | None = None
    ) -> str:
        """Retrieve and format context for LLM injection."""
        # 1. Build query from story + criteria
        query = user_story
        if acceptance_criteria:
            query += " " + " ".join(acceptance_criteria)

        # 2. Retrieve from all relevant doc types
        result = self.retrieve(query, top_k=5)

        # 3. Format as numbered context blocks
        formatted = []
        for i, (chunk, source, section) in enumerate(
            zip(result.chunks, result.sources, result.sections), 1
        ):
            formatted.append(f"[{i}] Source: {source}\nSection: {section}\n{chunk}")

        return "\n\n---\n\n".join(formatted)

    def get_system_context(self) -> str:
        """Always retrieve system context for injection."""
        results = self.vectorstore.query(
            query_embedding=None,  # Get all from system_context collection
            doc_types=[DocumentType.SYSTEM_CONTEXT],
            n_results=1
        )
        return results[0].content if results else ""
```

**Why keep it simple?**
- Phase 2 testing showed semantic search finds relevant content (correct answer was in top-10)
- Vocabulary mismatch issues exist but may not block thesis comparison
- Optimization should be data-driven (see Phase 5)
- Faster to build, easier to debug

**What's intentionally NOT included:**
- ❌ Re-ranking (deferred to Phase 5)
- ❌ Query expansion (deferred to Phase 5)
- ❌ Hybrid search (deferred to Phase 5)

---

## Step 3.4: Requirements Generator
**Create `src/secreqgen/generation/generator.py`**

```python
from dataclasses import dataclass
from enum import Enum

class GenerationMode(Enum):
    ZERO_SHOT = "zero_shot"     # No retrieval (baseline)
    RAG = "rag"                 # With retrieval

@dataclass
class GenerationConfig:
    mode: GenerationMode = GenerationMode.RAG
    max_requirements_per_story: int = 10
    include_threat_mapping: bool = False  # Optional, for later

class SecurityRequirementsGenerator:
    def __init__(
        self,
        llm_client: OllamaClient,
        retriever: SecurityRetriever | None = None,  # None for zero-shot
        config: GenerationConfig | None = None
    ):
        self.llm = llm_client
        self.retriever = retriever
        self.config = config or GenerationConfig()

    def generate(self, story: UserStory) -> GenerationResult:
        """Generate security requirements for a user story."""

        # 1. Get system context (always)
        system_context = ""
        if self.retriever:
            system_context = self.retriever.get_system_context()

        # 2. Get retrieved context (only in RAG mode)
        retrieved_context = ""
        if self.config.mode == GenerationMode.RAG and self.retriever:
            retrieved_context = self.retriever.retrieve_for_story(
                story.story,
                story.acceptance_criteria
            )

        # 3. Build prompt
        if self.config.mode == GenerationMode.RAG:
            prompt = PROMPTS["generate_requirements"].format(
                system_context=system_context,
                retrieved_context=retrieved_context,
                story_id=story.id,
                story_title=story.title,
                story_text=story.story,
                acceptance_criteria="\n".join(story.acceptance_criteria or [])
            )
        else:
            prompt = PROMPTS["generate_requirements_baseline"].format(
                system_context=system_context,
                story_id=story.id,
                story_title=story.title,
                story_text=story.story,
                acceptance_criteria="\n".join(story.acceptance_criteria or [])
            )

        # 4. Call LLM
        response = self.llm.generate_structured(
            prompt=prompt,
            output_schema=RequirementsResponse,
            system_prompt=PROMPTS["system"].template
        )

        # 5. Build result
        return GenerationResult(
            user_story=story,
            requirements=response.requirements,
            generated_at=datetime.now(),
            model_used=self.llm.model,
            mode=self.config.mode,
            retrieved_context=retrieved_context if self.config.mode == GenerationMode.RAG else None
        )

    def generate_batch(self, stories: list[UserStory]) -> list[GenerationResult]:
        """Generate requirements for multiple stories."""
        return [self.generate(story) for story in stories]
```

**Key design decisions:**
- `GenerationMode` enum makes baseline vs RAG comparison explicit
- Same generator class for both modes (consistent processing)
- `retrieved_context` saved in result for analysis
- System context always included (even in zero-shot) for fair comparison

---

## Step 3.5: Output Formatter
**Create `src/secreqgen/output/formatter.py` and `exporter.py`**

```python
class OutputFormatter:
    def to_json(self, result: GenerationResult, indent: int = 2) -> str:
        """Export as formatted JSON."""
        return result.model_dump_json(indent=indent)

    def to_markdown(self, result: GenerationResult) -> str:
        """Export as readable Markdown."""
        lines = [f"# Security Requirements for {result.user_story.title}\n"]
        lines.append(f"**Generated**: {result.generated_at}")
        lines.append(f"**Mode**: {result.mode.value}")
        lines.append(f"**Model**: {result.model_used}\n")

        for req in result.requirements:
            lines.append(f"## {req.id}: {req.title}")
            lines.append(f"**Priority**: {req.priority}")
            lines.append(f"**ASVS**: {req.asvs_category}")
            lines.append(f"\n{req.description}\n")
            lines.append(f"**Rationale**: {req.rationale}")
            lines.append(f"**Acceptance Criteria**: {req.acceptance_criteria}\n")

        return "\n".join(lines)

class ResultExporter:
    def export(self, result: GenerationResult, path: Path, format: str = "json"):
        """Export result to file."""
        formatter = OutputFormatter()
        if format == "json":
            content = formatter.to_json(result)
        elif format == "markdown":
            content = formatter.to_markdown(result)
        else:
            raise ValueError(f"Unknown format: {format}")

        path.write_text(content)
        return path
```

---

## Step 3.6: Wire Generation CLI
**Update `src/secreqgen/cli.py`**

```python
@generate_app.command("requirements")
def generate_requirements(
    input_file: Path = typer.Argument(..., help="JSON/YAML file with user stories"),
    output: Path = typer.Option(None, help="Output file path"),
    format: str = typer.Option("json", help="Output format: json or markdown"),
    baseline: bool = typer.Option(False, "--baseline", help="Run without RAG (zero-shot)"),
):
    """Generate security requirements for user stories."""
    # 1. Load stories from file
    # 2. Initialize generator (with or without retriever based on --baseline)
    # 3. Generate requirements
    # 4. Export results
```

**Commands:**
- `secreqgen generate requirements stories.json` → RAG-enhanced generation
- `secreqgen generate requirements stories.json --baseline` → Zero-shot generation
- `secreqgen generate requirements stories.json --output results.md --format markdown`

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

---

## What Comes Next

After completing Phase 3:

1. **Run Phase 4 (Evaluation)** to measure:
   - Does RAG improve over zero-shot?
   - By how much? (Precision, Recall, F1)

2. **Based on evaluation results**, decide if Phase 5 (Retrieval Optimization) is needed:
   - If RAG significantly beats zero-shot → thesis success, optimization is bonus
   - If RAG marginally beats zero-shot → investigate if retrieval is the bottleneck
   - If RAG doesn't beat zero-shot → debug pipeline (something is wrong)

See **Phase 5: Retrieval Optimization (Optional)** for enhancement options.
