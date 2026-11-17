# scripts/: the record of how the results were made

This folder holds the scripts that produced the results of the thesis. It has 71 Python files (56 in this folder, 15 in `tuning_lib/`), 10 shell files and one text file (`repo_targets.txt`).

**Do not edit, move or delete any file here.** The scripts are the record. They are kept as they were run. Some files are also pinned by a hash list (see "Pinned files"). A change of one byte breaks a pin.

Read this page first. Then open a script. Each script starts with a docstring that tells what it does.

## Periods and studies, in time order

Many files entered git in one bulk commit on 25 October 2026. A git date is therefore not the work date. The dates below come from the docstrings and from the documents named in the last column.

| Period | Date | Scripts | Source document |
|---|---|---|---|
| 1. Dataset and early analysis | up to January 2026 | dataset builders and `threshold_analysis.py` | `threshold_analysis.py` is the oldest file (git date 1 January 2026) |
| 2. Path A and pilots (before the audit) | before 19 May 2026 | pilot summaries, figures, deck, qwen3 against qwen3.6 | `docs/HANDOFF.md` |
| 3. Audit follow-up and ablation lab | May 2026 | judges, 10 ideas, cross-family run | `docs/RAG_ABLATION_LAB_NOTEBOOK.md` sections 1 to 10 |
| 4. Adversarial probe and idea 21 | 24 May 2026 | probe and idea 21 scripts | lab notebook sections 11 and 12 |
| 5. Study 2 | 2 November 2026 (run); review sheet v2 14 and 15 November 2026 | Study 2 driver, analysis, review tools, AI raters | lab notebook sections 14 and 15; `thesis/analysis_lock.md` |
| 6. Study 2b | 14 to 16 November 2026 | Study 2b driver and analysis | lab notebook section 16 |
| 7. Prompt lab, search test, outside product | 16 November 2026 | tuning runner, `tuning_lib/`, search freeze | `thesis/analysis_lock.md` |
| 8. Final test | 17 November 2026 | the `final_test_*` scripts | `thesis/analysis_lock.md` |

Do not cite results from these scripts as RAG results in two cases:

- Periods 2 (Path A and pilots) and the first cross-family matrix of May 2026 ran before the audit of 19 May 2026. The audit found that every retrieval had returned the fallback string. Those results measure prompt structure, not RAG.
- The idea 21 scripts (period 4) have a confounded result (lab notebook section 12).

The column "Pinned" below shows a hash list name or "no". The lists are in `thesis/evidence/` in the root repository:

- `study2_registered_hashes_2026-11-14.sha256` (short name: **study2 list**)
- `final_test_tool_freeze_2026-11-16.sha256` (short name: **tool freeze list**)

## 1. Dataset and early analysis

| File | What it does | Pinned |
|---|---|---|
| `asvs_templates.py` | ASVS-aligned ground-truth requirement templates. | no |
| `bootstrap_dataset.py` | Builds an evaluation dataset from one GitHub repository URL. Imports `asvs_templates`. | no |
| `fetch_docs_for_unevaluated.py` | Fetches README and SECURITY.md files for 6 projects. | no |
| `threshold_analysis.py` | Threshold sensitivity analysis of evaluation results. | no |

## 2. Path A and pilots (before the audit)

| File | What it does | Pinned |
|---|---|---|
| `meta_analysis.py` | Cross-project meta-analysis for the Path A claim. | no |
| `synthesize_pilot_v2.py` | Cross-project synthesis of pilot results. | no |
| `generate_figures.py` | Makes PNG figures from the pilot v2 and v3 results. | no |
| `generate_powerpoint.py` | Makes the PowerPoint deck of the status update. | no |
| `cross_model_comparison.py` | Compares qwen3 with qwen3.6 (the qwen3.6 sub-test). | no |

## 3. Audit follow-up and ablation lab (May 2026)

