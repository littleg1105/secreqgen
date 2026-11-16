# The prompt lab: design

Status: design, 2026-11-16. Three builders work from this file at the same time, each in its own files. Each interface below is exact: names, argument names, types, error classes and file formats. If a builder needs a change, the builder tells the main session. The builder does not change an interface alone.

Read `scripts/tuning_lib/DESIGN.md` first. This file adds to it and never repeats it. Same rules as there: all paths are relative to the thesis root unless they start with `scripts/` or `tests/` (those start in `coded_project/secreqgen/`); no absolute path and no account name in any file that a builder writes; every write goes through the guard; no silent fallback; every module is importable with no side effect and no network; Python 3.13, standard library plus what the environment has; type hints; short docstrings.

## 0. Summary

The author wants a loop: take 10 stories where the tool fails, change the prompt, run old against new, judge, repeat, and take care not to overfit. Five other stories stay apart and are used one time at the end.

The lab has four parts.

1. A prompt builder that rebuilds the full prompt of the old tool (arm B2 of Study 2b) byte for byte, from a template folder and the stored passages (`tuning_lib/prompts.py`). A new prompt version is a copy of the template folder with edits, plus an optional list of passage changes.
2. Prompt versions as folders, with a generality check that refuses a version that holds a product word, a fact-sheet identifier, a story id or a long phrase of a story (same file).
3. A real `run` subcommand (`scripts/run_tuning.py`): several versions on a list of stories, both hosted models, several repeats, versions mixed in one session. A report with script measures and "new against old" for each story (`tuning_lib/lab_report.py`, `tuning_lib/lab_measures.py`).
4. Helpers for blind judging (`tuning_lib/judging.py`): a batch builder (versions mixed, neutral ids, private key) and a scorer.

Facts that I checked on 2026-11-16 (read only, with scratch scripts, no network, no model):

- The old prompt can be rebuilt exactly. With `SecurityRequirementsPromptBuilder(prompts_dir=data/winning_prompts_s2b/rep)`, the story from `study2/<project>/stories.json`, the system context from `real_test_cases/<project>/system_context.yaml` (`Path.read_text()`), and the stored passage block from `study2b/<project>/<model slug>/retrieval_frozen.json`, `build_system_prompt()` and `build_rag_prompt(...)` gave the stored `prompt_system` and `prompt_user_sent` of the B2 record in all 30 cases (15 stories, 2 models): 0 differences.
- Importing `secreqgen.generation.prompts` and `secreqgen.generation.schemas` has no side effect and no heavy import (the package `__init__` loads `generator`, `llm_client`, `httpx` and `pydantic` only). `parse.py` already does the same.
- The 30 stored contexts split with `passages.split_context`, and `SEPARATOR.join(p.text for p in passages)` gives the context back exactly. The code-search hits are mixed between the documentation passages (for example `DcDcDcDD`), so a passage change that removes them must number the passages again.
- `SecurityRequirementsPromptBuilder._load_template` falls back to a built-in default template when a file is missing. This is a silent fallback. `load_version` therefore checks that all three files exist.
- The three `v0_old` template files hold no product name. They hold one fact-sheet identifier of one story (`strict`, of `KC-S2-005`). This is the case "words which v0_old holds already are reported but do not fail".
- The anchor run (120 generations, mixed A2 and B2) cost 0.365 USD and took 658k prompt and 252k answer tokens. A lab run of 10 stories x 2 models x 2 versions x 3 repeats is also 120 generations, all B2-sized, so expect about 0.4 to 0.5 USD.
- The record schema (`records.py`) is closed: an unknown field raises. The manifest has a fixed key list that already uses the key `versions` for Python versions. The lab needs a small extension of the record schema (section 6.1) and must keep its prompt versions in other keys (section 5.5).

## 1. Files, owners and build order

Already made by this design step (the builders must not change them):

| File | Content |
|---|---|
| `scripts/tuning_lib/splits.json` | keys `dev` (10 ids), `holdout` (5 ids), `rule`; a copy of the scratch split file without the statistics |
| `scripts/tuning_lib/prompt_versions/v0_old/system_prompt.txt`, `rag_user_prompt.txt`, `zero_shot_user_prompt.txt` | byte copies of `data/winning_prompts_s2b/rep/` (SHA-256 checked, section 2.2) |
| `scripts/tuning_lib/prompt_versions/v0_old/version.json` | see section 2.2 |

| Builder | Files (new, unless marked) | Test files |
|---|---|---|
| A | `scripts/tuning_lib/prompts.py`, `scripts/tuning_lib/lab_measures.py`, `scripts/tuning_lib/pairstats.py` | `tests/unit/test_tuning_prompts.py`, `tests/unit/test_tuning_lab_measures.py`, `tests/unit/test_tuning_pairstats.py` |
| B | `scripts/run_tuning.py` (extend), `scripts/tuning_lib/records.py` (extend), `scripts/tuning_lib/splits.py`, `scripts/tuning_lib/lab_report.py` | `tests/unit/test_tuning_lab_run.py`, `tests/unit/test_tuning_lab_report.py`, `tests/unit/test_tuning_splits.py`; edits in `tests/unit/test_tuning_runner.py` (the stub test) and `tests/unit/test_tuning_records.py` (new cases) |
| C | `scripts/tuning_lib/judging.py` | `tests/unit/test_tuning_judging.py` |

Dependency direction (an arrow means "imports"). No cycle.

- `lab_measures` to `prompts`, `passages`.
- `prompts` to `passages`, `paths`, `guard`, `measures` (only `load_fact_sheets`), `secreqgen.generation.prompts`, `secreqgen.generation.schemas`.
- `splits` to `prompts` (only `load_stories`), `paths`.
- `lab_report` to `lab_measures`, `prompts`, `pairstats`, `measures`, `records`, `passages`, `paths`.
- `run_tuning` to everything above, and to what it imports today.
- `judging` to `guard`, `records`, `pairstats`, `paths`. It does not import `run_tuning`, `prompts` or `lab_measures`.
- `pairstats` to the standard library only.

Builder A writes `pairstats.py` first (it is 30 lines), so that B and C can use it at once. All three builders code against the signatures in this file from the first minute.

Changing existing files: B extends `run_tuning.py` and `records.py`. The behaviour of `anchor` must not change: the existing tests of `anchor` (`test_tuning_runner.py`, `test_tuning_records.py`) stay green with the one exception named in 5.1 (the stub test). No other existing file changes.

## 2. prompts.py (builder A)

### 2.1 Constants, errors, guard

```python
VERSIONS_ROOT: Path = SCRIPTS / "tuning_lib" / "prompt_versions"
TEMPLATE_FILES: tuple[str, ...] = ("system_prompt.txt", "rag_user_prompt.txt", "zero_shot_user_prompt.txt")
VERSION_FILE: str = "version.json"
BASELINE: str = "v0_old"
VERSION_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
KNOWN_PLACEHOLDERS: tuple[str, ...] = (
    "story_id", "story_title", "story_text", "acceptance_criteria",
    "system_context", "retrieved_context", "json_schema",
)
PRODUCT_WORDS: tuple[str, ...] = ("keycloak", "bitwarden", "mastodon", "mattermost", "gitlab")
PHRASE_WORDS: int = 5            # length of a story phrase for the generality check

class PromptError(Exception): ...                    # base class; the message says which file or which story
class PromptMismatch(PromptError): ...               # a rebuilt prompt differs from the stored one
class NoDocumentationAfterChange(PromptError): ...   # zero documentation passages after the passage changes
class GeneralityError(PromptError): ...              # a version has new generality hits

VERSIONS_GUARD: WriteGuard   # WriteGuard(allowed_roots=(VERSIONS_ROOT,), forbidden_roots=(STUDY2, STUDY2B, DATA), forbidden_names=("study2", "study2b")), built at import time from constants only
```

`new_version` is the only function of the module that writes. It uses `guarded_mkdir` and `guarded_write_text` with `guard=VERSIONS_GUARD` (the default guard allows `tuning/` only). No other write.

### 2.2 A version folder

`scripts/tuning_lib/prompt_versions/<name>/` holds exactly: the three files of `TEMPLATE_FILES` and `version.json`. Nothing else (an extra file is an error: it could hide text that nobody checks).

`version.json` has exactly these keys (an extra or missing key is an error):

| Key | Type | Rule |
|---|---|---|
| `name` | str | equals the folder name; matches `VERSION_NAME_RE` |
| `parent` | str or null | null only for `v0_old`; else the name of an existing version folder |
| `description` | str | one sentence that says what changed; 1 to 300 characters; one line; not empty after strip |
| `passage_changes` | list of str | names in the registry of 2.4, applied in this order; may be empty |

`v0_old` (already made): `{"name": "v0_old", "parent": null, "description": "The old tool: a byte copy of data/winning_prompts_s2b/rep (arm B2 of Study 2b), with no passage change.", "passage_changes": []}`. Its three template files must stay byte equal to `data/winning_prompts_s2b/rep/`. SHA-256 (checked on 2026-11-16, copy and source equal):

```
bab4639429934b7fe8844aa94075f1d4edabb82e51a842415e2dae6f50e9ffc0  rag_user_prompt.txt
814750d9ceeb8221622a8d079c3509212e696d78fd0fdae7697802f7428bdfca  system_prompt.txt
72f9ea78e4087092c399aad662be64ac1ee51c1458c0035f08450a72edcdb3f4  zero_shot_user_prompt.txt
```

Template rules, checked by `load_version` (a violation raises `PromptError` with the file name):

1. All three files exist, are UTF-8, and hold no carriage return. (`Template.safe_substitute` and `read_text` would hide a difference.)
2. In `rag_user_prompt.txt` and `zero_shot_user_prompt.txt`, find every `$` with the regex `\$(?:\{)?([A-Za-z_][A-Za-z0-9_]*)\}?`. Each name must be in `KNOWN_PLACEHOLDERS`. The number of `$` characters must equal the number of matches (so `$5` and `$$` are refused: `safe_substitute` would leave them or turn them into text without a warning).
3. In `rag_user_prompt.txt`: `retrieved_context` exactly one time; every other known placeholder at least one time. In `zero_shot_user_prompt.txt`: `retrieved_context` zero times; the other six at least one time. (The lab runs the RAG prompt only. The zero-shot file is kept so that a version is a full copy of the old folder, and the generality check reads it.)
4. `system_prompt.txt` has no placeholder (the builder sends it as it is). A `$` there is plain text and is allowed.

