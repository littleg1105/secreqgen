# Test on an outside product (mattermost)

The lab works on three products (Study 2). This test uses a product that the tuning never used. It has three parts. All writes go below `real_test_cases/tuning/` through the write guard.

## Parts

1. `scripts/tuning_lib/xproject.py` (shared code)
   - `ALLOWED_PRODUCTS`: the allow-list (now only `mattermost`: documentation folder and repository folder).
   - `load_xstories(project)`: the 8 stories from `real_test_cases/<project>/user_stories.json`. A lab story id (dev or hold-out, from `splits.json`) is refused.
   - `read_xfrozen(...)`: reads `tuning/_xproject/<project>/<model folder>/retrieval_frozen.json`. It checks the hash, refuses the 41-character fallback text, and needs one documentation passage or more.
   - `XProjectBuilder`: a subclass of `PromptBuilder`. Only the stories and the frozen blocks come from other sources. `build` is the lab code, so the prompt is built in the same way. `verify_v0_old` is switched off (no stored prompt exists).
   - `check_persist_dir(path)`: the guard for a new vector store folder (inside `tuning/`, never inside `secreqgen/data/`, links followed).
   - `write_evidence_files(project)`: one Markdown file for each story in `tuning/_judging/evidence_x/`.
2. `scripts/xproject_retrieve.py`: the search step with the Study 2 configuration (embedder, new vector store, `SimpleRetriever` with `CrossEncoderReranker` over 30 candidates, `Idea19Retriever`, the code agent is the other model). It writes `retrieval_frozen.json` and `manifest.json` for each model. It never overwrites a file. It needs `PYTHONHASHSEED=0`.
3. `scripts/xproject_run.py`: the generations on the hosted models. It uses the code of `run_tuning.py` (client, request, retries, parser, records, executor). It writes the manifest and a short `report.md` (no name measures).

## How to run

Run all commands from `coded_project/secreqgen`. Never run two experiment scripts at once.

```
# 1. check only (no model, no Ollama, no write)
PYTHONHASHSEED=0 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:scripts .venv/bin/python scripts/xproject_retrieve.py --project mattermost --dry-run

# 2. the real search (Ollama needed)
PYTHONHASHSEED=0 TOKENIZERS_PARALLELISM=false PYTHONPATH=src:scripts .venv/bin/python scripts/xproject_retrieve.py --project mattermost

# 3. evidence files for the judges
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:scripts .venv/bin/python -c "from tuning_lib.xproject import write_evidence_files; print(*write_evidence_files('mattermost'), sep='\n')"

# 4. check the generations, then run them
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:scripts .venv/bin/python scripts/xproject_run.py --project mattermost --run-name x1_mattermost --versions v0_old,v3_pn_render_fact_hits --repeats 3 --dry-run
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:scripts .venv/bin/python scripts/xproject_run.py --project mattermost --run-name x1_mattermost --versions v0_old,v3_pn_render_fact_hits --repeats 3
```

Judging works with no change: `tuning_lib.judging.build_batches(run_dir, versions=[...], stories=[the 8 ids], per_cell=..., seed=..., round_name=...)`.

## Differences from the lab

- Records have `source_study = "xproject"`. `source_file` is the frozen file; `source_line` is 1.
- The cost estimate uses the prompt size and a fixed 2100 answer tokens for each generation (no stored Study 2b figures exist).
- Not made: the v0_old check against a stored prompt, the dev and hold-out split, the lab report with name measures.