| File | What it does | Pinned |
|---|---|---|
| `judge_content_quality.py` | LLM judge of the quality of generated requirements. | no |
| `judge_retrieval_quality.py` | LLM judge of retrieval quality (follow-up of the audit of 19 May 2026). | no |
| `generate_judge_calibration_sample.py` | Makes a sample for hand labelling, to calibrate the judge. | no |
| `score_judge_calibration.py` | Scores the hand-labelled calibration sample. | no |
| `rag_ablation_study.py` | Phase A: retrieval ablation, 10 ideas on 5 projects. | no |
| `rag_ablation_llm_query.py` | Ideas 11, 12 and 16: query changes made by a local model. | no |
| `rag_ablation_generation.py` | Ideas 13, 14 and 15: changes to generation (few-shot). | no |
| `rag_ablation_agents.py` | Ideas 17 to 20: a local model as retrieval agent (documents and code). | no |
| `rag_ablation_judge.py` | Judges each idea and project cell (P@k and nDCG@5). | no |
| `run_cross_family_winning_config.py` | Cross-family run with the winning configuration. Other scripts import it. | no |
| `inspect_cross_family_results.py` | Per-cell sanity check of the cross-family results. | no |
| `read_everything.py` | Qualitative pass over 20 cells and 8 stories. | no |
| `dump_all_content_for_claude.py` | Dumps all generated requirements and ground truth. Writes to a temporary folder. | no |
| `dump_cell_qualitative.py` | Dumps one project and model cell next to the ground truth. | no |

## 4. Adversarial probe and idea 21 (24 May 2026)

| File | What it does | Pinned |
|---|---|---|
| `run_adversarial_probe.py` | Adversarial probe on the mastodon project. | no |
| `analyze_adversarial_probe.py` | Analysis of the probe. | no |
| `dump_adversarial_qual.py` | Per-story dump of the probe outputs. | no |
| `run_idea21_grounded_citation.py` | Idea 21: grounded-citation generation on the probe. Near-copy of the probe script. | no |
| `analyze_idea21_vs_baseline.py` | Compares idea 21 with the baseline. | no |
| `dump_idea21_qual.py` | Side-by-side dump of baseline, idea 15 and idea 21. | no |

## 5. Study 2

| File | What it does | Pinned |
|---|---|---|
| `run_study2.py` | Study 2 driver: four arms, frozen retrieval. | yes, study2 list |
| `analyze_study2.py` | Study 2 analysis: citation of fact-sheet identifiers. | yes, study2 list |
| `study2_names.py` | Finds code-like names in requirements and in the project sources. | no |
| `build_study2_review_sheet.py` | Blinded review sheet, version 1. | no |
| `build_study2_review_sheet_v2.py` | Blinded review sheet, version 2 (14 November 2026). | no |
| `check_review_sheet_v2.py` | Check of sheet v2 with the key. | no |
| `score_study2_review.py` | Scores the blinded review (key opened on 16 November 2026). | no |
| `run_study2_ai_raters.py` | Runs the two AI raters of the review (`thesis/analysis_lock.md`, section 7). | no |

## 6. Study 2b

| File | What it does | Pinned |
|---|---|---|
| `run_study2b.py` | Study 2b driver: documents and instructions, each on or off. Imports `run_study2`. | no |
| `analyze_study2b.py` | The 2 by 2 analysis. Imports `analyze_study2`. | no |
| `check_s2b_prompts.py` | Checks the prompt folders in `data/winning_prompts_s2b/`. | no |

## 7. Prompt lab, search test, outside product (16 November 2026)

| File | What it does | Pinned |
|---|---|---|
| `run_tuning.py` | Test runner for configurations on hosted models. | yes, tool freeze list |
| `search_freeze.py` | Stores the passages of the new search options (search test). | no |
| `xproject_retrieve.py` | Search step for an outside product. | no |
| `xproject_run.py` | Generations on hosted models for an outside product. | yes, tool freeze list |
| `xproject_search_freeze.py` | Stores the passages of the new search options for an outside product. | no |

## 8. Final test (17 November 2026)

| File | What it does | Pinned |
|---|---|---|
| `final_test_pick.py` | Step 1: picks the documentation sections for the new stories. | yes, tool freeze list |
| `final_test_check.py` | Step 1: checks and seals the drafts of the new stories. | no |
| `final_test_key_check.py` | Two outside AI raters check each answer key. Imports `run_study2_ai_raters`. | no |
| `final_test_set.py` | Fixes the stories of the run from the key check. | no |
| `final_test_search_freeze.py` | Word-search passages for the sealed stories. | no |
| `final_test_retrieve.py` | The old search for the sealed stories (arm OLD). Imports `run_tuning` and `xproject_retrieve`. | no |
| `final_test_run.py` | The generations. A thin entry point for `xproject_run.py`. | no |
| `final_test_measure.py` | Measure 1: name use (a script, no judge). | no |
| `final_test_judging.py` | Measure 2: blind judge batches and the summary of labels. | no |
| `final_test_names_check.py` | The author checks the code-like names, blind to the version. | no |
| `final_test_nodoc_round.py` | Added round, planned after the result: judge labels for the run with no documentation. | no |

