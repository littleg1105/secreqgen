# SecReqGen

SecReqGen writes security requirements and a draft threat list for each user story. It reads
the documentation of a product. It uses local language models that run in Ollama. With an
Ollama server on your own computer, no data leaves your computer.

## Requirements

- Python 3.11, 3.12 or 3.13. The tested environment is Python 3.13. Python 3.14 is not supported.
- [Ollama](https://ollama.com), running on your computer or on a server that you can reach.
- The Ollama models that you want to use. The tested setup uses `gemma4:26b` and `qwen3.8:27b`
  (see the two sections below). The tool does not download models. Pull them with `ollama pull`.

## Installation

Run these commands in the folder of this repository.

```bash
python3.13 -m venv .venv
.venv/bin/pip install --no-deps -r requirements-tested.txt
export PYTHONPATH=src
export TOKENIZERS_PARALLELISM=false
```

- `requirements-tested.txt` lists the exact package versions of the tested environment. Use it first.
- The second way is Poetry: `./scripts/setup-venv.sh`. It creates `.venv`, adds `PYTHONPATH` to the
  activate script and runs `poetry install`. The file `poetry.lock` is older than `pyproject.toml`.
- Set `PYTHONPATH=src` in each shell. On macOS, pip marks its `.pth` files as hidden, and Python
  skips hidden `.pth` files. Then the editable install does not work.
- Set `TOKENIZERS_PARALLELISM=false` for each command that embeds text.

Check the install:

```bash
.venv/bin/python -m secreqgen --help
.venv/bin/python -m secreqgen generate check-ollama
```

The examples below write `secreqgen`. Use `.venv/bin/python -m secreqgen` in its place when the
command `secreqgen` is not on your path.

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
- `--other-formats` (on `generate requirements` and `generate threats`) lets the search also read `.rst`, `.adoc`,
  `.asciidoc`, `.txt`, `.mdx`, `.markdown`, `.html`, `.htm`, `.pdf` and `.docx` files. The tool converts each file
  to Markdown in a temporary folder and removes the folder at the end. The conversion changes the markup. The
  words of the body text stay. Navigation, page headers and footers, metadata and the labels of notes are left
  out. PDF text is less exact: a PDF has no headings, and a word that the page splits at the end of a line stays
  split. Files that are not documentation are left out: files in dependency, build, test and template folders, and
  files such as `LICENSE.txt` or `requirements.txt`. A file that cannot be read is skipped, and the note names it. A PDF or
  Word file that someone made on purpose to be hard to read can make the conversion slow, so use documentation
  from a source that you trust. The
  `.md` files are used as they are. The command prints one note with the number of converted files. This is
  outside the tested setup, because the final test read `.md` files only. A folder with `.md` files only
  gives the same run as without the option.

### A system context for a new codebase

`secreqgen generate context FOLDER` writes a draft system context file for a codebase. Plain code
collects the facts of the folder: the file tree, the README, the file paths with security words, the
names of the files in the largest code folders, the manifests, the container files and the API files.
The writer model reads these facts and writes the context. Plain code then removes each item that names
no real part of these facts, and each technology that no file names. The code does not check that a
part says what a feature or a kind of data says: the command prints the source of each line, and a
person checks it. The security rating is the judgement of the model. The file is a draft.

The scan skips secret files by their name (for example `.env`, key files and credential files) and does
not show their names. In the files that it reads, it replaces values that look like secrets (a value
after a name such as `password` or `token`, a password in a URL, a key block, a long random text). These
rules lower the risk. They are not a proof that no secret reaches the local model.

```bash
secreqgen generate context path/to/codebase --output system_context.yaml
```

- Without `--output` the YAML text goes to the console. With it, the file must be a new file: a file that
  exists is never replaced.
- `--model NAME` changes the writer. `--product NAME` gives the name of the product.
- A run that uses a context of this kind is outside the tested setup.

### A paragraph about the product

`secreqgen generate about SOURCE` writes a draft paragraph for the option `--about-file` of
`generate threats`. The source is a codebase folder or a system context file (YAML). For a folder,
the writer model writes a system context as in `generate context`, and plain code checks it. For a
file, no model runs. Then plain code makes the paragraph from the checked context with the rule of the
test of 17 November 2026: name, description, main parts and features, about 80 words (120 at most). No
model writes the sentences of the paragraph. The command prints each line that the paragraph uses with
its source file, and the number of features that did not fit.

```bash
secreqgen generate about path/to/codebase --output about.txt
secreqgen generate about system_context.yaml --output about.txt
```

- Without `--output` the paragraph goes to the console. With it, the file must be a new file: a file
  that exists is never replaced.
- `--model NAME` changes the writer (not for a file). `--product NAME` gives the name of the product.
- The paragraph of a folder is a draft: a model chose the facts. A person checks each line against its
  source. A run with such a paragraph is outside the tested setup, because the test used paragraphs
  that were made from system context files that a person wrote.

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

## Commands

| Command | What it does |
|---|---|
| `version` | Shows the version. |
| `config` | Shows the current settings. |
| `ingest documents DIRECTORY` | Puts the documents of a folder in the vector store. Option `--type` (`policies`, `architecture` or `standards`). |
| `ingest system-context FILE` | Loads a system context YAML file into the vector store. |
| `ingest stats` | Shows statistics of the vector store. |
| `ingest clear` | Removes all data from the vector store. |
| `generate check-ollama` | Checks that Ollama runs and has the model. |
| `generate requirements INPUT_FILE` | Writes requirements for the user stories of a file. Options `--docs`, `--other-formats`, `--product`, `--context`, `--baseline`, `--format`, `--output`. |
| `generate single` | Writes requirements for one story. Options `--title`, `--story`, `--ac`, `--docs`, `--product`, `--context`, `--baseline`, `--format`. |
| `generate threats INPUT_FILE` | Writes a draft threat list for each story. Needs `--docs`. Other options: `--product`, `--story-id`, `--labels`/`--no-labels`, `--model`, `--check`/`--no-check`, `--checker-model`, `--about-file`, `--other-formats`, `--output`. |
| `generate context FOLDER` | Writes a draft system context file for a codebase. Options `--output`, `--model`, `--product`. |
| `generate about SOURCE` | Writes a draft paragraph about the product, for `--about-file`. The source is a codebase folder or a system context file. Options `--output`, `--model`, `--product`. |
| `evaluate run STORIES GROUND_TRUTH` | Runs the older RAG against zero-shot comparison. |
| `evaluate compare-runs STORIES GROUND_TRUTH` | Repeats that comparison `--runs` times (default 20) and reports totals. Option `--pilot` runs 10. |
| `evaluate compare GENERATED GROUND_TRUTH` | Matches a file of generated requirements against a ground truth file. |
| `evaluate report RESULTS_FILE` | Builds reports from a saved evaluation result. |

Run `secreqgen <group> <command> --help` to see all options of a command.

## Settings

- All settings have the prefix `SECREQGEN_`. Run `secreqgen config` to see the values in use.
- The model settings are `SECREQGEN_LLM_<NAME>`, for example `SECREQGEN_LLM_MODEL_NAME`.
  The form `SECREQGEN_LLM__<NAME>` also works.
- `SECREQGEN_LLM_BASE_URL` sets the address of the Ollama server. The default is
  `http://localhost:11434`.
- `OLLAMA_HOST` has no effect on the command line. The commands always pass the value of
  `SECREQGEN_LLM_BASE_URL` to the client, and this value has a default. To use another server,
  set `SECREQGEN_LLM_BASE_URL`.
- The retrieval similarity threshold of the vector-store path is 0.4 (`SECREQGEN_RETRIEVAL_SIMILARITY_THRESHOLD`).
  Keep it at 0.4. A much higher value makes the search return no text.

## Limits

- The threat list is a draft for a person to check. It is not a complete threat model.
- The vector-store path is the older path. It uses `ingest`, `generate` without `--docs`, and
  `evaluate`. It is not the tested setup.
- The options `--with-verification` and `--verification-threshold` of `evaluate run` are shown in
  the start panel only. They do not change the run.
- The default output folders depend on the working folder. The vector store is `./data/vectordb`,
  `generate threats` writes to `./threat_lists`, and `evaluate run` writes to
  `docs/evaluation_results` when you give no `--output`. Run the commands from the same folder
  each time, or give the paths.

## Development

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/unit -m "not slow" -q
.venv/bin/ruff check src tests
```

The tests marked `slow` need a real model server or a model download. The fast run skips them.

## More pages

- `docs/README.md`: the documents of the project and which ones to trust.
- `scripts/README.md`: the lab scripts that ran the experiments. They are a frozen record.
- Code map: `src/secreqgen/README.md`: the modules, the layer rule, the test patch points and the golden tests.