### 2.3 Loading, listing, creating versions

```python
@dataclass(frozen=True)
class VersionInfo:
    name: str
    parent: str | None
    description: str
    passage_changes: tuple[str, ...]
    folder: Path
    template_sha256: Mapping[str, str]     # file name -> SHA-256 of the bytes, keys in TEMPLATE_FILES order
    version_json_sha256: str

def list_versions(root: Path = VERSIONS_ROOT) -> list[str]: ...                 # sorted names of folders that hold a version.json
def load_version(name: str, root: Path = VERSIONS_ROOT) -> VersionInfo: ...     # checks 2.2; also: the parent exists; no parent loop (follow at most 20 parents)
def new_version(name: str, parent: str, description: str, *, root: Path = VERSIONS_ROOT,
                guard: WriteGuard = VERSIONS_GUARD) -> Path: ...
```

`new_version` creates `root/<name>/`: the three template files as exact bytes of the parent folder, and a `version.json` with `parent` set, the given `description`, and `passage_changes` copied from the parent (the main session can edit it by hand). It raises `PromptError` when the name is bad, the folder exists, the parent does not exist, or the description is bad. It returns the folder. A test checks that the three copies are byte equal to the parent (the guard function writes text; if the builder finds that `guarded_write_text` cannot write a file byte for byte, for example because of newline translation, the builder tells the main session at once, see section 8, point 5).

`load_version` is pure reading. It has no cache that a test cannot clear (use a plain dict inside `PromptBuilder`, section 2.6).

### 2.4 Passage changes

```python
PassageChange = Callable[[str], str]          # pure function: stored passage block -> new block
PASSAGE_CHANGES: dict[str, PassageChange]     # the registry

def apply_passage_changes(block: str, names: Sequence[str]) -> str: ...
```

Registry, first two entries:

- `"identity"`: returns the block unchanged (the same object).
- `"drop_code_hits"`: `passages = split_context(block)`. Keep the documentation passages (`kind == "doc"`) in their order. Number them again from 1: only the digits inside `[n]` of the first line of each passage change (the first line matches `^### \[(\d+)\] `; replace the number with the new one). Every other byte of a documentation passage stays. Join with `SEPARATOR`. If no documentation passage is left, raise `NoDocumentationAfterChange`. If the block has no code hit, the result equals the input block (the renumbering is then a no-op).

`apply_passage_changes` applies the names in order. An unknown name raises `PromptError` that lists the known names. After the last change it calls `split_context` on the result (so a change cannot produce a broken block) and requires at least one documentation passage, else `NoDocumentationAfterChange`. It never returns the 41-character fallback text.

New changes are added by adding a function and a registry entry. A change is a function of the block and an optional context (see 2.4a). A change that does not need the context ignores it. No change may use the model or the network. Only `story_name_hits` reads files (2.4a).

### 2.4a Passage context, `story_name_hits`, and the product rule of `render_markup`

`PassageContext(project, story_text)` is a frozen dataclass. `story_text` is the title, the story sentence and the acceptance criteria as the prompt shows them (`Story.prompt_text()`). A change has the form `change(block, context=None)`. `apply_passage_changes(block, names, context=None)` gives the context to each change. `PromptBuilder.build` always gives it. A change that needs the context raises `PromptError` when it is `None`. `run_tuning.py` builds prompts only through `PromptBuilder`, so it needed no change. Existing versions give the same bytes (the dry run check "v0_old rebuild" proves it for `v0_old`).

`story_name_hits` keeps each documentation passage. It keeps a code hit only when all of these are true:
1. The search pattern is a literal code-like name, or a list of such names split at `|`. A name is code-like when it matches `^[A-Za-z0-9_.:/-]+$`, has 4 characters or more and 4 letters (`[A-Za-z]`) or more, and has a separator between two letters or digits, or a lower-case letter before an upper-case one, or only upper-case letters, digits and underscores. If one part is not a code-like name (parentheses, `.*`, a backslash, a square bracket, a plain word, an empty part), the pattern is "not literal" and the hit is dropped.
2. The story text holds at least one of the names. The story text is cut into tokens (a token is a longest run of `[A-Za-z0-9_.:/-]`, with `. : / -` removed from its start and end). The story holds a name only when a token is exactly equal to the name. So `A_B.c` is not held by `A_B.c.d`, `MY_NAME` is not held by `pre-MY_NAME`, but `MY_NAME` is held by `MY_NAME.` at the end of a sentence, by `` `MY_NAME` `` and by `MY_NAME=true`.
3. The file is not a test file. A file is a test file when (a) a folder of its path, in lower case, is `test`, `tests`, `spec`, `specs`, `__tests__`, `testsuite`, `testing`, `e2e`, `cypress`, `fixtures`, `mocks` or `__mocks__`, or starts with `test-`, `test_`, `tests-` or `tests_`, or ends with `-test`, `_test`, `-tests` or `_tests`; or (b) the file name without its extension ends with `Test`, `Tests`, `Spec` or `Specs` (this case exactly), or with `IT` after a lower-case letter (`LoginIT` is a test file, `SUBMIT` is not), or ends with `_test`, `_tests`, `_spec`, `.test`, `.spec`, `-test` or `-spec` (lower case), or starts with `test_`, or is `test` or `spec`.
4. The file is in the repository folder of the product (`REPOS_ROOT`, `REPO_DIR_OVERRIDE` in `paths.py`; bitwarden is `bitwarden-server`) and the stored line equals that line of the file after `strip()`. A path that leaves the folder, an absolute path and a link that leaves the folder are refused. Reason when it fails: "not_found".
5. The stored hit line holds, as a substring (case-sensitive), at least one name that rule 2 found in the story. Reason when it fails: "name_not_in_line".

The drop reasons are `not_literal`, `not_in_story`, `test_file`, `not_found`, `name_not_in_line` and `privacy` (checked in this order).

A kept hit shows lines 1 to 3 as stored, then one note line ("Source code, not documentation. Lines A to B of the file. The search found line N."), an optional line "Line M starts the enclosing block: ...", and a fenced block with the numbered lines (3 lines before and after). A line of more than 240 characters is cut after 240 characters and ends with " ..." (the opening line too). A carriage return at the end of a line is removed.

The opening line is found level by level. Start with level = the indentation of the hit line and position = the hit line. Go up from the position (400 lines at most in total) to the nearest line that is not blank, is not only a comment (after `strip()` it starts with `#`, `//`, `*` or `/*`) and has less indentation than the level. If there is none, there is no opening line. If that line starts with a keyword (`def`, `class`, `module`, `function`, `public`, `private`, `protected`, `internal`, `static`, `async`, `override`, `export`, `fun`, `func`, `sub`), it is the opening line. If not, its indentation becomes the level, it becomes the position, and the walk repeats. The opening line is shown only when it is above line A. So a block that ended before the hit gives no opening line.

A hit whose new lines hold a private marker (the privacy guard of `guard.py`, not weakened) is dropped. Passages are numbered again from 1. With no hit kept the result equals `drop_code_hits`. The function is deterministic, writes nothing and uses no network. `classify_code_hits(block, context)` gives the reason of each dropped hit (for reports).

`render_markup` acts only for `context.project == "keycloak"` (its rules come from the Keycloak documentation toolchain). With another product the block is returned unchanged. With no context it acts as before. It renders `{project_name}` in the numbered header line of a passage as well as in the body (the header still matches the header pattern of `split_context`, with the same number and relevance). It renders `opts.printRelevantOptions` as the text that a reader of the published page sees, as one plain line: "Relevant options: <names of includedOptions that are not in excludedOptions, in their order>". The names of excludedOptions and of deniedCategories are not shown. If no name is left, the line is "(The published page shows a table of the relevant options here.)". Single and double quotes are accepted for the attribute values. The closing tag line is dropped.

### 2.5 Stories, system context, stored block

```python
@dataclass(frozen=True)
class Story:
    story_id: str
    project: str
    title: str
    story: str
    acceptance_criteria: tuple[str, ...]
    identifiers: tuple[str, ...]          # fact_sheet.identifiers (used by the generality check only)

def load_stories(projects: Sequence[str] = PROJECTS, study2_root: Path = STUDY2) -> dict[str, Story]: ...
```

`load_stories` reads `study2_root/<project>/stories.json` (read only). A duplicate id, a missing key, or an empty list of identifiers raises `PromptError`. For the real data it returns 15 stories. The keys used are `id`, `title`, `story`, `acceptance_criteria`, `fact_sheet.identifiers`.

The system context of a project is read with the same call as `run_study2b.py:515-516`: `RTC / project / "system_context.yaml"`, `Path.read_text(encoding="utf-8")`. The old code used `None` when the file is missing, and the template then says "No system context provided.". Here a missing file raises `PromptError` (all three projects have it; a missing file would change the prompt without a warning).

The stored passage block is `passages.read_study2b_context(project, model, story_id).text`. That function checks the stored SHA-256. It is the only source of passages. The model argument is the local name (`qwen3.8:27b` or `gemma4:26b`, `paths.MODELS`).

### 2.6 The builder

```python
@dataclass(frozen=True)
class BuiltPrompt:
    story_id: str
    project: str
    model: str
    version: str
    system: str                    # exactly what is sent as system prompt
    user: str                      # exactly what is sent as user prompt
    user_non_doc: str              # user with the block removed (same rule as the registered records)
    block_original: str            # the stored block
    block: str                     # the block that went into the prompt (after the passage changes)
    block_sha256: str              # SHA-256 of `block`
    n_doc_passages: int            # in `block`
    n_code_hits: int               # in `block`
    info: VersionInfo

class PromptBuilder:
    def __init__(self, versions_root: Path = VERSIONS_ROOT, study2_root: Path = STUDY2, rtc_root: Path = RTC) -> None: ...
    def stories(self) -> dict[str, Story]: ...                       # loaded once, kept in the object
    def story(self, story_id: str) -> Story: ...                     # unknown id raises PromptError
    def system_context(self, project: str) -> str: ...
    def version(self, name: str) -> VersionInfo: ...                 # load_version, kept in the object
    def stored_block(self, story_id: str, model: str) -> str: ...    # read_study2b_context(...).text, kept in the object
    def build(self, story_id: str, model: str, version: str) -> BuiltPrompt: ...
    def verify_v0_old(self, story_id: str, model: str) -> None: ...  # raises PromptMismatch

def build_prompt(story_id: str, model: str, version: str) -> BuiltPrompt: ...      # PromptBuilder().build(...) with a module-level default builder
```