## Shell files and `repo_targets.txt`

None of these is pinned. Most are runners of the early periods.

| File | What it does |
|---|---|
| `setup-venv.sh` | Makes the virtual environment with `PYTHONPATH` set. |
| `fix-pth-hidden.sh` | Removes the macOS hidden flag from `.pth` files. |
| `heartbeat_poller.sh` | Prints a status line every 60 seconds for a cross-model pilot. |
| `per_project_pilot.sh` | Ingests one project and runs a pilot. |
| `run_full_eval_v3.sh` | Builds datasets from a list of repositories, then runs a pilot on each. |
| `run_thesis_n20.sh` | Path A driver: N=20 paired evaluation of the 10 hand-curated projects. |
| `run_thesis_n20_qwen36.sh` | Marked DEPRECATED in its header. Old qwen3.6 sub-test driver. |
| `run_cross_model_pilot.sh` | Model-agnostic cross-model pilot driver. |
| `run_cross_family_n10.sh` | Cross-family sub-test wrapper. |
| `run_all_ablations.sh` | Runs the ablation phases one after the other. |
| `repo_targets.txt` | List of target GitHub repositories for `bootstrap_dataset.py`. |

## `tuning_lib/`

The modules support the scripts of periods 7 and 8. The folder also holds design notes (`DESIGN.md`, `DESIGN_LAB.md`, `DESIGN_XPROJECT.md`), `splits.json` and `prompt_versions/` (28 prompt versions, each with 4 prompt files and a `version.json`).

| File | What it does | Pinned |
|---|---|---|
| `__init__.py` | Package docstring. | no |
| `paths.py` | Fixed paths and constants. | yes, tool freeze list |
| `guard.py` | Write rules, run lock, hash lists and privacy check. | yes, tool freeze list |
| `records.py` | Record and manifest schema. | yes, tool freeze list |
| `parse.py` | Reads and validates model answers. Failures are counted. | yes, tool freeze list |
| `passages.py` | Reads stored prompts and passages. | yes, tool freeze list |
| `measures.py` | The name-use measure. | yes, tool freeze list |
| `lab_measures.py` | Two script measures with one fixed reference. | yes, tool freeze list |
| `prompts.py` | Prompt versions, prompt builder and generality check. | yes, tool freeze list |
| `openrouter_client.py` | Client for hosted models through OpenRouter. | yes, tool freeze list |
| `search.py` | The new search step (BM25 over whole pages). | yes, tool freeze list |
| `xproject.py` | Shared code for outside-product tests. | yes, tool freeze list |
| `judging.py` | Blind batch builder and scorer. | no |
| `pairstats.py` | Paired summary. | no |
| `final_test.py` | Sealed stories, run set and the final-test builder. | no |

## Pinned files

A hash list holds one SHA-256 hash for each file. The lists are in `thesis/evidence/` of the root repository. Path forms inside the lists differ. The study2 list writes `coded_project/secreqgen/scripts/...`. The tool freeze list writes `scripts/...`.

| List | Pinned files in this folder |
|---|---|
| `study2_registered_hashes_2026-11-14.sha256` | `run_study2.py`, `analyze_study2.py` |
| `final_test_tool_freeze_2026-11-16.sha256` | `run_tuning.py`, `final_test_pick.py`, `xproject_run.py`, 11 modules of `tuning_lib/` (all in the table above marked "tool freeze list"), 12 prompt files in 3 prompt versions (`v0_old`, `s1_word_3000`, `v3_pn_render_fact_hits`). The same list also pins 5 files in `src/secreqgen/ingestion/`. |

The other lists in that folder pin data, not scripts. Files marked "no" are not pinned, but the rule "do not edit" applies to them as well.

## How the scripts depend on each other

The scripts are not a package. They import each other by file name, so each script needs this folder to be on `sys.path`. Python does this when you run a script from here. Keep the folder name and the file names as they are.

