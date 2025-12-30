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

**Recommended: Use the setup script** (handles macOS .pth issues automatically):
```bash
./scripts/setup-venv.sh
source .venv/bin/activate
```

**Manual setup** (if you prefer):
```bash
python3.13 -m venv .venv
source .venv/bin/activate
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

### macOS: Imports fail even after `pip install -e .`
**Symptom:** Python shows `ModuleNotFoundError` even though `.pth` file exists in site-packages.

**Root Cause:** This is a **Python security feature** (see [CPython Issue #113659](https://github.com/python/cpython/issues/113659)). Python 3.8+ intentionally skips .pth files with the BSD `hidden` flag to prevent malicious code execution. On macOS Sequoia, APFS may set this flag on pip-created files.

**Best Solution - Use PYTHONPATH:**
```bash
# Recreate venv with the setup script (adds PYTHONPATH to activate)
./scripts/setup-venv.sh
source .venv/bin/activate
```

**Quick Fix - Remove hidden flag:**
```bash
# Check if .pth files have hidden flag
ls -lO .venv/lib/python3.13/site-packages/*.pth
# Look for "hidden" in the flags column

# Remove hidden flag
chflags -R nohidden .venv/lib/python3.13/site-packages/*.pth
```

**Why PYTHONPATH is better:** The hidden flag can reappear after pip operations. Using PYTHONPATH completely bypasses .pth files, making imports reliable regardless of file flags.

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