The class is the unit. The module function `build_prompt` is a thin wrapper that keeps one default `PromptBuilder` (made at first call, not at import). The spec of the author says "build_prompt(story id, model, version) returns the system prompt and the user prompt": the return value has `system` and `user` among its fields.

`build` does, in this order:

1. `info = version(name)`. `story = story(story_id)`.
2. `block_original = stored_block(story_id, model)`.
3. `block = apply_passage_changes(block_original, info.passage_changes)`.
4. `pb = SecurityRequirementsPromptBuilder(prompts_dir=info.folder)` (imported from `secreqgen.generation.prompts`; this is the registered code, so the substitution cannot drift). `system = pb.build_system_prompt()`.
5. `user = pb.build_rag_prompt(user_story=UserStory(id=..., title=..., story=..., acceptance_criteria=list(...)), retrieved_context=block, system_context=self.system_context(story.project))`. `UserStory` comes from `secreqgen.generation.schemas`. This is exactly the call of `expected_prompts` in `run_study2b.py:417-428` and of the registered generator.
6. The block must occur exactly one time in `user`, else `PromptError`. `user_non_doc = user.replace(block, "", 1)`. It must equal `pb.build_rag_prompt(..., retrieved_context="", ...)`, else `PromptError` (this is the check of `expected_prompts`; it also catches a template that puts the block in two places or changes it).
7. Count passages with `split_context(block)`; fill `n_doc_passages`, `n_code_hits`.

`verify_v0_old(story_id, model)`: builds `v0_old`, finds the stored B2 prompt with `passages.read_study2b_prompts(projects=(project,), models=(model,), arms=("B2",), run=1, stories={story_id})`, and requires `built.system == stored.system` and `built.user == stored.user_sent` and `built.user_non_doc == stored.user_non_doc` and `built.block_sha256 == stored.doc_context_sha256`. A difference raises `PromptMismatch`. The message gives the story, the model, which of the four values differs, the first differing index, and the two lengths. It never prints the prompt text.

Nothing in this module calls a model, an embedder or the network, and nothing reads `data/vectordb`, `study2/review*`, `study2_threat` or `data/threat_prompts`.

### 2.7 The generality check

```python
@dataclass(frozen=True)
class GeneralityHit:
    kind: str            # "product", "fact_identifier", "story_id", "story_phrase"
    term: str            # the word, identifier, id or phrase (a phrase is the casefolded 5-word text)
    file: str            # one of TEMPLATE_FILES
    line: int            # first line (1-based) where it occurs in this file
    count: int           # number of occurrences in this file of this version
    baseline_count: int  # number of occurrences of the same (kind, term) in the same file of v0_old
    @property
    def inherited(self) -> bool: ...    # count <= baseline_count

@dataclass(frozen=True)
class GeneralityReport:
    version: str
    hits: tuple[GeneralityHit, ...]                 # all hits, inherited and new, sorted by (file, line, kind, term)
    @property
    def new_hits(self) -> tuple[GeneralityHit, ...]: ...
    @property
    def inherited_hits(self) -> tuple[GeneralityHit, ...]: ...
    @property
    def ok(self) -> bool: ...                        # no new hits

def check_generality(version: str, *, builder: PromptBuilder | None = None) -> GeneralityReport: ...
def assert_general(version: str, *, builder: PromptBuilder | None = None) -> GeneralityReport: ...   # raises GeneralityError when not ok; the message lists at most 10 new hits (kind, term, file:line)
```

What counts as a hit, in each of the three template files (the comparison file is always the same file of `v0_old`, not of the parent):

1. `product`: any word of `PRODUCT_WORDS`, case-insensitive, as a substring (so "Keycloak's" and "keycloakd" count).
2. `fact_identifier`: any identifier of the fact sheet of any of the 15 stories (`Story.identifiers`), found as a whole name: the character before is not `[A-Za-z0-9_]`, and the text after does not continue the name (`[A-Za-z0-9_-]` or a dot followed by `[A-Za-z0-9_]`). This is the rule of `whole_rx` of `study2_names.py` (copy the one-line regex; do not import). Case-sensitive. Plain words that are also identifiers (like `strict`) are hits too: the author decides, the check does not guess.
3. `story_id`: the regex `\b(?:BW|KC|MT)-(?:S2|ADV)-\d{3}\b`, and any literal id of the 15 stories.
4. `story_phrase`: any run of 5 consecutive words (`\w+`, casefolded) that also occurs in the title, the story text or an acceptance criterion of any of the 15 stories. This catches a sentence that was copied from a story.

A version is a general version when `new_hits` is empty. `v0_old` is general by definition (`baseline_count` equals `count`, so every hit is inherited; today it has the one inherited hit `fact_identifier "strict"`). The check is deterministic and reads only the version folders and `stories.json`.

The runner calls `assert_general` for every version of a run, also for `--dry-run`. A version with new hits stops the run with exit code 2 and nothing is sent.

### 2.8 Unit tests of prompts.py (`tests/unit/test_tuning_prompts.py`)

Read-only tests on the real registered data (like the other tuning tests). Tests that write use `tmp_path` and a `WriteGuard(allowed_roots=[tmp_path / "pv"], forbidden_roots=[], forbidden_names=[])`. No test uses the network, the key, Ollama, an embedder or ChromaDB.

1. `v0_old` equality, the acceptance test: for the 15 stories and both models, `PromptBuilder().build(story, model, "v0_old")` gives `system == stored.system` and `user == stored.user_sent` and `user_non_doc == stored.user_non_doc` (30 cases; the stored prompt comes from `read_study2b_prompts`). Also `verify_v0_old` returns `None` for the 30 cases.
2. Hash test: the three files of `v0_old` have the three SHA-256 values of 2.2, and are byte equal to `data/winning_prompts_s2b/rep/`.
3. A mismatch is loud: copy `v0_old` to a temporary root, change one character in `rag_user_prompt.txt` (keep the version name `v0_old`), build with that root, and `verify_v0_old` raises `PromptMismatch`. A changed system context (a temporary `rtc_root` with one different `system_context.yaml`) also raises it.
4. `load_version`: a missing template file, an extra file, a carriage return, an unknown placeholder, `$5`, `$$`, `retrieved_context` twice, `retrieved_context` zero times, a dropped `$system_context`, a bad name, `parent` of `v0_old` not null, a parent that does not exist, a parent loop, an empty or two-line description, an extra key or a missing key in `version.json`, an unknown passage change name: each raises `PromptError` (one test each, check the message names the file or the key). The silent default of `SecurityRequirementsPromptBuilder` must never be reached: a test with a version folder that lacks `system_prompt.txt` raises, it does not build.
5. Passage changes: `identity` returns the same string. `drop_code_hits` on all 30 stored blocks: the result holds only `kind == "doc"` passages; the number of passages equals the number of documentation passages of the input; the numbers in the headers are 1..n; for each kept passage, the text after the first line is byte equal to the original, and the first line differs only in the number; when the block has no code hit the output equals the input (find such a block among the 30, or build one); the result passes `split_context`. A block with only code hits raises `NoDocumentationAfterChange`. `apply_passage_changes(block, ["nope"])` raises `PromptError`. `apply_passage_changes(block, ["drop_code_hits", "identity"])` equals `["drop_code_hits"]`.
6. `build` with a version that has `passage_changes = ["drop_code_hits"]`: `block` is the changed block; `user.count(block) == 1`; `user_non_doc` equals the prompt built with an empty block; `n_code_hits == 0`; the `user` text differs from `v0_old` only inside the block (replace the two blocks by a mark and compare).
7. `build` with a template that puts `$retrieved_context` twice is refused by `load_version` (test 4), and a template edit that makes the removal check fail (simulate with a monkeypatched `build_rag_prompt`) raises `PromptError`.
8. `new_version`: creates the folder; the three files are byte equal to the parent; `version.json` has the four keys with `parent` set and `passage_changes` copied; a second call with the same name raises; a bad name, a missing parent and a bad description raise; the result loads with `load_version`; nothing is written outside the given root (the guard refuses a root outside `allowed_roots`).
9. Generality: `check_generality("v0_old")` has `ok` true, and holds the inherited hit `strict`. A temporary version made from `v0_old` plus one added sentence with each of: "Keycloak", "BITWARDEN", "mastodon" (case), an identifier of a story that is not in `v0_old` (take one from `load_stories`), a story id, and a 5-word phrase copied from a story: each gives exactly one new hit of the right kind and `assert_general` raises `GeneralityError`. A version that adds one more occurrence of `strict` has a new hit (count above baseline); a version that only keeps the one occurrence has none. A version with a general new rule (plain English, no hit) is `ok`.
10. Import safety: `import tuning_lib.prompts` in a fresh interpreter with `sys.modules` checked does not import `chromadb`, `sentence_transformers` or `secreqgen.retrieval`, and creates no file (compare a listing of the tuning folder before and after).
11. Source scan: `prompts.py` holds no write call (`write_text(`, `open(` with a write mode, `mkdir(`, `shutil`, `os.replace`) except through the `guarded_*` functions in `new_version`.

## 3. lab_measures.py and pairstats.py (builder A)

### 3.1 pairstats.py

```python
@dataclass(frozen=True)
class PairedSummary:
    n: int
    mean: float | None        # None when n == 0
    se: float | None          # sample standard deviation / sqrt(n); None when n < 2
    up: int                   # differences above +tol
    equal: int                # |difference| <= tol
    down: int                 # differences below -tol
    sign: str                 # "+", "-" or "0": the sign of the mean (with the same tolerance); "" when n == 0

def paired_summary(diffs: Sequence[float], *, tol: float = 1e-9) -> PairedSummary: ...
```

