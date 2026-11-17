# Documents index

This folder holds the documents of the SecReqGen project. Use this page to find the documents that state the current facts.

On 19 May 2026 an audit found a fault. Every search in every experiment up to that date returned no documentation text. A result written before that date does not measure the effect of the documentation. Do not cite such a result.

The thesis record of November 2026 (Study 2, Study 2b and the final test) is not in this folder. It is in the thesis folder beside this repository (the analysis lock and the evidence files).

## Read these first

| Document | What it holds | Date |
|---|---|---|
| [HANDOFF.md](HANDOFF.md) | The handoff. Its own text says: read the top section first, because the lower parts can be out of date. The top block is "the final week started". Below it are the notes of 3 November 2026 (Study 2 done), of 25 October 2026, and the section "CURRENT STATE" (25 May 2026). The sections after that are history. | Top block 14 November 2026 |
| [RAG_ABLATION_LAB_NOTEBOOK.md](RAG_ABLATION_LAB_NOTEBOOK.md) | The lab notebook. It records approaches, actions, problems and results of the experiments done after the audit. Sections 9 to 12 cover the cross-family run, the manual read, the adversarial probe and idea 21. Section 13 is the council audit and repairs (25 October 2026). Sections 14 and 15 are the plan and the results of Study 2 (2 November 2026). Section 16 is the plan of Study 2b (15 November 2026). | Started 19 May 2026. Newest entry 16 November 2026 (a dated line in section 16) |
| [RAG_RETRIEVAL_AUDIT_2026-05-19.md](RAG_RETRIEVAL_AUDIT_2026-05-19.md) | The postmortem of the audit. It states that all 1,278 RAG queries of the 16-cell matrix returned the text "No relevant security documentation found." It also gives the repair plan. | 19 May 2026 |

New experiment reports go in a new section of the lab notebook. Update the current-state part of HANDOFF.md at the same time.

Some HANDOFF.md sections tell the reader to wait for the author's review labels. Follow that rule.

## History, kept for the record

All rows below are kept as a record. Do not use them as a source of current facts.

### Results written before the audit

Do not cite these for results.

| Document | What it is | Date or period | Why it is history |
|---|---|---|---|
| [thesis_status_report.md](thesis_status_report.md) | Status report with the story of the evaluation work and the N=20 results. | 4 May 2026, updated 5 May 2026 | Before the audit. HANDOFF.md replaced it. |
| [meta_n20_thesis.md](meta_n20_thesis.md) | Cross-project table of the N=20 run (per-project paired test). | Not stated in the file. The N=20 run finished on 5 May 2026 (see sub_test_qwen36.md). | Before the audit. |
| [meta_v2_baseline.md](meta_v2_baseline.md) | Cross-project table of the pilot v2 run. | Not stated | Before the audit. The status report places pilot v2 before the N=20 run of 5 May 2026. |
| [sub_test_qwen36.md](sub_test_qwen36.md) | Plan and reason for the test with the model qwen3.6. | 7 May 2026 | Before the audit. |
| [sub_test_qwen36_results.md](sub_test_qwen36_results.md) | Results of the qwen3.6 test, compared with the main model. | Not stated. It follows the test of 7 May 2026. | Before the audit. |
| [figures/](figures/) | Seven charts (fig1 to fig7: baseline against RAG, effect sizes, knowledge base size, Keycloak runs, timeline, N=20 results). | Not stated | The status report and the deck use them. Both are from before the audit. |
| [presentation.md](presentation.md) | Slide deck for the supervisor, in Marp format. | 4 May 2026 | Before the audit. |
| [SecReqGen_Status.pptx](SecReqGen_Status.pptx) | A PowerPoint status deck. | The deck is dated 5 May 2026 | Before the audit. The slides carry the N=20 status of 5 May 2026. |

### Reads and research after the audit

These files are dated after the audit, or HANDOFF.md lists them in its post-audit sections (the two judge files). HANDOFF.md says the worksheet calibrates the retrieval judge only. These files are evidence behind the lab notebook. They are not instructions.

| Document | What it is | Date |
|---|---|---|
| [CROSS_FAMILY_DEEP_READ_2026-05-22.md](CROSS_FAMILY_DEEP_READ_2026-05-22.md) | A read of all generated requirements of the 20 cells. | 22 May 2026 |
| [CROSS_FAMILY_QUALITATIVE_READ_2026-05-22.md](CROSS_FAMILY_QUALITATIVE_READ_2026-05-22.md) | A manual look at the generated requirements. It states that the F1 score does not measure requirement quality. | 22 May 2026 |
| [CROSS_FAMILY_CONTENT_QUALITY_2026-05-23.md](CROSS_FAMILY_CONTENT_QUALITY_2026-05-23.md) | Scores of the judge llama3:8b for the 20 cells (1,636 judge calls). | 23 May 2026 |
| [CLAUDE_PERSONAL_REVIEW_2026-05-23.md](CLAUDE_PERSONAL_REVIEW_2026-05-23.md) | A personal review of the generated requirements by the AI assistant. It was not scored by a judge model. | 23 May 2026 |
| [RAG_IMPROVEMENT_RESEARCH_2026-05-24.md](RAG_IMPROVEMENT_RESEARCH_2026-05-24.md) | Research for ideas to improve retrieval. | 24 May 2026 |
| [judge_calibration_sample.md](judge_calibration_sample.md) | Worksheet of 30 (story, passage) pairs for hand labels. The reader must not open the .json file before labelling. | Not stated. HANDOFF.md lists it as pending in its May 2026 sections. |
| [judge_calibration_sample.json](judge_calibration_sample.json) | The 30 pairs with the verdicts of the language model judge. Do not open it before you label the worksheet. | Not stated. Same as the worksheet. |

### Plans, design notes and tools of the early project

| Document | What it is | Date or period | Why it is history |
|---|---|---|---|
| [log.md](log.md) | The implementation log. | Last update 1 January 2026 | It ended in January 2026. See the note below. |
| [phases/](phases/) | Five phase plans: foundation, ingestion, generation, evaluation, optimization (phase-1 to phase-5). | Not stated | Plans from the start of the project. |
| [proposals/](proposals/) | An index and four design notes (the compare-runs command, the wording of goal 6, next steps, reproducibility). The four notes are "rev. 2" after a council review. | 3 May 2026 | Before the audit. |
| [vultr_migration.md](vultr_migration.md) | Plan to move the work to a cloud server. It is paused at the GPU step. | 17 May 2026 | Before the audit. It is an operational note. |
| [reference.md](reference.md) | A quick reference of code patterns (for example the Pydantic schema pattern). | Not stated | Not checked against the current code. |
| [coded_project.code-workspace](coded_project.code-workspace) | A VS Code workspace file. It lists this repository and a second folder. | Not stated | A local editor setting, not a document. |

## About docs/log.md

The last real entry of docs/log.md is from January 2026. Do not add entries to it. It does not agree with later results. The file CLAUDE.md in the repository root says the same. Three of the four commands in `.claude/commands/` (log, status, step) still name docs/log.md. They are older than the audit. Do not follow them.
