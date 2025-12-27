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

## Development

```bash
# Run tests
poetry run pytest tests/ -v

# Format code
poetry run black src/
poetry run ruff check src/
```
