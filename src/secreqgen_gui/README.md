# SecReqGen GUI

The GUI runs the tool in a browser on your computer. It starts the two runs of the tool (threat list, requirements), shows the steps of a run while it works, and shows the results.

## Start

```bash
pip install -r requirements-gui.txt        # one time, in the environment of the tool
PYTHONPATH=src python -m secreqgen_gui
```

Open `http://127.0.0.1:8080`. The model server (Ollama) must run and must hold the models of the settings page.

## Pages

| Page | What it does |
|---|---|
| Home | State of the model server, the two entries, the newest runs |
| Threat list, Requirements | The setup form of a run |
| Run | The steps of the story in work, the stories, the stop button |
| Result | The threat list of a story with the evidence beside each row, or the requirements of a run |
| History | All runs |
| Prompts | The tested prompt sets (read only) and your own sets |
| How it works | The parts of the tool and the steps of one story, as a replay of a finished story |
| Settings | Model server, models, data folder |

## The step diagram

The lanes are the parts of the tool. The boxes are the steps of one story, in the order of the code. On the run page, a box changes only when the tool reports a step. On the page "How it works", the numbers come from the stored result of a finished story. The button "Full screen" shows the diagram alone, for a projector.

## Settings before the start (environment variables)

| Variable | Default | What it does |
|---|---|---|
| `SECREQGEN_GUI_PORT` | `8080` | The port |
| `SECREQGEN_GUI_HOME` | `.secreqgen` in your home folder | The folder for uploads, runs and your prompt sets |
| `SECREQGEN_GUI_HOST` | `127.0.0.1` | The address that the server listens on |
| `SECREQGEN_GUI_ALLOWED_HOSTS` | empty | More host names that a request can use, separated by commas |
| `SECREQGEN_GUI_START_RUN` | empty | A JSON file that starts one run when the server starts |

## Safety

- The GUI has no login. It listens on `127.0.0.1`, so only this computer can reach it.
- WARNING: with a different address in `SECREQGEN_GUI_HOST`, every computer that can reach that address can start runs and can read the files that the GUI can read. Do this only on a network that you trust.
- The server refuses a request that names a host that is not in its list: `127.0.0.1`, `localhost`, the address of `SECREQGEN_GUI_HOST`, and the names of `SECREQGEN_GUI_ALLOWED_HOSTS`. This stops a page of a different website that tries to reach the GUI through a name that points to your computer.
- The GUI calls one server only: the model server of the settings page.
- Text of a story file, of the documentation and of a model is shown as text. It is never run as page code.
- A script or a container asks `/healthz` to check that the server runs. It does not ask a page.

## Code

`src/secreqgen_gui/` uses only `secreqgen.application` and `secreqgen.config` of the tool. The tool does not import the GUI. Tests: `tests/gui/` (they need the packages of `requirements-gui.txt`).
