# SecReqGen: instructions for an AI coding agent

## What this repository is

SecReqGen is the tool of an MSc thesis. For one user story, it writes security requirements and a draft threat list. It uses the product documentation and local Ollama models. The thesis record (the experiments and their numbers) points at the files of this repository, so some files are frozen.

## Read first

1. `docs/README.md`: which documents are current and which are old.
2. `scripts/README.md`: the lab record. It explains each experiment script.
3. The top block of `docs/HANDOFF.md` (the block that says "read this block first"). Lower blocks are history.

Do not write entries in `docs/log.md`. It is an old file. It does not hold the current state.

## Do not touch

Never edit, move or delete these files. This is the only copy of the list. Other pages point to it.

| Item | Reason |
|---|---|
| Everything that exists today in `scripts/`, `data/`, `docs/` and `configs/` | It is the record of the experiments. Some files are hash-pinned. |
| `src/secreqgen/ingestion/*.py` | Hash-pinned. |
| `src/secreqgen/threats/prompts/*` | Hash-pinned. They are the threat prompts that the tests used. |
| `src/secreqgen/generation/prompt_sets/**` | Hash-pinned. They are the prompts of the experiments. |
| `poetry.lock`, `requirements-tested.txt`, `pyproject.toml` | They fix the tested environment. |
| `conftest.py` in the repository root | The tests depend on it. |
| `src/secreqgen/retrieval/retriever.py` and `src/secreqgen/config.py` | The thesis numbers build reads them. |
| `src/secreqgen/evaluation/semantic_matcher.py`, `metrics.py` and `multi_run.py` | Chapter 6 cites their lines. |
| `src/secreqgen/generation/*.py` | `scripts/run_study2b.py` hashes them. |
| `src/secreqgen/retrieval/word_search.py` and `passage_edits.py` | Tests check their source text. |
| `src/secreqgen/threats/pipeline.py`, `checks.py` and `render.py` | They were tested on 17 November 2026. |
| `tests/unit/test_cli.py` | The thesis numbers name it. |
| `tests/unit/test_tuning_*.py`, `tests/unit/test_study2*.py` | They test the lab scripts and belong to the lab record. |

A task can name one of these files and order a change. Then change only that file.

## Rules for a change

- Behaviour stays the same. The same commands, options, defaults, help texts, console lines, written files and prompts stay. Change them only when the owner orders it.
- Put no home-folder path in `src/`. Put no absolute path of one machine in any file.
- Add no new dependency without the owner's word.
- Never run a lab script again. It can overwrite the record or call a model for hours. Read it, do not run it.
- The retrieval `similarity_threshold` is 0.4. It is never 0.7. The value 0.7 was the fault that the audit of 19 May 2026 found. With 0.7, every search returned the fallback text and no documentation.
- Add new tests as new files. Change another existing test file only together with the code that it tests, and only when the owner orders a change of behaviour.
- Write comments, docstrings and commit text in Simplified Technical English. Use no em-dash.

## Commands

Run all commands from the repository root.

```bash
./scripts/setup-venv.sh                      # make .venv (Python 3.13)
PYTHONPATH=src .venv/bin/python -m pytest tests/unit -m "not slow" -q
.venv/bin/ruff check src                     # report only
PYTHONPATH=src .venv/bin/python -m secreqgen --help
```

Old code has 294 ruff messages. Never run `ruff --fix`, `ruff format` or `black` on it. New code must have none.

Set `PYTHONPATH=src` for every Python command. Set `TOKENIZERS_PARALLELISM=false` when a command embeds text.

The command line has five top-level commands: `version`, `config`, `ingest`, `generate` and `evaluate`. Run `<group> --help` to see the sub-commands and options.

The unit tests cannot reach a model server. A connection to port 11434 is refused, so a test that needs a model fails at once. Tests with a real model are in `tests/integration`.

Four tests of the Study 2b runner are skipped in a copy of the repository. The script `scripts/run_study2.py` names the registered workspace as a fixed path, and the guards protect only that path. In a copy, the guards do not refuse the folders of the copy, and one of the four tests started real model calls.

## macOS: imports fail

Use `./scripts/setup-venv.sh`. It adds `PYTHONPATH` to `.venv/bin/activate`.

Symptom: `ModuleNotFoundError: No module named 'secreqgen'`, even after `pip install -e .`.

Cause: Python skips `.pth` files that have the BSD `hidden` flag (CPython issue 113659). On macOS, pip can set this flag. It can come back after any pip operation.

Fix 1 (best): set `PYTHONPATH=src`. This does not use `.pth` files.

Fix 2: remove the flag. First look for `hidden` in the flags column:

```bash
ls -lO .venv/lib/python3.13/site-packages/*.pth
chflags -R nohidden .venv/lib/python3.13/site-packages/*.pth
```

## Old slash commands

The files `.claude/commands/log.md`, `status.md` and `step.md` still name `docs/log.md`. Do not follow them. Use the "Read first" list above.