Use `statistics.fmean` and `statistics.stdev`. A non-finite value raises `ValueError`. No other function. All lab and judging comparisons use it, so "stories up, equal, down" means the same everywhere.

### 3.2 lab_measures.py: two new script measures with ONE fixed reference

The measures say, for a requirement, whether the code-like names that it uses are in the material that the OLD tool gave the model. The reference is the same for every version, so a version that changes the passage block (for example `drop_code_hits`) is not helped or hurt by its own block.

```python
class LabMeasureError(Exception): ...

@dataclass(frozen=True)
class Reference:
    story_id: str
    model: str
    text: str                        # the reference text (3.2.1)
    sha256: str
    vocab: frozenset[str]            # names that the documentation passages write inside backticks (3.2.2)

def build_reference(story_id: str, model: str, *, builder: PromptBuilder | None = None) -> Reference: ...
def requirement_text(req: Mapping) -> str: ...                    # copy of study2_names.requirement_text
def extract_names(text: str, vocab: AbstractSet[str]) -> list[str]: ...   # copy of study2_names.extract_names (with _tidy and the regex constants)
def search_forms(name: str) -> list[str]: ...                     # copy
def whole_rx(form: str) -> re.Pattern[str]: ...                   # copy
def name_in_reference(name: str, ref: Reference) -> bool: ...     # any form of search_forms(name) matches whole_rx in ref.text

@dataclass(frozen=True)
class RequirementNames:
    index: int
    names: tuple[str, ...]           # extract_names(requirement_text(req), ref.vocab)
    outside: tuple[str, ...]         # the names for which name_in_reference is False

@dataclass(frozen=True)
class NameCheck:
    n_requirements: int
    n_with_names: int                # requirements with at least one name
    n_outside: int                   # requirements with at least one name outside the reference
    n_all_inside: int                # requirements with at least one name and every name inside
    per_requirement: tuple[RequirementNames, ...]
    @property
    def outside_share(self) -> float: ...      # n_outside / n_requirements; 0.0 when no requirement
    @property
    def all_inside_share(self) -> float: ...   # n_all_inside / n_requirements; 0.0 when no requirement
    @property
    def no_name_share(self) -> float: ...      # (n_requirements - n_with_names) / n_requirements; 0.0 when none

def check_requirements(requirements: Sequence[Mapping], ref: Reference) -> NameCheck: ...
```

`n_outside + n_all_inside == n_with_names`, and `outside_share + all_inside_share + no_name_share == 1.0` (when there is at least one requirement). The denominator is always the number of requirements of the record, so the three shares add up and the numbers are comparable between versions even when a version makes more or fewer requirements.

#### 3.2.1 The reference text (exact)

`ref.text` is these parts joined with `"\n\n"`, in this order:

1. The text of each documentation passage (`kind == "doc"`) of the stored block of that story and model (the `v0_old` block, `PromptBuilder.stored_block`), in their order, joined with `"\n\n"`. Code-search hits are not part of the reference. (This is the rule of the author: "the documentation passages of the v0_old block".)
2. The story: title, then story text, then each acceptance criterion on its own line.
3. The system text: the text of `system_context.yaml` of the project.
4. The text of `v0_old/system_prompt.txt` and the output of `secreqgen.generation.prompts.get_json_schema()`. These two are not product facts. They are added so that field names of the answer format (`implementation_notes`, `acceptance_criteria`) and ASVS words that the old prompt itself contains are not counted as invented. They are fixed, so they are the same for every version.

#### 3.2.2 The vocabulary

`vocab` is built from the documentation passages of the reference only (part 1): for each `` `span` `` found by the backtick regex of `study2_names.py` in a line, strip `" \t.,;:()[]{}\"'`*"`; if it is not empty and has no white space, add the span and the span with leading dashes removed; also add each `--flag` (without the dashes). The fact-sheet identifiers are NOT added (the review code adds them; the lab must not use an answer key).

#### 3.2.3 Unit tests (`tests/unit/test_tuning_lab_measures.py`, `tests/unit/test_tuning_pairstats.py`)

pairstats: empty list (n 0, mean None); one value (se None); five values by hand (mean, sd, se); tolerance (a difference of 1e-12 is "equal"); up, equal and down counts; sign "+", "-", "0"; NaN or inf raises.

lab_measures:

1. Copy fidelity: load `scripts/study2_names.py` by path with `importlib` in the test (it has stdlib imports only) and compare `extract_names`, `search_forms`, `whole_rx` pattern text, `requirement_text` with the copies on at least 20 real requirement texts (read from the Study 2b records, read only) and on small hand-made texts (backticks, `--flag`, `FOO_BAR`, `fooBar`, `a.b.c`, `/a/b/c`, `file.yaml`, a placeholder `<realm>`, `name=value`).
2. `build_reference` on a real story: the text holds the documentation passages and no code-hit header (`### code search hit`); holds the story title; holds the first line of `system_context.yaml`; holds the output of `get_json_schema()`. Two calls give the same `sha256`. The reference of the same story is different for the two models only if their stored blocks differ.
3. `name_in_reference`: a name that is in the text (whole name) is inside; a prefix of a longer name is not (`keycloak.conf` against `keycloak.config.resolver`); `--import` against `--import-realm` is not inside; a name found only in a code hit of the block is outside.
4. `check_requirements`: hand-made reference and requirements: one with no name, one with names all inside, one with one name inside and one outside, one with only outside names; check the four counts, the three shares, and that the shares add up to 1. An empty list gives zeros and no division error.
5. The same requirement list scored against the reference of a block with code hits and the reference of the same story is stable: the reference does not depend on the version (build the reference with a `PromptBuilder` whose versions root holds a `drop_code_hits` version; the `text` and `sha256` must be equal).
6. `build_reference` for an unknown story id raises `PromptError`.

## 4. splits.py (builder B)

```python
SPLITS_PATH: Path = SCRIPTS / "tuning_lib" / "splits.json"

@dataclass(frozen=True)
class Splits:
    dev: tuple[str, ...]
    holdout: tuple[str, ...]
    rule: str

class SplitError(Exception): ...

def load_splits(path: Path = SPLITS_PATH, *, known_ids: Collection[str] | None = None) -> Splits: ...
def resolve_stories(spec: str, splits: Splits, *, final_check: bool) -> tuple[tuple[str, ...], bool]: ...
```

`load_splits`: the file must hold exactly the keys `dev`, `holdout`, `rule`; `dev` has 10 and `holdout` has 5 distinct ids; the two lists do not share an id; `rule` is a non-empty string. If `known_ids` is given (the keys of `prompts.load_stories()`), every id must be in it. Else `SplitError`. The file is never written.

`resolve_stories(spec, splits, final_check)` returns `(story ids in a fixed order, used_holdout)`:

- `"@dev"`: the 10 development ids. `"@holdout"`: the 5 hold-out ids. Anything else: a comma list of ids (no spaces, no empty item, no duplicate); each id must be in `dev` or `holdout`, else `SplitError`. A comma list that holds a hold-out id counts as a use of the hold-out stories.
- `used_holdout` is true when the result holds at least one hold-out id.
- If `used_holdout` and not `final_check`: raise `SplitError("the hold-out stories need --final-check")`. If `final_check` and not `used_holdout`: raise `SplitError("--final-check is only for runs that use hold-out stories")` (a flag that does nothing hides a mistake).
- The order: the ids as in `splits.dev` then `splits.holdout` (for a list: sorted by that order). A story is never repeated.

Tests (`tests/unit/test_tuning_splits.py`): the real file loads and has 10 and 5; `known_ids` check with the 15 real ids; a temporary file with an overlap, a wrong count, an extra key, an empty rule, an unknown id: each raises; `@dev`; `@holdout` without the flag raises; with the flag works and `used_holdout` is true; a list with one hold-out id without the flag raises; a list of dev ids with `--final-check` raises; an unknown id raises; the order is fixed.

## 5. run_tuning.py: the `run` subcommand (builder B)

`anchor` does not change. The code of `run` is new and lives beside it. Copy small helpers if that is simpler than refactoring `anchor`. Do not refactor `preflight`, `build_manifest`, `build_report` or `_configuration_block` of the anchor.

### 5.1 Command line

```
python scripts/run_tuning.py run --run-name NAME --versions v0_old,v1 --stories @dev
        [--repeats 3] [--models both] [--workers 6] [--max-cost-usd 1.00] [--max-minutes 60]
        [--max-attempts 3] [--order-seed 1] [--dry-run] [--resume] [--skip-hash-lists] [--final-check]
python scripts/run_tuning.py report --run-name NAME      # now also for a lab run (5.8)
```

Options:

- `--run-name`: same rule as `anchor`.
- `--versions`: comma list of version names, at least one, no duplicate. Each must load (`load_version`) and pass `assert_general`. If `v0_old` is not in the list, print a WARN line: "no baseline in this run; the report has no comparison".
- `--stories`: `@dev`, `@holdout` or a comma list of ids (section 4). Required (no default: a lab run must name its stories on purpose).
- `--repeats` default 3 (at least 1). `--models` default `both`: `both` is `paths.MODELS`; otherwise a comma list of local names from `MODELS`. `--workers` default 6 (1 to 16). `--max-cost-usd` default 1.00. `--max-minutes` default 60. `--max-attempts` default 3 (1 to 3). `--order-seed` default 1 (an int, used by 5.2 only). `--dry-run`, `--resume`, `--skip-hash-lists` (only with `--dry-run`): as in `anchor`.
- `--final-check`: allowed only together with hold-out stories (section 4). 
- The old `--config` option of the stub is removed. `cmd_run` no longer raises `NotImplementedError`. The test `test_cmd_run_is_a_stub` in `test_tuning_runner.py` is replaced by tests of the new command (B edits that one test, nothing else in that file).
- Exit codes as in `anchor`: 0 done; 2 usage error, or a guard, check, split or generality refusal (nothing was sent); 3 stopped early and can be resumed.