| Script | Imports |
|---|---|
| `bootstrap_dataset.py` | `asvs_templates` |
| `run_study2.py`, `run_adversarial_probe.py`, `run_idea21_grounded_citation.py` | `run_cross_family_winning_config` |
| `run_study2b.py` | `run_study2` |
| `analyze_study2b.py` | `analyze_study2` |
| `build_study2_review_sheet_v2.py`, `final_test_names_check.py` | `study2_names` |
| `final_test_key_check.py` | `run_study2_ai_raters` |
| `final_test_retrieve.py` | `run_tuning`, `xproject_retrieve`, `run_cross_family_winning_config` |
| `final_test_run.py` | `xproject_run` |
| `xproject_run.py` | `run_tuning` |
| `xproject_retrieve.py` | `run_tuning`, `run_cross_family_winning_config` |
| `rag_ablation_judge.py` | `judge_retrieval_quality` |

Sixteen scripts of periods 7 and 8 import `tuning_lib`: `run_tuning.py`, `search_freeze.py`, `xproject_retrieve.py`, `xproject_run.py`, `xproject_search_freeze.py` and the 11 `final_test_*.py` files.

Not stated: other script-to-script imports may exist. This page lists only those found in the checked files.

Some scripts write to the shared vector store `data/vectordb`. For example `run_cross_family_winning_config.py` and the four `rag_ablation_*` scripts call `vectorstore.clear()`. Never run two such scripts at the same time.

## What the scripts import from the package

These modules of `secreqgen` are imported by the scripts (found with a search for `from secreqgen` in this folder):

- `secreqgen.config`
- `secreqgen.evaluation`
- `secreqgen.exceptions`
- `secreqgen.generation.generator`
- `secreqgen.generation.grounded_citation`
- `secreqgen.generation.llm_client`
- `secreqgen.generation.prompts`
- `secreqgen.generation.schemas`
- `secreqgen.ingestion` (package root, and `.chunker`, `.loader`, `.metadata`)
- `secreqgen.retrieval` (package root, and `.reranker`, `.retriever`, `.vectorstore`)

No script imports `secreqgen.cli`, `secreqgen.threats`, `secreqgen.output`, `secreqgen.verification` or `secreqgen.logging`.

**These module paths and the names the scripts take from them must stay importable.** Some scripts also set attributes on package objects, for example `prompt_builder` of the generators. Do not rename these.

## Scripts that run only on the author's machine

These files hold a string that starts with the home folder of the author's machine. Most of them use it as a fixed folder for data or for the thesis root. They run only there, or after you edit the path. This page does not print the path.

Python files with a fixed folder path (28):

`analyze_adversarial_probe.py`, `analyze_idea21_vs_baseline.py`, `analyze_study2.py`, `build_study2_review_sheet.py`, `check_review_sheet_v2.py`, `cross_model_comparison.py`, `dump_adversarial_qual.py`, `dump_all_content_for_claude.py`, `dump_cell_qualitative.py`, `dump_idea21_qual.py`, `generate_figures.py`, `generate_judge_calibration_sample.py`, `generate_powerpoint.py`, `inspect_cross_family_results.py`, `judge_content_quality.py`, `meta_analysis.py`, `rag_ablation_agents.py`, `rag_ablation_generation.py`, `rag_ablation_llm_query.py`, `rag_ablation_study.py`, `read_everything.py`, `run_adversarial_probe.py`, `run_cross_family_winning_config.py`, `run_idea21_grounded_citation.py`, `run_study2.py`, `score_judge_calibration.py`, `study2_names.py`, `synthesize_pilot_v2.py`.

Shell files with a fixed folder path (7): `per_project_pilot.sh`, `run_all_ablations.sh`, `run_cross_family_n10.sh`, `run_cross_model_pilot.sh`, `run_full_eval_v3.sh`, `run_thesis_n20.sh`, `run_thesis_n20_qwen36.sh`.

Two more files contain the string, but as a privacy pattern and not as a folder: `run_study2_ai_raters.py` and `tuning_lib/guard.py`. `tuning_lib/paths.py` holds no home path.

The four scripts that define `THESIS_ROOT` are `run_cross_family_winning_config.py`, `run_study2.py`, `run_idea21_grounded_citation.py` and `run_adversarial_probe.py`. Because the others import `run_cross_family_winning_config`, the chain of `run_study2b.py` also ends in a fixed path.
