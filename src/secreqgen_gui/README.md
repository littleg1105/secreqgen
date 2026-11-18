# SecReqGen GUI

The GUI runs the tool in a browser on your computer. It starts the two runs of the tool (threat list, requirements), shows the steps of a run while it works, and shows the results.

## Start

```bash
pip install -r requirements-gui.txt        # one time, in the environment of the tool
PYTHONPATH=src python -m secreqgen_gui
```

Open `http://127.0.0.1:8080`. Ollama must run and must have the models of the Settings page.

## Try it on a new product

1. Open "Requirements" or "Threat list" in the menu.
2. In "Stories", click "Use the example stories". The GUI comes with 8 generic stories (login, password reset, roles, API tokens, single sign-on, file uploads, audit log, webhooks). Your copy is `examples/generic_stories.json` in the data folder. You can change it.
3. In "Documentation", click "Browse" and pick the folder with the documentation of the product. It can be the folder of the codebase itself. The line under the field says what the search will read.
4. On "Requirements" only: click "Write one with the model" under the context file field. Pick the codebase folder and click "Write". The model writes a draft system context. Check each line against its source, change what is wrong, and click "Save and use".
5. Click "Start requirements" or "Start threat list".

"Browse" shows a list of folders in the page. It works the same way on every system and in a container.

## Outside the tested setup

The thesis tests used Markdown files and system context files that a person wrote. Two options of the GUI go beyond that. A run that uses one of them gets a tag on its pages.

| Option | What it does | Tag of the run |
|---|---|---|
| Switch "Read other file types too" (on) | The search also reads `.rst`, `.adoc`, `.txt`, `.mdx`, `.html`, `.pdf` and `.docx` files. The GUI converts them to Markdown in a copy of the folder. Your folder is never changed. Files that are no documentation are left out (dependency, test and template folders, licence files). | untested file types |
| "Write one with the model" | A model writes the system context from the files of a codebase. Plain code removes each line that names no real file, and each technology that no file names. The security rating is the judgement of the model. | generated context |

With the switch off and a context file of your own (or none), a run is the tested path.

## The forms keep their values

The two forms keep what you enter, also after a restart of the GUI. The stories, the documentation folder and the product name are the same in both forms. The values are in `forms.json` in the data folder. "Clear the form" empties the shared values and the values of that form.

## The command of a run

Under the Start button of each form, the block "Command" shows the command of `secreqgen` that starts the same run in a terminal. It changes with each option that you change. "Copy" puts it on the clipboard. The dialog "Write a system context" and the page of a run show their command too (on the page of a run: click "Command").

The GUI does not run this command. The GUI and the command call the same code. A test compares the two: the models get the same prompts from the command and from the GUI.

The command line cannot do two things that the GUI can do. Then the block says so in one line.

| In the GUI | In the command |
|---|---|
| You pick some of the stories | `generate threats` takes one story (`--story-id`) or all stories. `generate requirements` takes all stories. |
| You use a prompt set of your own | The command uses the tested prompts. |

More facts:

- An option shows only when it differs from the default of the command line. A run with the tested models and all stories gives a short command.
- `generate requirements` has no option for the model. The model is the variable `SECREQGEN_LLM_MODEL_NAME` in front of the command. An Ollama address that is not the default is the variable `SECREQGEN_LLM_BASE_URL`.
- The paragraph "About the product" goes to the command as a file (`--about-file`). The GUI writes the paragraph of an open form into the folder `about` of the data folder, a moment after you stop typing. Each text has its own file, so a command stays true when you change the paragraph later. The GUI removes these files at its next start. The paragraph of a run is in the folder of the run (`about.txt`).
- A run of some stories keeps a copy of only those stories in its folder. So the command on the page of the run starts exactly that run.
- The page of a run shows no command when the GUI cannot know it. Examples: a requirements run of an older version of the GUI (it did not store if the run had a system context file), and a run of several stories of an older version.
- The command writes its result where the command line writes it by default: `generate threats` into the folder `threat_lists` in the current folder, `generate requirements` and `generate context` to the screen. Add `--output` to change that.
- The command starts with `secreqgen`. When this name is not on your path, use `.venv/bin/python -m secreqgen` in its place (see the README of the tool).

## Pages

| Page | What it does |
|---|---|
| Home | State of Ollama, the two entries, the recent runs |
| Threat list, Requirements | The setup form of a run |
| Run | The steps of the story that is running, the stories, the stop button |
| Result | The threat list of a story with the evidence beside each threat, or the requirements of a run |
| History | All runs |
| Prompts | The tested prompt sets (read only) and your own sets |
| How it works | The parts of the tool and the steps of one story, as a replay of a finished story |
| Settings | Ollama, models, data folder |

## The step diagram

The lanes are the parts of the tool. The boxes are the steps of one story, in the order of the code. On the run page, a box changes only when the tool reports a step. On the page "How it works", the numbers come from the stored result of a finished story. The button "Full screen" shows the diagram alone, for a projector.

## Settings before the start (environment variables)

| Variable | Default | What it does |
|---|---|---|
| `SECREQGEN_GUI_PORT` | `8080` | The port |
| `SECREQGEN_GUI_HOME` | `.secreqgen` in your home folder | The data folder: uploads, runs, your prompt sets, saved system contexts, the form values and the copies of converted documentation |
| `SECREQGEN_GUI_HOST` | `127.0.0.1` | The address that the server listens on |
| `SECREQGEN_GUI_ALLOWED_HOSTS` | empty | More host names that a request can use, separated by commas |
| `SECREQGEN_GUI_START_RUN` | empty | A JSON file that starts one run when the server starts |

## Safety

- The GUI has no login. It listens on `127.0.0.1`, so only this computer can reach it.
- WARNING: with a different address in `SECREQGEN_GUI_HOST`, every computer that can reach that address can start runs and can read the files that the GUI can read. Do this only on a network that you trust.
- The server refuses a request that names a host that is not in its list: `127.0.0.1`, `localhost`, the address of `SECREQGEN_GUI_HOST`, and the names of `SECREQGEN_GUI_ALLOWED_HOSTS`. This stops a page of a different website that tries to reach the GUI through a name that points to your computer.
- The GUI calls one server only: Ollama, at the address of the Settings page.
- Text of a story file, of the documentation and of a model is shown as text. It is never run as page code.
- The scan for a system context skips secret files by their name (for example `.env`, key files, credential files) and replaces values that look like secrets before the model sees the text. These rules lower the risk. They are not a proof. The text goes to Ollama only.
- The copies of converted documentation stay in the data folder (`doc_cache`). The GUI keeps the copy of the folder in use and at most three more.
- A script or a container asks `/healthz` to check that the server runs. It does not ask a page.

## Code

`src/secreqgen_gui/` uses only `secreqgen.application` and `secreqgen.config` of the tool. The tool does not import the GUI. Tests: `tests/gui/` (they need the packages of `requirements-gui.txt`).