Hold-out rule (on top of section 4):
- A run that uses hold-out stories prints one line: `HOLD-OUT STORIES USED: <ids> (versions: <names>, run: <name>)`.
- The manifest records it: `options.final_check = true`, `options.holdout_used = [ids]`, and a note in `notes`.
- A real run (not `--dry-run`) also appends one JSON line `{"ts", "run_name", "versions", "stories", "command_line"}` to `<runs root>/holdout_use_log.jsonl` through `guarded_append_line` (`runs root` is `TUNING_ROOT`, or `runs_root` in tests). A dry run writes nothing.
- A run without hold-out stories has `options.final_check = false` and `options.holdout_used = []`.

### 5.2 Jobs

```python
@dataclass(frozen=True)
class LabJob(Job):                                   # Job is the existing frozen dataclass
    version: str
    version_parent: str | None
    template_sha256: tuple[tuple[str, str], ...]     # (file name, SHA-256) for the three files, in TEMPLATE_FILES order
    passage_changes: tuple[str, ...]
    passage_block_sha256: str
    n_doc_passages: int
    n_code_hits: int

def plan_lab_jobs(sel: Selection, versions: Sequence[str], *, order_seed: int = 1,
                  builder: PromptBuilder | None = None) -> list[LabJob]: ...
```

`Selection` is the existing class: `projects` = the projects of the chosen stories (in `PROJECTS` order), `models`, `arms = ("B2",)`, `stories` = frozenset of ids (never None here), `repeats`.

Fields of a `LabJob` (the inherited `Job` fields):

| Field | Value |
|---|---|
| `config_id` | the version name |
| `step` | `"lab"` |
| `project`, `model`, `story_id`, `repeat` | as in `anchor` |
| `arm` | `"B2"` (the lab sends the RAG prompt with the stored passages; this is the meaning of B2 in Study 2b, and it keeps `measures.py` unchanged) |
| `system`, `user_sent`, `user_non_doc` | from `BuiltPrompt` (`system`, `user`, `user_non_doc`) |
| `doc_context_ref` | `"frozen"` |
| `doc_context_sha256` | `BuiltPrompt.block_sha256` (the block as sent) |
| `stored` | the `StoredPrompt` of the B2 record of this story and model (run 1): source file and line, and the local token counts |

`key` is `(config_id, model, story_id, arm, repeat)` as before, so the version is part of the key and `--resume` works for each version.

`plan_lab_jobs` does, in order:

1. Each version: `load_version`, `assert_general`. (Raises `PromptError` or `GeneralityError`.)
2. `read_study2b_prompts(projects=sel.projects, models=sel.models, arms=("B2",), run=1, stories=sel.stories)`. Each prompt is checked with `verify_stored_prompt` and the context from `read_study2b_context`, as `OldConfigurationAnchor.jobs` does. A missing or errored stored prompt raises (that function already does).
3. For each stored (story, model): `builder.verify_v0_old(story, model)`, always, also when `v0_old` is not one of the versions. So the builder is proved on exactly the pairs of this run. A difference raises `PromptMismatch`.
4. For each (story, model, version): `builder.build(...)`. `NoDocumentationAfterChange` propagates (the run stops with exit 2 before anything is sent). For `v0_old`, `user_sent` equals the stored `user_sent` by step 3.
5. One job per repeat 1 to `sel.repeats`.
6. Order. Group the jobs by `(repeat, story id, model)`. Order the groups by repeat, then by story id (sorted), then by model (`MODELS` order). Inside a group, order the versions by a seeded shuffle: `random.Random(f"{order_seed}|{repeat}|{story_id}|{model}").shuffle(list(versions))`. So old and new always run in the same minutes, with no fixed order of the versions, and a stop leaves complete repeats. (With 6 workers, one group of 2 versions and the group of the other model are all in flight together.)
7. The keys must be unique, else raise.

### 5.3 The record

The lab record is the existing record (`records.REQUIRED_RECORD_FIELDS`, `DESIGN.md` 8.1) with these values and six extra fields (6.1):

| Field | Value in a lab record |
|---|---|
| `study` | `"tuning"` |
| `step` | `"lab"` |
| `config_id` | the version name |
| `arm` | `"B2"` |
| `arm_description` | `f"prompt version {version}, RAG prompt with the stored Study 2b passages"` |
| `source_study`, `source_run`, `source_file`, `source_line` | the Study 2b B2 record that holds the stored passages (`job.stored`) |
| `prompt_system`, `prompt_user_sent`, `prompt_user_non_doc` | the full prompts that were sent |
| `doc_context_ref` | `"frozen"` |
| `doc_context_sha256` | SHA-256 of the block as sent |
| `prompt_check` | for `v0_old`: `"built from the template folder; equal to the stored prompt of Study 2b arm B2 (checked)"`; for any other version: `"built from the template folder and the stored passages"` |
| `template_sha256` | `{"system_prompt.txt": "...", "rag_user_prompt.txt": "...", "zero_shot_user_prompt.txt": "..."}` |
| `passage_changes` | list of the change names (empty for `v0_old`) |
| `version_parent` | the parent name or null |
| `passage_block_sha256` | same as `doc_context_sha256` (kept as a separate field so that the lab fields are complete on their own) |
| `n_doc_passages`, `n_code_hits` | counts in the block as sent |

Everything else is as in `anchor` (attempt loop, retry rule, request body, `request_sha256`, token fields, parse fields). The runner changes `_record_base` so that a `LabJob` adds the six fields and uses the values above; for any other job the result is byte for byte what it is today.

### 5.4 Execution

`execute_detailed` is reused as it is. It needs no change: it works on a list of `Job` objects, keys by `job.key`, and writes records to `run_dir/<project>/<model slug>/records.jsonl` (all versions of one cell go into one file; the key separates them). The same guards, the same cost cap, the same time cap, the same retry rule (same prompt again, 3 attempts at most, waits of 1 s and 2 s), the same "4 failed in a row" stop, the same run lock, and the same record checks (`new_record`, privacy guard) apply.

### 5.5 Preflight, `--dry-run`, manifest

```python
def preflight_lab(sel: Selection, jobs: Sequence[LabJob], versions: Sequence[str], run_dir: Path, *,
                  dry_run: bool, resume: bool, max_cost_usd: float, skip_hash_lists: bool,
                  guard: WriteGuard = DEFAULT_GUARD, max_attempts: int = 3) -> list[Check]: ...
```

A new function (the anchor `preflight` stays). It returns `Check` objects of the same class. Checks:

1. `run folder`: as in the anchor (guard, name rule, exists or resume). With `--resume` the manifest must have `step == "lab"` and the same `options.versions`, `options.stories`, `options.models`, `options.repeats`, `options.max_attempts`, `options.order_seed`, the same pins, and the same `configuration.prompt_versions[...].template_sha256` and `passage_changes` as the versions now on disk. (A version that was edited after its first records cannot be resumed.)
2. `hash lists (before)`, `other processes and lock`: as in the anchor.
3. `prompt versions`: each version loads; `check_generality` result: PASS with a count of inherited hits, FAIL with the new hits (at most 10 lines).
4. `v0_old rebuild`: `verify_v0_old` passed for each (story, model) of the run (done in `plan_lab_jobs`; report the count).
5. `passages`: for each job, `n_doc_passages >= 1`; the block is not the 41-character fallback. (`NoDocumentationAfterChange` is a FAIL line with the story, the model and the version.)
6. `hold-out`: PASS "no hold-out story" or WARN "HOLD-OUT STORIES USED" with the ids (the run is allowed only with `--final-check`, which `resolve_stories` already required).
7. `request bodies`: for every job `build_request_body` works, `reasoning.effort == "none"`, one provider, `allow_fallbacks` false (as in the anchor).
8. `prompt size`: bytes/4 estimate of system plus user at most `MAX_PROMPT_TOKENS_EST` for every job (FAIL above). WARN when the cautious estimate is above it. For a version other than `v0_old`, the local window rule of the anchor (`local_prompt_tokens + 4096 <= 16384`) cannot be tested with the stored token count of the old prompt; use the scaled value (`local_prompt_tokens * bytes4(job) / bytes4(stored B2 prompt)`) and report only a WARN.
9. `pins`: as in the anchor (WARN when unconfirmed, FAIL for more than 4 generations with an unconfirmed pin).
10. `cost estimate`: per job, input tokens = `stored.local_prompt_tokens * bytes4(job) / bytes4(stored B2 prompt)`, output tokens = `stored.local_completion_tokens`, price from the pin. FAIL above `--max-cost-usd`. Print the estimate (estimate only).
11. `local reference`: not needed for a lab run (skip; the anchor check stays in the anchor).

`--dry-run` prints: the number of jobs for each model and version, repeats, the order of the first 12 jobs (to show the mixing), estimated tokens and cost, the longest prompt, each check, then `key: not read, no network call made`. It creates no folder, no lock, no file. It returns 0 when no check failed, else 2. The hash lists are read unless `--skip-hash-lists`.

Manifest: the existing `records.validate_manifest` key list is fixed and the key `versions` already means Python versions, so the lab manifest uses these keys (new function `build_lab_manifest`, the anchor `build_manifest` stays):

- `step`: `"lab"`.
- `options`: the anchor options plus `versions` (list), `stories` (list of ids), `stories_spec` (the text of `--stories`), `order_seed`, `final_check` (bool), `holdout_used` (list).
- `configuration`: `{"config_id": "lab", "source": "prompt versions below scripts/tuning_lib/prompt_versions, with the stored Study 2b passages", "retry_rule": ..., "parser": ..., "request_template": ..., "prompt_versions": {name: {"parent", "description", "passage_changes", "template_sha256": {file: sha}, "version_json_sha256"}}}`.
- `jobs.per_model_arm` holds `"<model>/<version>": count`.
- `inputs`: `study2b_records_sha256` as in the anchor, plus `splits_sha256` and `analysis_s2b_sha256` is left out (the lab does not read it).
- Everything else (models, cost, tokens, versions of Python, code hashes, hash lists, client, notes) as in the anchor. The code hashes also include `scripts/tuning_lib/prompt_versions/*/version.json`. The manifest text goes through `assert_no_private_text`.

### 5.6 Selection of models and projects

`--models both` gives `MODELS`. The projects are derived from the stories (`PromptBuilder.story(sid).project`), in `PROJECTS` order. A comma list of models keeps the `MODELS` order. An unknown model, or a model without a hosted pin, exits with 2.

