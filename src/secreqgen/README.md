# Code map of `src/secreqgen`

This page is for a developer who opens the package for the first time. It shows where each part lives and which rules hold.

## The tree

```
src/secreqgen/
  __init__.py, __main__.py   version and the entry for `python -m secreqgen`
  config.py                  settings (Pydantic, `SECREQGEN_` prefix)
  exceptions.py              the error classes of the package
  logging.py                 legacy log setup with Rich (nothing imports it)
  cli/                       command line: Typer and Rich only
    __init__.py              re-exports; holds `_ollama_client`, the test patch point
    _app.py                  the apps, the console and `DOC_TYPE_MAP`
    root.py                  `version` and `config`
    ingest.py                `ingest` commands (older path)
    generate.py              `generate` commands (`requirements`, `single`, `threats`, `check-ollama`)
    evaluate.py              `evaluate` commands (older path)
    __main__.py              entry for `python -m secreqgen.cli`
  application/               use cases; no Typer, no Rich, no print, no `sys.exit`
    __init__.py              the layer rule, in the docstring
    clients.py               `build_ollama_client`, `build_evaluation_client`, `build_health_client`
    retrievers.py            `build_embedder`, `build_vector_store`, `build_vector_retriever`, `build_word_retriever`
    errors.py                `ApplicationError`, `InputError`, `NothingToDoError`, `ModelServerError`
    events.py                `ProgressEvent`, `ProgressCallback`
    inputs.py                checks and readers for input files
    requirements.py          stages, `RequirementsOptions`, `RequirementsRun`, `run_requirements`
    threats.py               stages, `ThreatOptions`, `ThreatRun`, `run_threat_lists`
  generation/                prompts, Ollama client, generator, schemas, `prompt_sets/`
  retrieval/                 `__init__.py` loads names on first use; vector store, retriever,
                             reranker, `word_search.py`, `passage_edits.py`
  threats/                   threat list: `actors.py`, `checks.py`, `pipeline.py`, `render.py`, `prompts/`
  ingestion/                 loader, chunker, embedder, metadata
  evaluation/                baseline, metrics, semantic matcher, `multi_run.py`, pipeline, report
  output/                    formatter and exporter
  verification/              legacy judge (nothing imports it)
```

## The layer rule

| Layer | May import | Must not |
|---|---|---|
| cli | application, config, exceptions, typer, rich. Domain packages only inside command bodies. | Heavy libraries at module level. |
| application | config, exceptions. Domain packages only inside function bodies. | cli, typer, rich, print, `sys.exit`, logging setup, a default path in the working folder. |
| domain (generation, retrieval, threats, evaluation, output, ingestion) | each other, as before | application, cli |
| gui, http (planned) | application, config | cli |

Domain modules log through `logging`. A GUI can attach a handler.

## Two levels in `application/`

1. Stage functions. Each does one job and takes plain arguments. A command calls them in its own order and keeps its own console lines.
2. Run functions: `run_requirements` and `run_threat_lists`. They call the same stages in order and raise typed errors. A GUI or an HTTP API can call them. They take options in and give a result out.

The unit tests run the command and the run function on one input and compare the output.

## The tested path and the older path

The tested path is the one that the thesis measured:

- `generate requirements --docs`, `generate single --docs` and `generate threats`.
- Search: the word search (`retrieval/word_search.py`) over a documentation folder.
- Prompts: the tested prompt sets in `generation/prompt_sets/` and `threats/prompts/`.

The older path uses the vector store and the embedder:

- `ingest ...`, `generate` without `--docs`, and `evaluate ...`.
- Their long command bodies stay as they are, by choice. They only use the shared builders.

## Legacy parts

`verification/` and `logging.py` have no importer. Chapter 5 names them, so they stay. Do not build on them.

## Pinned files

Some files are frozen because the thesis record pins them. The list is in `CLAUDE.md`, section "Do not touch". It is the only copy. Read it before you edit.

## Test patch points

| Name | Module | Note |
|---|---|---|
| `_ollama_client` | `secreqgen.cli` | `cli/generate.py` looks it up at call time through `_client`. |
| `DocumentEmbedder` | `secreqgen.ingestion` | The builders import it inside their bodies. |
| `SecReqGenVectorStore`, `SimpleRetriever` | `secreqgen.retrieval` | Same. |
| `OllamaClient` | `secreqgen.generation` | Same. |

Patch the package-level name, not the defining module. The builders import at call time, so the patch takes effect.

## Golden tests

`tests/unit/test_cli_golden.py` pins the console text, exit codes, written files, stand-in constructor calls and the prompts sent. The expected text of each case is a file in `tests/unit/golden/cli/`.

- A new case needs a new golden file. A changed console line needs a new recording.
- Record again only for a change that you intend: `GOLDEN_RECORD=1` and run that one test file.
- Then read the diff of the golden files line by line. A diff that you did not intend is a bug in the code, not in the file.
- A newer Rich or Click can change panel layout. Record again, then read the diff.
