# SecReqGen

RAG-enhanced security requirements generator for Master's thesis.

## Overview

SecReqGen is a CLI tool that generates security requirements from user stories using:
- RAG (Retrieval-Augmented Generation) with security knowledge bases
- Zero-shot LLM generation for baseline comparison

## Installation

```bash
# Create virtual environment with Python 3.13
python3.13 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install poetry
poetry install
```

## Usage

```bash
# Show configuration
secreqgen config

# Ingest documents
secreqgen ingest documents <directory> --type policies

# Generate requirements
secreqgen generate requirements <input> --output <file>

# Evaluate results
secreqgen evaluate compare <generated> <ground_truth>
```

### The tested setup: word search over a documentation folder

The option `--docs` runs the setup that the final test of 17 November 2026 tested
(configuration `s1_word_3000`): a word search (BM25) over the Markdown files of a
documentation folder, about 3,000 tokens of whole pages in the prompt, and the tested
prompt set. It needs no `ingest` step, no vector store and no embedding model.

```bash
export SECREQGEN_LLM_MODEL_NAME=gemma4:26b    # a model that your Ollama server has

secreqgen generate requirements stories.json \
    --docs path/to/product/docs \
    --context path/to/system_context.yaml \
    --product keycloak \
    --format json --output requirements.json

secreqgen generate single --title "Session timeout" \
    --story "As a user, I want my session to end after idle time, so that nobody uses it." \
    --docs path/to/product/docs
```

- `stories.json` holds `{"project": "...", "user_stories": [{"id", "title", "story", "acceptance_criteria"}]}`.
- `--product keycloak` switches on the cleaning of Keycloak template text. For other products the option changes nothing.
- With `--docs` the tool calls the model as the tested runs did: no JSON-mode option, a time limit of 900 s
  and a context window of 16384 tokens. The settings `SECREQGEN_LLM_STRUCTURED_OUTPUT`, `SECREQGEN_LLM_TIMEOUT`
  and `SECREQGEN_LLM_NUM_CTX` change these values.
- Without `--docs` the tool works as before (vector store and the older prompt). The three settings above
  are new: when you set one, it applies to that older path also.
- `--baseline` cannot be used with `--docs`: no test ran the tested prompt in a zero-shot mode.
- If no word of a story is in the documentation, the tool writes a warning and uses the first pages of the folder.
- Difference from the lab: the lab could add a code-search hit when a story holds a code name of the product.
  The command line has no code search. In the final test, no prompt of the new tool held such a hit (0 of 222).
- The unit test `tests/unit/test_word_search.py` checks that the search, the passage edits and the whole prompt
  equal the lab code and the stored lab prompts.

## Development

```bash
# Run tests
poetry run pytest tests/ -v

# Format code
poetry run black src/
poetry run ruff check src/
```