### 5.7 Records extension (`records.py`)

```python
LAB_RECORD_FIELDS: dict[str, tuple[type, ...]] = {
    "template_sha256": (dict,),
    "passage_changes": (list,),
    "version_parent": (str, _N),
    "passage_block_sha256": (str,),
    "n_doc_passages": (int,),
    "n_code_hits": (int,),
}
```

`validate_record` rule: if `rec["step"] == "lab"`, the six `LAB_RECORD_FIELDS` are required with these types, and no other extra field is allowed. For any other `step`, the six fields are unknown fields (an error, as today). In a lab record `template_sha256` has exactly the three `TEMPLATE_FILES` keys with 64-hex values, and `passage_changes` holds strings. Everything else in `records.py` is unchanged. The existing tests stay green. `record_key`, `key_states`, `read_jsonl` work for lab records as they are.

### 5.8 The lab report

`cmd_report(run_name, ...)` first reads `manifest.json` (if it exists). If `manifest["step"] == "lab"`, or there is no manifest and the first record has `step == "lab"`, it takes the lab path: `build_lab_report` and `render_lab_report_md` from `lab_report.py`, written as `report.json` and `report.md` through the guard (and the privacy check), and printed. Otherwise it is the anchor path, unchanged (the `anchor1` and `smoke1` folders have `step == "anchor"`). A run folder that mixes steps is an error.

`run` calls `cmd_report` at the end when every job is final (same rule as `anchor`).

```python
def build_lab_report(run_dir: Path, *, fact_sheets: Mapping[str, Sequence[str]] | None = None,
                     builder: PromptBuilder | None = None, baseline: str = BASELINE) -> dict[str, Any]: ...
def render_lab_report_md(report: Mapping[str, Any]) -> str: ...
```

`build_lab_report` reads all `records.jsonl` below the run folder and the manifest. `fact_sheets` defaults to `measures.load_fact_sheets()`. `builder` defaults to `PromptBuilder()` (for the references).

Definitions (all use the last line of each key, errors left out, like `measures.latest_records`; a "final ok record" is such a line with `error is None`):

- Record name use: `measures.score_record(rec, fact_sheets[story_id]).name_use` (the registered measure). `n_with_name` and `n_requirements` come from the same call.
- Record outside share and all-inside share: `lab_measures.check_requirements(rec["requirements"], reference(story_id, model))`, with `reference` from `build_reference` (one fixed reference for each story and model, the same for every version).
- Story value of (version, model, story): the mean over the final ok repeats of the record value. A story with no final ok repeat has no value.
- Cell value (version, model): the mean over the stories of the story values (this is how `measures.arm_rates` computes the arm rate).
- Story value of a version for a comparison: the mean over the models of the (model, story) values. A story is part of a comparison only when both the version and the baseline have a value for every model of the run. The other stories are listed in `stories_missing`.
- Difference of a story: version value minus baseline value. `paired_summary` over the differences gives mean, standard error, up, equal and down. The sign for each model comes from `paired_summary` of that model's differences.

JSON schema of `report.json`:

```
{
 "schema_version": 1, "run_name": str, "generated_utc": str, "state": str, "baseline": "v0_old" or null,
 "options": {...copy of manifest options...},
 "versions": {name: {"parent", "description", "passage_changes", "template_sha256"}},
 "models": [local names], "stories": [ids], "repeats": int,
 "cells": [ {                                   # one for each version and model
    "version", "model",
    "n_planned": int,                           # stories x repeats
    "n_final_ok": int, "n_final_failed": int, "n_not_final": int,
    "parse_failures": {"attempt_lines_failed": int, "final_failed_keys": int},
    "requirements_per_answer": float | null,    # mean n_requirements over final ok records
    "name_use": float | null,                   # registered measure, arm rate
    "n_with_name_per_story": {story: float},    # mean over repeats of the count of requirements with an attributable name
    "outside_share": float | null, "all_inside_share": float | null, "no_name_share": float | null,
    "cost_usd": float, "mean_prompt_tokens": float | null, "mean_completion_tokens": float | null } ],
 "per_story": {version: {model: {story: {"n_runs", "name_use", "n_with_name", "n_requirements",
                                          "outside_share", "all_inside_share"}}}},
 "comparisons": [ {                             # one for each version except the baseline and each metric
    "version", "baseline", "metric", "n_stories", "stories_missing": [ids],
    "per_story": {story: difference},
    "mean", "se", "up", "equal", "down",
    "per_model": {model: {"n", "mean", "se", "up", "equal", "down", "sign"}} } ],
 "totals": {...measures.RunTotals as a dict...},
 "notes": [str]
}
```

`metric` takes these values, in this order: `name_use`, `n_with_name`, `n_requirements`, `outside_share`, `all_inside_share`. `cost_usd` in a cell is the sum of `cost_usd` over all attempt lines (also failed attempts) of that version and model. `parse_failures.final_failed_keys` counts keys whose last line has an error.

`report.md` has: a header (run name, state, models, provider names seen, the stories, the repeats, a line for a hold-out use); a table of the versions (name, parent, description, passage changes); a table of cells (columns: version, model, final ok of planned, parse failures, requirements per answer, name use %, outside share %, all-inside share %, cost); the "requirements with an attributable name for each story" table (stories in rows, version and model in columns); and, for each version against the baseline, a table of the five metrics (mean difference, standard error, stories up, equal and down, sign for each model), then the table of differences by story for `name_use` and `outside_share`. Notes (fixed text, at least): "Script measures only. They show where names come from. They do not show whether a requirement is right. The judges do that."; "One fixed reference for every version: the documentation passages of the v0_old block, the story and the system text."; "The standard error is over stories, not over requirements."; "No sentence in this file says that a version is better."

### 5.9 Unit tests of the run (`tests/unit/test_tuning_lab_run.py`, `tests/unit/test_tuning_lab_report.py`)

All tests use a fake client (`httpx.MockTransport` through `OpenRouterClient`, as the anchor tests do), `tmp_path`, and `WriteGuard(allowed_roots=[tmp_path], ...)` with `runs_root=tmp_path`. No test uses the network or the key (`load_api_key` is replaced by the `client_factory` parameter of `main`). The fake client answers with a fixed valid JSON that holds one requirement that cites a name from the story (so name use is not zero) and a second answer variant that is invalid JSON (for retries).

Planning and order:
1. `plan_lab_jobs` with `v0_old` and a temporary version `v1` (made with `new_version` in a temporary versions root and a `PromptBuilder(versions_root=...)`), 2 stories, both models, 3 repeats gives 2 x 2 x 2 x 3 = 24 jobs with unique keys; `config_id` is the version; `arm` is `"B2"`; `step` is `"lab"`.
2. For `v0_old` jobs: `system` and `user_sent` equal the stored B2 prompt (30-case check for the real 15 stories through `plan_lab_jobs`, dry, no run).
3. Order: jobs are grouped by repeat; inside a repeat by story and model; the two versions of one (story, model) are neighbours; with two different `order_seed` values the order of versions inside the groups differs for at least one group; the same seed gives the same list. A stop after the first repeat leaves repeat 1 complete (simulate with `max_cost` small).
4. A version with `drop_code_hits` gives `n_code_hits == 0` and its `doc_context_sha256` differs from `v0_old` where the block had code hits.
5. A version whose change leaves no documentation passage (use a temporary registry entry added with `monkeypatch.setitem(PASSAGE_CHANGES, "drop_all", lambda b: "")`... or a block that has only code hits) stops with `NoDocumentationAfterChange` and `main` returns 2 and writes nothing.
6. A version with a product word is refused with exit 2 and nothing is written; a version with only an inherited hit (v0_old itself) is accepted.
7. A tampered `v0_old` (temporary root, one changed byte) gives `PromptMismatch` and exit 2.

Command line and guards:
8. `parse_args`: all options and defaults; `--stories` is required; `--final-check` without hold-out stories exits 2; `@holdout` without `--final-check` exits 2 (also with `--dry-run`); a comma list with one hold-out id without the flag exits 2; `--skip-hash-lists` without `--dry-run` exits 2; a bad run name, `study2`, `study2b` exit 2; an unknown model exits 2; an unknown version exits 2.
9. `@holdout --final-check`: the printed line `HOLD-OUT STORIES USED` appears; the manifest has `options.final_check` true and the ids in `holdout_use`; `holdout_use_log.jsonl` has one new line (real run), and none after a dry run.
10. `--dry-run`: returns 0, prints counts and the line `key: not read, no network call made`, creates no folder, no lock and no file below the runs root (compare listings), reads no key (a `load_api_key` that raises if called).
11. Existing run folder without `--resume` exits 2; `--resume` with another repeats value, other versions, other stories or an edited version template (change a byte in the temporary version after the first run) exits 2.

Execution (fake client):
12. A full small run (2 stories, 2 versions, 2 repeats, both models = 16 jobs): exit 0; records in `<run>/<project>/<model slug>/records.jsonl`; every record validates (`validate_record`) with `step == "lab"` and the six extra fields; `config_id` is the version; the stored prompts in the records equal the prompts that the fake client received (compare the body of each request with the record); a hash of `template_sha256` equals the file hash; the manifest validates and holds `configuration.prompt_versions` and `options.versions`; the key `versions` of the manifest still holds the Python versions.
13. Caps and stops: cost cap, time cap (fake clock), 4 failed in a row, a fatal error: exit 3 and `--resume` finishes the run without repeating a final key (same behaviour as the anchor tests; one test each is enough).
14. Parse retry: the first answer of one job is invalid JSON, the second is valid: two lines for the key, the last ok; a job with three invalid answers is a failed key and is counted in `parse_failures`.
15. A record check refuses an unknown extra field in a lab record, and refuses a lab record with a missing lab field (`tests/unit/test_tuning_records.py`, new cases). A non-lab record with a lab field is refused.
16. Guard: the source scan test of `DESIGN.md` 9 (no write call outside `guard.py`) covers the new code in `run_tuning.py`, `splits.py` and `lab_report.py`.
17. Anchor unchanged: all existing anchor tests pass; `cmd_report` on a synthetic anchor folder gives the same report as before; `cmd_report` on a synthetic lab folder takes the lab path; a folder that mixes `anchor` and `lab` records raises.

