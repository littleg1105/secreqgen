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

### Draft threat list of a user story

`secreqgen generate threats` writes a draft threat list for each user story. It uses the word search
of `--docs` above. Each row comes from one sentence of the documentation: an attack that the
documentation describes, or the breach of a security rule that it states. A row holds the exact quote,
the threat, a requirement, a threat actor and a STRIDE category. A script checks that each quote is in
its passage. The list is a draft for a person to check. It holds only what the documentation states,
so it is not a complete threat model.

```bash
secreqgen generate threats stories.json --docs path/to/product/docs --output threat_lists
```

- Output: one folder for each story, with `threats.md` (the review page) and `threats.json`.
- Default setup (the tested one): `gemma4:26b` writes the rows, and `qwen3.8:27b` checks each row with
  its passage in view. The rows that the second model accepts come first on the page. No row is removed.
  Ollama must have the two models.
- `--model NAME` changes the model that writes. Without it, the setting `SECREQGEN_LLM_MODEL_NAME`
  applies when you set it. `--checker-model NAME` changes the second model. `--no-check` runs with
  no second model; then the page puts the rows with no weak sign first (signs that a script finds).
- `--about-file FILE` gives the model that writes the rows one paragraph about the product (120 words
  at most) as background. It is not a source: each row still needs a quote from a passage. The second
  model does not get it. In the test the paragraph did not lower the number of wrong rows, so it is an
  option only.
- `--no-labels` leaves out the call that names the threat actor and the STRIDE category. The two
  labels are the model's choice from the threat sentence. They have no source in the documentation.
- Measured on 17 November 2026 (16 stories of 5 products; 4 of them fully new, 12 used before to choose
  a prompt version; two language-model judges that did not know the setup of a row, and a third
  reading where they differed). Default setup: of the 61 rows in the first group of the page, 41 were
  specific and supported by the documentation, 13 were general advice and 7 were wrong. With
  `qwen3.8:27b` as the writer and `gemma4:26b` as the checker: 30 of 102 rows in the first group were wrong.

## Development

```bash
# Run tests
poetry run pytest tests/ -v

# Format code
poetry run black src/
poetry run ruff check src/
```
