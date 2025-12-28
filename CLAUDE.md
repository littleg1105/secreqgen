# SecReqGen - Claude Code Instructions

## Project Overview
RAG-enhanced security requirements generator for Master's thesis. Compares RAG vs zero-shot LLM generation using Precision/Recall/F1 metrics.

## Critical Rules

1. **DOCUMENT EVERYTHING**: After each step, update `docs/log.md` with what/why/decisions
2. **ONE STEP AT A TIME**: Complete and test each step before moving on
3. **ASK BEFORE ASSUMING**: Unclear requirements → ask for clarification
4. **COMMIT OFTEN**: Atomic commits after each logical unit

## Tech Stack
- Python 3.11-3.13 (3.14 not supported), Poetry, Typer (CLI)
- LangChain, ChromaDB, sentence-transformers
- Ollama (Llama 3 8B / Mistral 7B)
- Pydantic 2.0 for schemas

## Development Environment
Use a Python venv for dependency isolation:
```bash
# Create venv with Python 3.13 (or 3.11/3.12)
python3.13 -m venv .venv
source .venv/bin/activate

# Install Poetry and dependencies
pip install poetry
poetry install
```

## Project Structure
```
src/secreqgen/
├── cli.py              # Typer CLI
├── config.py           # Pydantic Settings
├── ingestion/          # Doc loading, chunking, embeddings
├── retrieval/          # ChromaDB, RAG retriever
├── generation/         # LLM client, prompts, generator
├── output/             # Formatters, exporters
└── evaluation/         # Semantic matching, metrics
```

## Current Phase
Check `docs/log.md` for current implementation status.

## Commands
- Load phase details: `/phase N` (custom slash command)
- Document step: Update `docs/log.md`
- Run tests: `.venv/bin/python -m pytest tests/ -v`
- Run CLI: `.venv/bin/python -m secreqgen <command>`

## Troubleshooting

### "ModuleNotFoundError: No module named 'secreqgen'"
The package must be installed in editable mode for imports to work. Run:
```bash
.venv/bin/pip install -e .
```
This happens after:
- Recreating the venv
- Running `poetry install` (should do it automatically, but sometimes fails)
- Any corruption of the venv's site-packages

## Key Design Decisions
- **Embeddings**: all-mpnet-base-v2 (better semantic nuance)
- **Similarity threshold**: 0.85 for matches, 0.70-0.85 for review
- **Chunking**: 512 tokens max, 50 token overlap
- **Always inject**: System context YAML into every prompt

## Documentation Template
After completing work, add to `docs/log.md`:
```markdown
## [Date] Step X.Y: [Name]
**Files**: [list]
**What**: [description]
**Why**: [rationale]  
**Decisions**: [choices made]
**Next**: [what's next]
```