Report (`test_tuning_lab_report.py`, synthetic records, no run):
18. Hand-made records for `v0_old` and `v1`, 3 stories, 2 models, 2 repeats, with known requirement texts and a hand-made reference: check name use, `n_with_name_per_story`, outside share, all-inside share, no-name share, requirements per answer, parse failures, cost for one cell by hand.
19. The reference is the same for both versions: a `v1` record with a different `doc_context_sha256` is scored against the `v0_old` reference (the report never reads `prompt_user_sent` for this).
20. Comparison: story differences, mean, standard error (by hand, 3 stories), up, equal, down; the sign of each model; a story that lacks a value for one model in one version goes to `stories_missing` and is not in the mean; with no baseline in the run, `comparisons` is empty and `baseline` is null; a version with an incomplete cell is marked in the notes.
21. A record with an error as its last line is left out of the values and counted in `parse_failures.final_failed_keys`; an earlier error followed by an ok line is an ok key and the failed attempt line is counted in `attempt_lines_failed`.
22. `render_lab_report_md` holds each table and the fixed notes, and holds no private text (`assert_no_private_text` passes) and no sentence "better".
23. Registered values: `name_use` of a `v0_old`-like set built from the real Study 2b B2 records (re-labelled as lab records in memory, `config_id = "v0_old"`) equals `analysis_s2b.json` `rate` for B2 to 9 decimals for both models (this reuses the acceptance test of `measures.py` through the lab report).

## 6. judging.py (builder C)

No import of `run_tuning`, `prompts` or `lab_measures`. The module reads a run folder (records only) and writes below `<run folder>/judging/<round>/` through the guard.

### 6.1 Constants, errors

```python
ROUND_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,31}$")
LABELS: tuple[str, ...] = ("S", "G", "U")
U_KINDS: tuple[str, ...] = ("unsupported_detail", "wrong_purpose_or_case", "name_not_found",
                            "wrong_fact", "wrong_name_form", "not_usable")
NONE_KIND = "none"
BASELINE = "v0_old"

class JudgingError(Exception): ...        # every refusal; the message says which file, story or qid
```

### 6.2 The item text

```python
def clean(value) -> str: ...
def md_safe(text: str) -> str: ...
def fmt_requirement(req: Mapping, md: bool) -> str: ...
def item_text(req: Mapping) -> str: ...        # fmt_requirement(req, md=False)
```

`clean`, `md_safe` and `fmt_requirement` are copied without change from `scripts/build_study2_review_sheet_v2.py` (the scratch script of this morning copied the same three functions and checked them against the file at run time; copy them from there). The item shows only the title, the description, the rationale, the acceptance criteria and the implementation notes: no source references, no category, no priority, no id. `item_text(req)` is `fmt_requirement(req, md=False)`. The text of every item has the form of `ITEM_FORM` of the scratch script (the regex that starts with `\*\*Title\.\*\*`); copy that regex and check each item against it.

### 6.3 build_batches

```python
@dataclass(frozen=True)
class BatchDesign:
    round_dir: Path
    n_items: int
    n_items_by_story: dict[str, int]
    batch_paths: tuple[Path, ...]            # batches/<story_id>.json, sorted
    key_path: Path                           # private/key.json
    design_path: Path                        # private/design.json
    deviations: tuple[str, ...]

def build_batches(run_dir: Path, versions: Sequence[str], stories: Sequence[str], per_cell: int, seed: int,
                  round_name: str, *, models: Sequence[str] | None = None, exclude_rounds: Sequence[str] = (),
                  allow_short: bool = False, guard: WriteGuard = DEFAULT_GUARD) -> BatchDesign: ...
```

Inputs: `run_dir` is a run folder below `tuning/` (the guard checks writes only; reading is plain). `versions` and `stories` are given by the main session (this module does not know the splits and does not decide anything about the hold-out; the main session passes the ids). `per_cell` is at least 1. `models` defaults to the models found in the records (in `paths.MODELS` order). `round_name` matches `ROUND_RE`.

Draw (deterministic; the same inputs give the same files, except `created_utc` in `design.json`):

1. Read all `run_dir/*/*/records.jsonl` (`records.read_jsonl`). Keep the last line of each `record_key`. Keep the ones with `error is None`. Only `step == "lab"` records are allowed; any other step raises `JudgingError`.
2. A unit is `(version, model, story_id, repeat, requirement_index)`; `requirement_index` is the position in `rec["requirements"]` (counted from 0). A pool is the list of units of one cell `(story, version, model)`, sorted. Units of the rounds in `exclude_rounds` (read from `judging/<round>/private/key.json`) are removed from the pools. A requested version, story or model with no records at all raises.
3. For a cell: `rng = random.Random(f"{seed}|{round_name}|{story_id}|{version}|{model}")`. Let `R` be the repeats that have at least one free unit. Base quota `per_cell // len(R)`; the remainder goes to the repeats in the order of `rng.sample(R, len(R))`. A repeat with fewer free units than its quota gives all of them, and the shortfall goes round-robin to the other repeats that still have free units. Inside a repeat, `rng.sample` of the free indices. So the draw is spread over the repeats as evenly as the data allows, and a unit is never drawn two times.
4. If a cell has fewer free units than `per_cell` in all: take all of them and add a line to `deviations` (story, version, model, wanted, got) when `allow_short` is true; else raise `JudgingError` (no silent short batch).
5. Per story: all drawn units of all (version, model) cells are one list. Sort it by `(version, model, repeat, index)`, then shuffle it with `random.Random(f"{seed}|{round_name}|{story_id}|mix")`. Item number `n` (from 1) gets the id `f"{story_id}-q{n:02d}"`. So versions and models are mixed, and the id carries no information.
6. Text of an item: `item_text(requirement)`.

Files (written through `guarded_mkdir` and `guarded_write_text`; the round folder must not exist, else `JudgingError`; `json.dumps(..., ensure_ascii=False, indent=1) + "\n"`):

- `batches/<story_id>.json`: `{"story_id", "project", "n_items", "items": [{"qid", "text"}]}`, exactly these keys in this order, `project` from the record.
- `private/key.json`: `{qid: {"story_id", "project", "version", "model", "repeat", "requirement_index"}}`.
- `private/design.json`: `{"schema_version": 1, "round", "created_utc", "seed", "per_cell", "versions", "models", "stories", "exclude_rounds", "records_sha256": {relative path: sha256 of each records.jsonl}, "counts": {story: {version: {model: n}}}, "deviations": [...], "item_text_function": "fmt_requirement(req, md=False) of scripts/build_study2_review_sheet_v2.py", "checks": [{"name", "ok", "detail"}]}`. It is in `private/` (not at the round root) on purpose: it names versions, and the main session gives the judges the `batches/` folder only.

Checks after writing (read the files back; any failure raises `JudgingError`, so a bad round is never left looking fine): the batch files have the exact keys; every `qid` is unique and has the form `<story>-qNN` with NN running from 01 without a gap; the key has one entry for each qid and no other; each item text equals `item_text` of the requirement that the key points to (re-read from the records); each text matches `ITEM_FORM`; no unit occurs two times in the key; `n_items` is the length of `items`; the skeleton of each batch (everything except the item texts) holds none of: a version name, `v0`, a model name or its first word (`qwen`, `gemma`), `arm`, `version`, `model`, `repeat`, `B2` (case-insensitive). A needle that occurs inside an item text (for example the word "gemma" in a requirement) is not an error: it is listed in `design.json` under `checks` as `text_hits` with the qids.

### 6.4 score

```python
def score(judgments_path: Path, key_path: Path, *, out_dir: Path | None = None,
          baseline: str = BASELINE, guard: WriteGuard = DEFAULT_GUARD) -> dict[str, Any]: ...
```

`out_dir` defaults to the round folder (`key_path.parent.parent`). The function writes `scores.json` and `scores.md` there through the guard, and returns the scores dict.

Input file, written by the main session: `{story_id: {judge_name: [ {"qid", "label", "kind", "claim", "why", "doubt"} ]}}`. Rules (a violation raises `JudgingError` with the story, the judge and the qid):

- Each story id must be a story of the key. Each judge covers every qid that the key has for that story, exactly once (no missing, no extra, no duplicate qid). Every story that is in the judgments file must have the same set of judges. At least one judge.
- `label` is one of `LABELS`. `kind` is `NONE_KIND` or one of `U_KINDS`. For `S` and `G`, `kind` must be `none`. For `U`, a kind from `U_KINDS` is expected; `none` is accepted but counted in `warnings` (list of qids). `claim` and `why` are strings; `doubt` is a bool.
- Stories of the key that are not in the judgments file are not scored (a note says which ones).

Computation:

1. Cell counts. For each judge, version, model and story: `n`, `S`, `G`, `U` and the three shares. For "the mean of the judges": the shares are the mean of the judges' shares (each judge has the same items, so this is the share of the mean count).
2. Story value of a version: the mean over the models of the mean-of-judges share (so equal draws and unequal draws both count each model once). Version value: the mean over the stories of the story values.
3. Metrics: `u_share`, `s_share`, `g_share`, and `net` = `s_share - u_share`.
4. Each version against the baseline, by story: difference of each metric (version minus baseline) for every story where both have a value for every model; `paired_summary` gives mean, standard error, up, equal and down; also for each model alone (with `sign`). Stories that lack a value are listed in `stories_missing`.
5. Agreement of the judges (only when there are at least 2 judges). Over all items, and again for each version: number of items, number of items where all judges gave the same label, the share; for each pair of judges: number of items, number of equal labels, the share, and Cohen's kappa over the three labels (`None` when the expected agreement is 1); the mean of the pairwise kappas. With one judge, `agreement` is null and a note says so.
6. U kinds: for each version and kind, the count of `U` labels with that kind, as the mean over the judges (a float). Also the number of items with `doubt` true.

`scores.json` keys: `schema_version`, `round` (the round folder name), `judges`, `baseline`, `stories_scored`, `stories_not_scored`, `n_items`, `cells` (a list of `{version, model, story, judges: {name: {n, S, G, U, s_share, g_share, u_share}}, mean_judges: {s_share, g_share, u_share}}`), `by_version` (`{version: {model: {metric: value}, "all_models": {metric: value}}}`), `comparisons` (a list of `{version, baseline, metric, n_stories, stories_missing, per_story, mean, se, up, equal, down, per_model}`), `agreement`, `u_kinds`, `warnings`, `notes`. `scores.md` shows: the table of versions (model rows, S, G and U shares, net), each version against the baseline (mean difference of the U share, the S share and net, standard error, stories up, equal and down, sign for each model), the table of differences by story, the agreement table, the U kinds table, and the fixed notes: "The judges are AI models. The numbers are exploratory."; "The standard error is over stories."; "No sentence in this file says that a version is better." The text goes through `assert_no_private_text`.

### 6.5 How the main session calls it

No command line (a module in a package, no side effect). One line of Python:

```
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:scripts .venv/bin/python -c "from pathlib import Path; from tuning_lib import judging as j; ..."
```

### 6.6 Unit tests (`tests/unit/test_tuning_judging.py`)

Synthetic run folders in `tmp_path` (lab records with 2 versions, 2 models, 3 repeats, 3 stories, 4 to 6 requirements each); `WriteGuard(allowed_roots=[tmp_path], forbidden_roots=[], forbidden_names=[])`.

1. Item text: `clean`, `md_safe`, `fmt_requirement` equal the functions of `scripts/build_study2_review_sheet_v2.py`: read that file as text, cut the three function sources with `ast` (`ast.get_source_segment`), and compare with `inspect.getsource` of the copies (the same check the scratch script did at run time). `item_text` matches `ITEM_FORM` for 20 real requirements read from the Study 2b records (read only). An item shows no `source_references`, category or priority.
2. Batch structure: keys and key order of the batch file; the file names; qid form; `n_items`; the key has one entry for each qid; the text of each item equals `item_text` of the requirement that the key names.
3. The draw: `per_cell = 3` gives exactly 3 items for each (version, model) of each story; spread over the repeats (3 repeats: one each); `per_cell = 4` gives 2,1,1 over the repeats and the repeat that gets 2 depends on the seed; no unit twice; a cell with too few requirements raises without `allow_short`, and with it gives all and a line in `deviations`; a repeat that lacks units gives its shortfall to the others.
4. Determinism: the same arguments give byte-equal `batches/` and `private/key.json` (compare the files); another seed gives another mix; the round name is part of the seed (two round names give different mixes with the same seed).
5. Mixing and blindness: the versions of one story are mixed (the qid order does not follow the version: check that, for a fixed seed, the sequence of versions along the qids is not sorted); no batch skeleton holds a version name, a model name, `v0`, `arm` or `B2`; a requirement text that contains the word "gemma" does not raise and is listed in `checks` as `text_hits`.
6. `exclude_rounds`: a second round with the first round excluded draws no unit of the first round's key.
7. Refusals: an existing round folder; a bad round name; `per_cell` 0; a version or story with no records; a record of step `anchor`; records with an error as last line are not drawn (an error line followed by an ok line for the same key is drawn once from the ok line); a run folder outside the guard roots (the guard refuses the write).
8. Score input checks: a missing judge for one qid, an extra qid, a duplicate qid, a bad label, a bad kind, `kind != none` for an S, an unknown story, a story with different judges: each raises `JudgingError`. `U` with kind `none` is accepted and listed in `warnings`.
9. Score numbers by hand: 2 versions, 2 models, 2 stories, 2 judges, 4 items per cell: check counts and shares for each judge and for the mean of the judges; the story value (mean over models); the version value (mean over stories); the difference against the baseline for `u_share`, `s_share`, `net`; the mean, the standard error, up, equal and down over stories; the sign for each model; `stories_missing` when a story is judged for one version only.
10. Agreement by hand: pairwise equal share; kappa for a case with a known value (two judges, 10 items, a known table; also the case where all labels are equal, kappa `None`); the share of unanimous items; with one judge `agreement` is null.
11. Output: `scores.json` and `scores.md` are written into the round folder (not elsewhere); the JSON re-loads and holds the listed keys; the md holds the fixed notes and no sentence "better"; a write outside the guard roots is refused; the key of a story not in the judgments file is reported as not scored.
12. Source scan: `judging.py` holds no write call outside `guarded_*`.

## 7. Order of work and how the main session uses the lab

1. Builders build in parallel. A writes `pairstats.py` first. Each builder runs only its own test files, from `coded_project/secreqgen`: `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src TOKENIZERS_PARALLELISM=false .venv/bin/python -m pytest tests/unit/test_tuning_<name>.py -q -p no:cacheprovider`. The main session then runs all `tests/unit/test_tuning_*.py`.
2. Make a version: `new_version("v1_xxx", "v0_old", "One sentence that says what changed.")`, then edit the template files and `version.json` by hand.
3. Dry run: `... scripts/run_tuning.py run --run-name r1 --versions v0_old,v1_xxx --stories @dev --repeats 3 --dry-run`. Read the checks and the cost.
4. Real run: the same line without `--dry-run`. About 120 generations, about 0.4 to 0.5 USD.
5. Report: `run` writes `report.json` and `report.md` at the end. `report --run-name r1` makes them again.
6. Judging: `build_batches(...)`, the judges read `judging/<round>/batches/<story>.json` and the evidence files `tuning/_judging/evidence/<story_id>.md`, the main session writes the judgments file, `score(...)`.
7. At the end: one run with `--stories @holdout --final-check` for the old and the best version, then the full chain of judges.

## 8. What makes a point impossible, and the nearest safe form

1. The author says build_prompt returns "the system prompt and the user prompt". A bare pair would lose the passage block and the counts that the record needs. Nearest safe form: `build_prompt` returns a `BuiltPrompt` that has `system` and `user` and the other fields; `PromptBuilder.build` is the same call.
2. "Rebuild byte for byte" needs the stored passages and the system context file. The system context file (`real_test_cases/<project>/system_context.yaml`) is not in the hash lists of the registered files. If it is changed later, `v0_old` no longer equals the stored prompt. This is not hidden: `verify_v0_old` runs before every run and raises `PromptMismatch`. The 30-case check passed today.
3. The old builder substitutes with `string.Template.safe_substitute`, which leaves an unknown `$word` unchanged and falls back to a built-in template for a missing file. Both are silent. Nearest safe form: `load_version` forbids a `$` that is not a known placeholder, forbids a missing file, and requires `retrieved_context` exactly once (2.2). A version that wants a literal dollar sign cannot have one until the rule is changed on purpose.
4. The generality check cannot know every word that is "of a story". It finds product names, fact-sheet identifiers, story ids and copied 5-word phrases. It cannot find a paraphrase of a story. Nearest safe form: these four kinds are checked by a script; the rest is the work of the reviewer, and each version has a one-sentence description for the reader. The check also flags plain words that are fact-sheet identifiers (`strict` is one). It does not guess: the author or the main session decides, and `v0_old` itself holds this one hit, which is why inherited hits do not fail.
5. `guarded_write_text` writes text. `new_version` copies templates as text. This is byte exact when the files are UTF-8 with `\n` line ends, and `load_version` enforces both (no carriage return). If a builder finds a case where the copy differs by a byte, the builder stops and tells the main session. Do not write with `open(..., "wb")` outside `guard.py`.
6. The record schema is closed, and the manifest key list is fixed. A lab record cannot hold the template hashes and the passage changes without a change to `records.py`. Nearest safe form: the six `LAB_RECORD_FIELDS`, allowed only when `step == "lab"` (5.7), and the lab manifest keeps the prompt versions in `configuration.prompt_versions` because the key `versions` is taken.
7. "The two new script measures use ONE fixed reference". A reference made only of the documentation passages, the story and the system text marks names that come from the code-search hits or from the repository as "outside". That is on purpose (a version that drops the code hits is then measured on the same ground as the old one). It means that a name that is real in the repository but not in these texts counts as outside. The measure does not say that a name is wrong, only that the model did not get it from this material. I added two fixed texts to the reference (the old system prompt and the JSON schema text) so that field names of the answer format are not counted. They are the same for every version.
8. The name rules of the review (`study2_names.extract_names`) take a `vocab` that the review builds from the whole documentation folder and the fact-sheet identifiers. The lab cannot use the identifiers (they are an answer key) and does not read the whole documentation folder (the reference is the documentation passages). Nearest safe form: `vocab` comes from the backtick spans of the documentation passages of the reference (3.2.2). Lower-case hyphen words (`proxy-headers`) outside backticks therefore count as names only by the three-part rule. Numbers will differ a little from the review's name census. They are consistent between versions.
9. `design.json` of a judging round. The brief names `batches/`, `private/key.json` and `design.json` below the round folder. `design.json` lists versions and counts, so a judge who sees it is not blind. Nearest safe form: it is written to `private/design.json` (the layout of the morning check). The main session gives the judges `batches/` only.
10. Judging uses AI judges that the main session starts. The module does not call any model. It cannot check that a judge was blind; it checks only that the batch files carry no version, model or arm word outside the requirement text.
11. A run with only one version, or without `v0_old`, has no comparison. The report says so. The runner prints a WARN and goes on, because a run of one version for a check of the prompt builder is useful.
12. The cap of the cost is for generations only. The cost of the judges is outside this runner. The main session keeps that count.
13. A run folder may hold a record of `v0_old` from run A and a new record of `v0_old` from a later `--resume`. The key is `(version, model, story, arm, repeat)`, so repeats are not mixed between runs: each run has its own folder. Never use the same folder for two different sets of versions (the `--resume` check refuses it, 5.5).
14. Existing test `test_cmd_run_is_a_stub` must change. This is the one change to an existing test of `anchor` that the design asks for. The behaviour of `anchor` is the same.
15. The scratch script of this morning (`build_batches_of_the_s2b_check.py`) read `study2/review_v2/sheet.json` (allowed) and wrote the evidence files. `judging.py` does not read the sheet and does not write evidence files. The 15 evidence files that exist stay as they are (the guard and the hard rules say that nothing below `tuning/` is changed or deleted by a build).
16. Nothing here needs a network call, a model, the key or the embedder. The first real call is the main session's `run` without `--dry-run`.
