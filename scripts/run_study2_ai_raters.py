#!/usr/bin/env python3
"""Run the two AI raters of the blinded review of Study 2 (thesis/analysis_lock.md, section 7).

The raters are Gemini 4 Argon (through the `agy` command) and GPT (through the `codex` command).
Each rater labels each part of the review two times. One call holds one story.

Sub-commands
  self-test   Test the message builder, the answer reader and the run logic with invented data and
              two stand-in programs. No call to a model.
  smoke       Send invented items (no item of the study) to each rater: a test of the two commands,
              of the answer form, of a long message, and of memory between calls. It can run before
              the human file is locked.
  run         Send the 15 stories of each part to each rater, two runs each, and write the eight AI
              label files. It refuses to start until the human file is locked and committed, and
              until a smoke test has passed with this script and these versions of the commands.
  status      Count the calls that are done. It prints counts only, never a label.

Rules (fixed before the first label; `ai_rater_procedure.md` in the review folder says them in plain words)
  1. The message is the only study text that a rater gets. Each call starts in a new empty folder
     outside the thesis. The tools of a rater are switched off (codex) or refused by a guard (agy;
     the guard permits only a read inside the empty folder). The two commands add their own system text. The log of each call is saved, and the text of a
     call whose log shows a tool step is not read.
  2. The message is the text below the line of `ai_rater_prompt_names.md` (part 1) or
     `ai_rater_prompt.md` (part 2). Sections 1 to 6 of the rules file and the fields of `sheet.json`
     fill its placeholders. Both raters and both runs get the same message.
  3. The log of a call decides if the call gave an answer. An answer needs: the event of a completed
     turn, no tool step and no unknown step or event in the log, a final text that is not empty,
     and, for Gemini, the model and the mode that were tested. Each other call gives "no answer"
     and is sent again, three more times at most (analysis lock 7.5). A call that was open when the
     script stopped counts as one of the four calls. If its log holds a completed answer, that
     answer is used.
  4. After a call with no answer, a short test call ("Reply with the single word OK") goes first.
     If the rater fails it, the script stops that rater and writes no X. It also stops a rater
     after three calls in a row that ended with an error of the command or at the time limit, and
     when the log of a Gemini call names another model or mode. A later start continues.
  5. An answer is read line by line. A label line has the form `R07,P,reason`. The letter must be one
     capital letter of that part. An item with no label line, with label lines that differ, or
     with a different valid letter in another line that names the item, gets X. A line with
     several items gives a letter to its first item only. A change of the letter inside the reason
     of the item's own line, or in a line with no item number, is not seen. A story with no answer
     after four calls gets X for each of its items.
  6. A story that has an answer is never sent again. A label file that exists is never written again.
     Only one start of this script can use a review folder at one time.
  7. The script prints counts and states only. It never prints a label of the study.
"""
from __future__ import annotations

import argparse
import csv
import fcntl
import hashlib
import importlib.util
import io
import json
import os
import re
import secrets
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

sys.dont_write_bytecode = True

HERE = Path(__file__).resolve().parent
THESIS = HERE.parents[2]
DEFAULT_DIR = THESIS / "coded_project" / "real_test_cases" / "study2" / "review_v2"
UTF8 = "utf-8"

RATERS = ("gemini", "gpt")
PRETTY = {"gemini": "Gemini", "gpt": "GPT"}
PARTS = ("names", "support")
RUNS = (1, 2)
LETTERS = {"names": ("P", "N", "G"), "support": ("S", "G", "U")}
MISSING = "X"
RULES_FILE = {"names": "names_rules.md", "support": "labelling_rules.md"}
PROMPT_FILE = {"names": "ai_rater_prompt_names.md", "support": "ai_rater_prompt.md"}
PLACEHOLDERS = {
    "names": ("RULES", "STORY", "ITEMS"),
    "support": ("RULES", "STORY_BLOCK", "EVIDENCE", "ITEMS", "SOURCE_FILES"),
}

# Settings of the raters. They are part of the record of each call.
GEMINI_MODEL = "gemini-4-argon-high"  # Gemini 4 Argon, thinking level "high"
GEMINI_MODE_TESTED = "request-review"  # the mode that the agy log showed in the tests of 14 November 2026
GPT_MODEL = "gpt-6-astra"
GPT_EFFORT = "high"
TIME_LIMIT_S = 1800  # one call
MAX_ATTEMPTS = 4  # the first call and three more (analysis lock 7.5)
WAIT_S = (0, 60, 300, 900)  # the wait before call 1, 2, 3 and 4 of one story
DEFAULT_JOBS = 3  # calls of one rater that run at the same time, at most
TECHNICAL_IN_A_ROW = 3  # this number of calls in a row with an error of the command stops the rater
SMOKE_PAD_KB = 90  # the long invented file of the smoke test, in thousands of characters
PREFLIGHT_PROMPT = "Reply with the single word OK and nothing else."
PREFLIGHT_LIMIT_S = 300

# The programs. The self-test replaces them with stand-in programs.
BIN = {"gemini": "agy", "gpt": "codex"}
GIT = "/usr/bin/git"
# Features of the Codex command that give the model a tool. Each is switched off.
CODEX_OFF = (
    "shell_tool", "unified_exec", "multi_agent", "plugins", "apps", "browser_use", "browser_use_external",
    "computer_use", "image_generation", "view_image", "code_mode_host", "goals", "sleep_tool",
    "skill_search", "tool_suggest", "hooks",
)
# The Codex command writes an item of the type "error" with this text into each log when the feature
# code_mode_host is off. It is a notice, not a fault of the call.
CODEX_NOTICE = "Code Mode is unavailable"
# Step types of the agy event log that cannot bring text from outside the message. The agy change
# notes say that the list of step types is closed. Each other step type means: the call is not used.
GEMINI_PLAIN_STEPS = ("user_input", "agent_response", "thinking", "system_message", "compaction", "checkpoint")
CODEX_EVENTS = ("thread.started", "turn.started", "turn.completed", "turn.failed", "item.started", "item.updated",
                "item.completed", "error")
ENV_KEEP = ("PATH", "HOME", "USER", "LOGNAME", "SHELL", "TMPDIR", "LANG", "LC_ALL", "LC_CTYPE", "TERM")
# Reasons of a call with no answer that show a fault of the command or of the service, not of the answer.
TECHNICAL = ("the time limit ended", "the command ended with the exit code", "the log shows no completed answer")
WRONG_SERVICE = "the log names"  # the start of the reason when a Gemini log names another model or mode

PRINT_LOCK = threading.RLock()
STOP = threading.Event()  # set by Ctrl-C or a stop signal: no new call starts
LIVE: set[int] = set()  # process groups of the calls that are open
LIVE_LOCK = threading.RLock()  # the stop handler runs in the main thread and can meet its own lock


class Refuse(Exception):
    """A plain reason why a command stops. Nothing is changed when it is raised."""


class RaterDown(Exception):
    """A rater does not work now (rule 4 at the top of this file). Its calls stop, and no X is written."""


def say(text: str) -> None:
    with PRINT_LOCK:
        try:
            print(text.encode(UTF8, "backslashreplace").decode(UTF8), flush=True)
        except (BrokenPipeError, OSError, ValueError):  # the output was closed (for example "run | head"): the run goes on
            try:
                sys.stdout = open(os.devnull, "w")  # noqa: SIM115
            except OSError:
                pass


def now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode(UTF8, "backslashreplace")).hexdigest()


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_atomic(path: Path, text: str) -> None:
    """Write through a temporary file in the same folder, so that a stop never leaves half a file.
    A character that UTF-8 cannot hold (a lone surrogate from a log) is written as an escape."""
    tmp = path.with_name(f"{path.name}.tmp{os.getpid()}_{threading.get_ident()}")
    with open(tmp, "w", encoding=UTF8, errors="backslashreplace", newline="") as fh:
        fh.write(text)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def write_json(path: Path, data) -> None:
    write_atomic(path, json.dumps(data, indent=1, ensure_ascii=False) + "\n")


def read_json(path: Path):
    """The content of a JSON file, or None when the file is not there or cannot be read."""
    try:
        return json.loads(path.read_text(encoding=UTF8))
    except (OSError, ValueError):
        return None


def read_text(path: Path) -> str:
    try:
        return path.read_bytes().decode(UTF8, errors="replace")
    except OSError:
        return ""


def _d(value) -> dict:
    return value if isinstance(value, dict) else {}


def load_scorer():
    """The scoring tool, as a module: it knows the lock record and the form of a label file."""
    path = HERE / "score_study2_review.py"
    spec = importlib.util.spec_from_file_location("study2_review_scorer", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def label_file_name(part: str, rater: str, run: int) -> str:
    return f"labels_{part}_{rater}_run{run}.csv"


class RunLock:
    """Only one start of this script can use a folder at one time. The lock file stays on disk:
    if a start removed it, two later starts could each hold a lock on a different file."""

    def __init__(self, root: Path):
        self.path = root / "run.lock"
        self.fh = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        ignore = self.path.parent / ".gitignore"
        wanted = "run.lock\n*.tmp*\n*.message.txt\n"
        if not ignore.exists() or ignore.read_text(encoding=UTF8) != wanted:
            ignore.write_text(wanted, encoding=UTF8)
        self.fh = open(self.path, "a+", encoding=UTF8)  # noqa: SIM115
        try:
            fcntl.flock(self.fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.fh.close()
            raise Refuse("Another start of this script (run or smoke) uses this folder now. Wait until it ends, or stop it "
                         "with Ctrl-C. No call was made.") from None
        self.fh.seek(0)
        self.fh.truncate()
        self.fh.write(f"process {os.getpid()}, started {now_utc()}\n")
        self.fh.flush()
        return self

    def __exit__(self, *exc):
        fcntl.flock(self.fh, fcntl.LOCK_UN)
        self.fh.close()


def lock_is_held(root: Path) -> bool:
    path = root / "run.lock"
    if not path.exists():
        return False
    with open(path, "a+", encoding=UTF8) as fh:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return True
        fcntl.flock(fh, fcntl.LOCK_UN)
    return False


def kill_live() -> None:
    with LIVE_LOCK:
        pids = list(LIVE)
    for pid in pids:
        try:
            os.killpg(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass


def install_stop_handlers() -> None:
    def handler(signum, frame):
        if not STOP.is_set():
            say("stop: no new call starts, and each call that this start opened is ended (it counts as a call)")
        STOP.set()
        kill_live()
    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, handler)


def clean_tmp(*folders: Path) -> None:
    """Remove temporary files that a killed start left."""
    for folder in folders:
        if folder.is_dir():
            for p in folder.rglob("*.tmp[0-9]*_[0-9]*"):
                try:
                    p.unlink()
                except OSError:
                    pass


# ---------------------------------------------------------------------------
# the message
# ---------------------------------------------------------------------------
def sections_1_to_6(rules_text: str, name: str) -> str:
    """The text from the heading '## 1.' up to the heading '## 7.' (which is not included), without change."""
    start = re.search(r"^## 1\. ", rules_text, flags=re.M)
    end = re.search(r"^## 7\. ", rules_text, flags=re.M)
    if not start or not end or end.start() <= start.start():
        raise Refuse(f"{name}: the headings '## 1.' and '## 7.' were not found in this order.")
    return rules_text[start.start():end.start()].rstrip() + "\n"


def template_below_line(doc_text: str, name: str) -> str:
    """The instruction text: all that follows the first line that holds only '---'."""
    m = re.search(r"^---[ \t]*$", doc_text, flags=re.M)
    if not m:
        raise Refuse(f"{name}: the line '---' that starts the instruction text was not found.")
    return doc_text[m.end():].lstrip("\n")


def fill(template: str, parts: dict[str, str], expected: tuple[str, ...], name: str) -> str:
    """Put each part in the place of its placeholder, in one pass. Text that was put in is never scanned again."""
    found = re.findall(r"\{\{([A-Z_]+)\}\}", template)
    if sorted(found) != sorted(expected):
        raise Refuse(f"{name}: the placeholders must be exactly {sorted(expected)}, each one time. Found: {sorted(found)}.")
    return re.sub(r"\{\{([A-Z_]+)\}\}", lambda m: parts[m.group(1)].rstrip("\n"), template)


def items_block(items: list[dict], part: str) -> str:
    """Each item under the heading '### R00'. Part 1 adds the table, under the same label as the human sheet."""
    out = []
    for it in items:
        block = f"### {it['item']}\n\n{it['text'].rstrip()}\n"
        if part == "names":
            block += f"\n**Names in this requirement.**\n\n{it['names_table'].rstrip()}\n"
        out.append(block)
    return "\n".join(out)


def source_files_block(files: list[dict]) -> str:
    if not files:
        return "This story has no source file."
    out = []
    for f in files:
        out.append(f"===== Start of the file {f['name']} =====\n{f['text'].rstrip()}\n===== End of the file {f['name']} =====\n")
    return "\n".join(out)


def build_prompt(part: str, story: dict, rules: str, template: str) -> str:
    if part == "names":
        parts = {"RULES": rules, "STORY": story["names_story"], "ITEMS": items_block(story["items"], part)}
    else:
        parts = {"RULES": rules, "STORY_BLOCK": story["story_block"], "EVIDENCE": story["evidence"],
                 "ITEMS": items_block(story["items"], part), "SOURCE_FILES": source_files_block(story["source_files"])}
    return fill(template, parts, PLACEHOLDERS[part], PROMPT_FILE[part])


def load_inputs(d: Path, sheet: dict | None = None) -> dict:
    """The sheet, the rules and the instruction texts of the folder, and one message for each part and story."""
    if sheet is None:
        p = d / "sheet.json"
        if not p.is_file():
            raise Refuse(f"sheet.json is missing in {d}")
        sheet = json.loads(p.read_text(encoding=UTF8))
    stories = sheet["stories"]
    ids = [it["item"] for st in stories for it in st["items"]]
    if len(set(ids)) != len(ids):
        raise Refuse("sheet.json holds an item number two times.")
    if any(not re.fullmatch(r"R\d{2}", i) for i in ids):
        raise Refuse("sheet.json holds an item number that is not of the form R00.")
    prompts: dict[str, dict[str, str]] = {}
    for part in PARTS:
        for fname in (RULES_FILE[part], PROMPT_FILE[part]):
            if not (d / fname).is_file():
                raise Refuse(f"{fname} is missing in {d}")
        rules = sections_1_to_6((d / RULES_FILE[part]).read_text(encoding=UTF8), RULES_FILE[part])
        template = template_below_line((d / PROMPT_FILE[part]).read_text(encoding=UTF8), PROMPT_FILE[part])
        prompts[part] = {st["story_id"]: build_prompt(part, st, rules, template) for st in stories}
    return {"sheet": sheet, "stories": stories, "ids": ids, "prompts": prompts}


def instrument_hashes(d: Path) -> dict[str, str]:
    """The hashes of the rules and the instruction texts: the smoke test and the run must use the same files."""
    names = sorted(set(RULES_FILE.values()) | set(PROMPT_FILE.values()))
    return {f: (sha256_file(d / f) if (d / f).is_file() else "missing") for f in names}


def check_against_manifest(d: Path, runner_change: str = "") -> dict:
    """The sheet, the rules, the instruction texts and the tools must be the files that manifest.json records.
    Return a note for the record when the script itself differs and a reason was given."""
    p = d / "manifest.json"
    if not p.is_file():
        raise Refuse("manifest.json is missing. The run needs the record that was made before the first label.")
    m = json.loads(p.read_text(encoding=UTF8))
    recorded = dict(m.get("sha256") or {})
    recorded["labelling_rules.md"] = m.get("labelling_rules_sha256")
    for fname in ("sheet.json", "names_rules.md", "labelling_rules.md", "ai_rater_prompt_names.md", "ai_rater_prompt.md",
                  "ai_rater_procedure.md"):
        if not (d / fname).is_file() or recorded.get(fname) != sha256_file(d / fname):
            raise Refuse(f"{fname} is not the file that manifest.json records. The run does not start. Stop and ask for help.")
    if recorded.get("scoring_tool") != sha256_file(HERE / "score_study2_review.py"):
        raise Refuse("The scoring tool is not the file that manifest.json records (scoring_tool). The run does not start. "
                     "Stop and ask for help.")
    if not recorded.get("ai_rater_runner"):
        raise Refuse("manifest.json holds no hash of this script (ai_rater_runner). The record of the review folder must be "
                     "made again before the first label. The run does not start.")
    actual = sha256_file(Path(__file__))
    if recorded["ai_rater_runner"] != actual:
        if not runner_change.strip():
            raise Refuse("This script is not the file that manifest.json records (ai_rater_runner). The run does not start. "
                         "If the script had to change after the first label: write a dated line in the analysis lock first, "
                         "then start with --allow-changed-runner and the reason.")
        return {"reason": runner_change.strip(), "recorded_sha256": recorded["ai_rater_runner"], "actual_sha256": actual}
    return {}


# ---------------------------------------------------------------------------
# one call to a rater
# ---------------------------------------------------------------------------
def command(rater: str, prompt: str, cwd: Path) -> tuple[list[str], str | None]:
    """The command line, and the text for its standard input (None when the message is an argument)."""
    if rater == "gemini":
        return [BIN["gemini"], "-p", prompt, "--mode", "auto", "--sandbox", "--model", GEMINI_MODEL,
                "--output-format", "stream-json"], None
    argv = [BIN["gpt"], "exec", "-", "-C", str(cwd), "-s", "read-only", "--skip-git-repo-check", "--ignore-user-config",
            "--ignore-rules", "--ephemeral", "-m", GPT_MODEL, "-c", f'model_reasoning_effort="{GPT_EFFORT}"',
            "-c", 'web_search="disabled"']
    for feature in CODEX_OFF:
        argv += ["--disable", feature]
    argv.append("--json")
    return argv, prompt


def command_for_record(rater: str) -> list[str]:
    argv, _ = command(rater, "<THE MESSAGE>", Path("<A NEW EMPTY FOLDER>"))
    return argv


def child_env(rater: str) -> dict[str, str]:
    """A small environment: no variable of this session reaches the rater."""
    env = {k: os.environ[k] for k in ENV_KEEP if k in os.environ}
    if rater == "gemini":
        env["AGENT_COUNCIL_SEAT"] = "1"  # switches on the guard of the agy command
    return env


def guard_files() -> dict[str, Path]:
    config = Path.home() / ".gemini" / "config"
    return {"hooks.json": config / "hooks.json", "agent-council-seat-guard.py": config / "agent-council-seat-guard.py"}


def gemini_guard_ready() -> bool:
    """The guard that refuses each tool call of the agy command outside its (empty) folder must be installed."""
    files = guard_files()
    try:
        return "agent-council-seat-guard" in files["hooks.json"].read_text() and files["agent-council-seat-guard.py"].is_file()
    except OSError:
        return False


def guard_hashes() -> dict[str, str]:
    return {name: (sha256_file(p) if p.is_file() else "missing") for name, p in guard_files().items()}


def cli_version(rater: str) -> str:
    try:
        r = subprocess.run([BIN[rater], "--version"], capture_output=True, text=True, timeout=20, env=child_env(rater),
                           stdin=subprocess.DEVNULL, cwd=tempfile.gettempdir())
        text = (r.stdout or r.stderr).strip()
        return text.splitlines()[0] if text else "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def process_line(pid) -> str:
    """The start time and the command line of a process, as ps prints them. '' when the process is not there."""
    if not isinstance(pid, int) or isinstance(pid, bool) or not 1 < pid < 2 ** 31:
        return ""
    try:
        r = subprocess.run(["/bin/ps", "-p", str(pid), "-o", "lstart=", "-o", "command="], capture_output=True, text=True,
                           timeout=10)
    except (OSError, subprocess.SubprocessError, ValueError, OverflowError):
        return ""
    return r.stdout.strip()


def run_cli(rater: str, prompt: str, time_limit: float, out_path: Path, err_path: Path, on_start=None) -> dict:
    """Run one call in a new empty folder. The command writes its log straight into out_path, so a log
    is on disk also when this script stops. A message for standard input comes from a file, so the
    command reads the whole message also when this script dies. Return the facts of the call."""
    started, t0 = now_utc(), time.monotonic()
    code: int | None = None
    timed_out = False
    cwd = msg_path = fo = fe = fin = None
    try:
        cwd = Path(tempfile.mkdtemp(prefix="rater_"))
        if THESIS in cwd.resolve().parents or cwd.resolve() == THESIS:
            raise OSError("the temporary folder is inside the thesis folder; set TMPDIR to a folder outside it")
        argv, stdin_text = command(rater, prompt, cwd)
        if stdin_text is not None:
            msg_path = out_path.with_name(out_path.name + ".message.txt")
            write_atomic(msg_path, stdin_text)
            fin = open(msg_path, "rb")  # noqa: SIM115
        fo = open(out_path, "wb")  # noqa: SIM115
        fe = open(err_path, "wb")  # noqa: SIM115
    except OSError as e:  # a fault of this computer before the command started: nothing was sent
        for fh in (fo, fe, fin):
            if fh is not None:
                fh.close()
        if msg_path is not None:
            msg_path.unlink(missing_ok=True)
        if cwd is not None:
            shutil.rmtree(cwd, ignore_errors=True)
        return {"started": False, "local_error": True, "error": str(e), "started_utc": started}
    with fo, fe:
        try:
            proc = subprocess.Popen(argv, cwd=cwd, env=child_env(rater), stdout=fo, stderr=fe, start_new_session=True,
                                    stdin=fin if fin is not None else subprocess.DEVNULL)
        except OSError as e:  # the program is not there, or cannot start
            shutil.rmtree(cwd, ignore_errors=True)
            if msg_path is not None:
                msg_path.unlink(missing_ok=True)
            return {"started": False, "local_error": False, "error": str(e), "started_utc": started}
        finally:
            if fin is not None:
                fin.close()
        with LIVE_LOCK:
            LIVE.add(proc.pid)
        try:
            try:
                if on_start is not None:
                    on_start(proc.pid, started)
            except BaseException:  # the record of the start could not be written: end the command, then report
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    proc.kill()
                if msg_path is not None:
                    msg_path.unlink(missing_ok=True)
                raise
            if STOP.is_set():  # a stop came while this command started: end this command only
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    pass
            try:
                proc.wait(timeout=time_limit)
            except subprocess.TimeoutExpired:
                timed_out = True
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    proc.kill()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    pass
            code = proc.returncode
        finally:
            with LIVE_LOCK:
                LIVE.discard(proc.pid)
            files_left = sorted(p.name for p in cwd.iterdir()) if cwd.is_dir() else []
            shutil.rmtree(cwd, ignore_errors=True)
    if msg_path is not None and msg_path.exists():
        msg_path.unlink()  # the message is in the folder prompts; this copy was only the input of the command
    return {"started": True, "pid": proc.pid, "exit_code": code, "timed_out": timed_out, "stopped": STOP.is_set(),
            "started_utc": started, "ended_utc": now_utc(), "seconds": round(time.monotonic() - t0, 1),
            "files_left_in_folder": files_left}


def parse_jsonl(text: str) -> list:
    events = []
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            events.append(json.loads(line))
        except ValueError:
            continue
    return events


def read_log(rater: str, stdout: str) -> dict:
    """Read the event log of one call: the final text, the tool steps, what is not in the known form,
    the model name and the token counts."""
    events = [e for e in parse_jsonl(stdout) if isinstance(e, dict)]
    answer, completed, model, usage, mode, tools_listed = "", False, None, None, None, None
    tool_steps: list[str] = []
    unknown: list[str] = []
    notices: list[str] = []
    if rater == "gemini":
        seen: set = set()
        results = []
        conversations: set = set()
        for e in events:
            kind = e.get("event")
            if e.get("conversation_id"):
                conversations.add(str(e.get("conversation_id")))
            if kind == "init":
                init = _d(e.get("init"))
                model, mode = init.get("model"), init.get("permission_mode")
                tools_listed = len(init["tools"]) if isinstance(init.get("tools"), list) else None
            elif kind == "step_update":
                su = _d(e.get("step_update"))
                if su.get("conversation_id"):
                    conversations.add(str(su.get("conversation_id")))
                step_type = su.get("step_type")
                if step_type == "tool" or su.get("tool_name") or su.get("tool_info"):
                    ident = su.get("step_index") if su.get("step_index") is not None else len(tool_steps)
                    if str(ident) not in seen:
                        seen.add(str(ident))
                        tool_steps.append(str(su.get("tool_name") or "tool"))
                elif step_type not in GEMINI_PLAIN_STEPS:
                    unknown.append(f"step of the type {step_type!r}")
            elif kind == "result":
                results.append(_d(e.get("result")))
            else:
                unknown.append(f"event {kind!r}")
        if len(results) > 1:
            unknown.append("more than one result event")
        if results:
            res = results[-1]
            if res.get("conversation_id"):
                conversations.add(str(res.get("conversation_id")))
            answer = res.get("response") if isinstance(res.get("response"), str) else ""
            usage = res.get("usage")
            completed = res.get("status") == "SUCCESS" and not res.get("finished_reason") and not res.get("error")
            if res.get("error") or res.get("finished_reason"):
                notices.append(str(res.get("error") or res.get("finished_reason"))[:300])
            if str(res.get("num_turns", "1")) != "1":
                unknown.append(f"num_turns is {res.get('num_turns')!r}")
        if len(conversations) > 1:
            unknown.append("more than one conversation")
    else:
        messages: list[str] = []
        seen_ids: set = set()
        failed = False
        for e in events:
            kind = e.get("type")
            item = _d(e.get("item"))
            if kind in ("item.started", "item.updated", "item.completed"):
                what = item.get("type")
                if what == "agent_message":
                    if kind == "item.completed":
                        messages.append(item.get("text") if isinstance(item.get("text"), str) else "")
                elif what == "reasoning":
                    pass
                elif what == "error":
                    text = str(item.get("message") or "")
                    if not text.startswith(CODEX_NOTICE):
                        notices.append(text[:300])
                else:
                    ident = str(item.get("id") or (what, len(tool_steps)))
                    if ident not in seen_ids:
                        seen_ids.add(ident)
                        tool_steps.append(str(item.get("tool") or what or "tool"))
            elif kind == "turn.completed":
                completed = True
                usage = e.get("usage")
            elif kind == "turn.failed":
                failed = True
                notices.append(json.dumps(e)[:300])
            elif kind == "error":  # a notice of the command (for example a retry); a failed turn has its own event
                notices.append(json.dumps(e)[:300])
            elif kind not in CODEX_EVENTS:
                unknown.append(f"event {kind!r}")
        completed = completed and not failed  # a failed turn stays failed
        answer = messages[-1] if messages else ""  # the last message of the call is its final text
        model = None  # the Codex log does not name the model; the command line sets it
    return {"answer": answer, "completed": completed, "tool_steps": tool_steps, "unknown": unknown, "model_in_log": model,
            "permission_mode": mode, "tools_listed": tools_listed, "usage": usage, "n_events": len(events), "notices": notices}


def safe_read_log(rater: str, stdout: str) -> dict:
    """read_log, but a log that cannot be read is a log with no answer (the log itself stays on disk)."""
    try:
        return read_log(rater, stdout)
    except Exception as e:  # noqa: BLE001
        return {"answer": "", "completed": False, "tool_steps": [], "unknown": [f"the log could not be read ({type(e).__name__})"],
                "model_in_log": None, "permission_mode": None, "tools_listed": None, "usage": None, "n_events": 0, "notices": []}


def judge(res: dict, log: dict, rater: str = "", check_mode: bool = False) -> tuple[str, str]:
    """'answer' or 'no answer', with the reason. The log decides (rule 3 at the top of this file)."""
    form = "the log is not in the known form (" + "; ".join(log["unknown"][:3]) + "), so the text of this call is not read"
    tool = "the log shows a tool step, so the text of this call is not read"
    if log["completed"] and log["answer"].strip():
        if log["tool_steps"]:
            return "no answer", tool
        if log["unknown"]:
            return "no answer", form
        if rater == "gemini" and check_mode:
            if log["model_in_log"] != GEMINI_MODEL:
                named = f"the model {log['model_in_log']!r}, not {GEMINI_MODEL}" if log["model_in_log"] else "no model"
                return "no answer", f"{WRONG_SERVICE} {named}"
            if log["permission_mode"] not in (None, GEMINI_MODE_TESTED):
                return "no answer", f"{WRONG_SERVICE} the mode {log['permission_mode']!r}, which was not tested"
        return "answer", ""
    # No completed answer. The way the command ended comes first: it shows a fault of the command or the service.
    if res.get("stopped"):
        return "no answer", "the script stopped during this call"
    if res.get("timed_out"):
        return "no answer", "the time limit ended"
    if res.get("exit_code") not in (0, None):
        return "no answer", f"the command ended with the exit code {res['exit_code']}"
    if log["tool_steps"]:
        return "no answer", tool
    if log["unknown"]:
        return "no answer", form
    if log["completed"]:
        return "no answer", "the answer is empty"
    return "no answer", "the log shows no completed answer"


# ---------------------------------------------------------------------------
# reading an answer
# ---------------------------------------------------------------------------
# A label line: an optional list number ("1.", "1)" or "(1)"), then marks (characters that are not
# letters, digits or commas), then the item number, a comma, a word of letters, and then the end of
# the line or a comma with the reason. No part of the pattern can match a comma in two places, so
# its time grows with the length of the line only.
LABEL_LINE = re.compile(r"^\s*(?:\(?\d{1,3}[.)]\s*)?[^A-Za-z0-9,]*(R\d{2})[^A-Za-z0-9,]*,[^A-Za-z0-9,]*([A-Za-z]+)[^A-Za-z0-9,]*(?:,(.*))?$")
ITEM_NUMBER = re.compile(r"(?<![A-Za-z0-9])R\d{2}(?![A-Za-z0-9])")
# A capital letter that stands alone: no letter, digit or one of the signs + - / . : directly before it,
# and no letter, digit or one of the signs + - / directly after it (so the P of "P-256" does not count).
LONE_CAPITAL = re.compile(r"(?<![A-Za-z0-9+\-/.:])[A-Z](?![A-Za-z0-9+\-/])")


def clean_note(text: str) -> str:
    text = re.sub(r"\s+", " ", text).replace(",", ";").replace('"', "'").strip()
    return text[:300]


def parse_answer(answer: str, item_ids: list[str], letters: tuple[str, ...]) -> dict:
    """One letter for each item of the story, or X. See rule 5 in the text at the top of this file."""
    words: dict[str, list[str]] = {i: [] for i in item_ids}  # the word of each label line of the item
    reasons: dict[str, str] = {}
    elsewhere: dict[str, set[str]] = {i: set() for i in item_ids}  # valid letters in other lines that name the item
    other_items = 0
    for raw in answer.splitlines():
        line = raw.strip()
        m = LABEL_LINE.match(line)
        own, rest = None, line
        if m:
            own, rest = m.group(1), m.group(3) or ""  # for the other items, only the reason of this line counts
            if own in words:
                words[own].append(m.group(2))
                reasons.setdefault(own, clean_note(rest))
            else:
                other_items += 1
        named = {x for x in ITEM_NUMBER.findall(rest) if x in elsewhere and x != own}
        if named:
            valid_here = {x for x in LONE_CAPITAL.findall(rest) if x in letters}
            for item in named:
                elsewhere[item] |= valid_here
    labels, notes, unclear = {}, {}, []
    for i in item_ids:
        distinct = set(words[i])
        if len(distinct) == 1 and next(iter(distinct)) in letters and elsewhere[i] <= distinct:
            labels[i], notes[i] = words[i][0], reasons.get(i, "")
        else:
            labels[i] = MISSING
            if not words[i]:
                notes[i] = "no label line in the answer"
            elif len(distinct) > 1 or not elsewhere[i] <= distinct:
                notes[i] = "two different letters in the answer"
                unclear.append(i)
            else:
                notes[i] = "no valid letter in the label line"
    return {"labels": labels, "notes": notes, "n_valid": sum(v != MISSING for v in labels.values()),
            "lines_for_other_items": other_items, "items_with_two_different_letters": unclear}


# ---------------------------------------------------------------------------
# one story of one rater, part and run
# ---------------------------------------------------------------------------
class RaterState:
    def __init__(self):
        self.gate_lock = threading.Lock()  # one short test call at a time
        self.mu = threading.Lock()  # guards the fields below
        self.down = ""  # the reason why this rater stopped
        self.fail_seq = 0  # the number of calls with no answer so far
        self.checked_seq = 0  # the value of fail_seq that the last short test call covered
        self.technical_in_a_row = 0


class Context:
    def __init__(self, root: Path, prompts: dict, stories: list[dict], waits=WAIT_S, time_limit: float = TIME_LIMIT_S,
                 versions: dict | None = None, test_limit: float = PREFLIGHT_LIMIT_S, check_mode: bool = True):
        self.root = root  # the folder ai_runs (or a smoke or test folder)
        self.prompts = prompts
        self.stories = {st["story_id"]: st for st in stories}
        self.order = [st["story_id"] for st in stories]
        self.waits = waits
        self.time_limit = time_limit
        self.test_limit = test_limit
        self.check_mode = check_mode
        self.versions = versions or {}
        self.state = {r: RaterState() for r in RATERS}
        self.start_id = f"{now_utc()}_{secrets.token_hex(3)}"
        self.runner_sha256 = sha256_file(Path(__file__))

    def story_dir(self, rater: str, part: str, run: int, story_id: str) -> Path:
        return self.root / "calls" / rater / f"{part}_run{run}" / story_id

    def item_ids(self, story_id: str) -> list[str]:
        return [it["item"] for it in self.stories[story_id]["items"]]


def set_down(ctx: Context, rater: str, reason: str) -> None:
    st = ctx.state[rater]
    with st.mu:
        if not st.down:
            st.down = reason


def note_failure(ctx: Context, rater: str, reason: str) -> None:
    """A call gave no answer: the short test call goes before the next call of this rater."""
    st = ctx.state[rater]
    with st.mu:
        st.fail_seq += 1
        st.technical_in_a_row = st.technical_in_a_row + 1 if reason.startswith(TECHNICAL) else 0
        if st.technical_in_a_row >= TECHNICAL_IN_A_ROW and not st.down:
            st.down = f"{TECHNICAL_IN_A_ROW} calls in a row ended with an error of the command or at the time limit"


def note_answer(ctx: Context, rater: str) -> None:
    st = ctx.state[rater]
    with st.mu:
        st.technical_in_a_row = 0


def short_test(ctx: Context, rater: str) -> tuple[bool, str]:
    """One short call with no study text. It shows if the rater works. It is not a call of a story."""
    folder = ctx.root / "preflight"
    folder.mkdir(parents=True, exist_ok=True)
    stamp = f"{now_utc().replace(':', '').replace('-', '')}_{secrets.token_hex(2)}"
    out_path, err_path = folder / f"{rater}_{stamp}.events.jsonl", folder / f"{rater}_{stamp}.stderr.txt"
    res = run_cli(rater, PREFLIGHT_PROMPT, ctx.test_limit, out_path, err_path)
    if not res["started"]:
        why = f"the command did not start ({res['error']})"
        write_json(folder / f"{rater}_{stamp}.json", {"rater": rater, "started_utc": res["started_utc"], "passed": False, "reason": why})
        return False, why
    log = safe_read_log(rater, read_text(out_path))
    outcome, why = judge(res, log, rater, ctx.check_mode)
    if outcome == "answer" and log["answer"].strip().strip(".").upper() != "OK":
        why = "the answer was not the word OK"
    passed = outcome == "answer" and why == ""
    write_json(folder / f"{rater}_{stamp}.json", {
        "rater": rater, "started_utc": res["started_utc"], "seconds": res["seconds"], "exit_code": res["exit_code"],
        "passed": passed, "reason": why, "model_in_log": log["model_in_log"], "mode_in_log": log["permission_mode"],
        "tools_listed_in_log": log["tools_listed"], "tool_steps": log["tool_steps"],
        "stderr_first_line": (read_text(err_path).strip().splitlines() or [""])[0][:300]})
    return passed, why


def gate(ctx: Context, rater: str) -> None:
    """Before each call: stop when the rater is down; after a call with no answer, send the short test call first."""
    st = ctx.state[rater]
    with st.gate_lock:
        with st.mu:
            down, seen = st.down, st.fail_seq
            needed = st.fail_seq > st.checked_seq
        if down:
            raise RaterDown(down)
        if needed and not STOP.is_set():
            ok, why = short_test(ctx, rater)
            if STOP.is_set():
                return
            if not ok:
                reason = f"the short test call failed after a call with no answer ({why})"
                set_down(ctx, rater, reason)
                raise RaterDown(reason)
            with st.mu:  # a failure that came during the test is newer than "seen": it gets its own test
                st.checked_seq = max(st.checked_seq, seen)
                down = st.down  # another call can have stopped the rater while the test ran
            if down:
                raise RaterDown(down)


def call_is_alive(info: dict, rater: str) -> bool:
    """True when the command of a call of an earlier start still runs: the same process number, the
    same start time of the process, and the name of the rater command in its command line."""
    line = process_line(info.get("pid"))
    stamp = info.get("process_started")
    return bool(line) and isinstance(stamp, str) and len(stamp) >= 20 and line.startswith(stamp) and Path(BIN[rater]).name in line


def base_meta(ctx: Context, rater: str, part: str, run: int, story_id: str, attempt: int, prompt: str, log: dict) -> dict:
    return {
        "rater": rater, "part": part, "run": run, "story_id": story_id, "attempt": attempt,
        "tool_steps": log["tool_steps"], "not_in_the_known_form": log["unknown"], "notices": log["notices"],
        "events_in_log": log["n_events"],
        "model_requested": GEMINI_MODEL if rater == "gemini" else GPT_MODEL,
        "effort_requested": "part of the model name" if rater == "gemini" else GPT_EFFORT,
        "model_in_log": log["model_in_log"], "mode_in_log": log["permission_mode"], "tools_listed_in_log": log["tools_listed"],
        "usage": log["usage"], "prompt_sha256": sha256_text(prompt), "prompt_characters": len(prompt),
        "command": command_for_record(rater),
    }


def recover_call(ctx: Context, rater: str, part: str, run: int, story_id: str, attempt: int, prompt: str) -> dict | None:
    """A call that an earlier start opened and did not record. It counts. If its log holds a completed
    answer, the answer is used. Return None when a stop came while its command still ran."""
    sd = ctx.story_dir(rater, part, run, story_id)
    info = _d(read_json(sd / f"attempt{attempt}.started"))
    log_path = sd / f"attempt{attempt}.events.jsonl"
    if call_is_alive(info, rater):
        # The command of the earlier start still runs. Give it the rest of its time limit, then end it.
        try:
            began = datetime.strptime(str(info.get("started_utc")), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()
        except ValueError:
            began = time.time()
        left = max(0.0, began + ctx.time_limit - time.time())
        say(f"[{PRETTY[rater]}, {part}, run {run}, {story_id}] waiting for call {attempt} of an earlier start (process "
            f"{info.get('pid')}), at most {max(1, round(left / 60))} minute(s). Ctrl-C stops the wait; the call then stays open for the next start.")
        while call_is_alive(info, rater) and time.time() < began + ctx.time_limit:
            if STOP.wait(1):
                return None
        if call_is_alive(info, rater):
            try:
                os.killpg(info["pid"], signal.SIGKILL)
            except (ProcessLookupError, PermissionError, OverflowError):
                pass
            time.sleep(0.5)
    log = safe_read_log(rater, read_text(log_path))
    outcome, reason = judge({"stopped": True}, log, rater, ctx.check_mode)
    meta = base_meta(ctx, rater, part, run, story_id, attempt, prompt, log)
    meta.update({"started_utc": info.get("started_utc"), "start_id": info.get("start_id"),
                 "command_version": info.get("command_version", "not recorded"), "runner_sha256": info.get("runner_sha256"),
                 "outcome": outcome, "reason": reason, "recorded_utc": now_utc(), "recorded_by_a_later_start": True,
                 "exit_code": "not known", "events_sha256": sha256_file(log_path) if log_path.is_file() else None})
    if log_path.is_file():
        meta["log_last_changed_utc"] = datetime.fromtimestamp(log_path.stat().st_mtime, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if outcome == "answer":
        write_atomic(sd / "answer.txt", log["answer"])
    write_json(sd / f"attempt{attempt}.meta.json", meta)
    log_path.with_name(log_path.name + ".message.txt").unlink(missing_ok=True)  # the copy of the message of that call
    return meta


def one_call(ctx: Context, rater: str, part: str, run: int, story_id: str, attempt: int, prompt: str) -> dict:
    sd = ctx.story_dir(rater, part, run, story_id)
    out_path, err_path = sd / f"attempt{attempt}.events.jsonl", sd / f"attempt{attempt}.stderr.txt"
    marker = sd / f"attempt{attempt}.started"
    facts = {"started_utc": now_utc(), "start_id": ctx.start_id, "runner_sha256": ctx.runner_sha256,
             "command_version": ctx.versions.get(rater, "unknown")}
    write_json(marker, facts)  # before the command starts, so that each command that starts counts

    state = {"command_started": False}

    def on_start(pid: int, started: str) -> None:
        state["command_started"] = True
        write_json(marker, {**facts, "started_utc": started, "pid": pid, "process_started": process_line(pid)[:24]})

    try:
        res = run_cli(rater, prompt, ctx.time_limit, out_path, err_path, on_start)
    except BaseException:
        if not state["command_started"]:  # the fault came before the command started: this was not a call
            marker.unlink(missing_ok=True)
        raise
    if not res["started"]:
        for p in (marker, out_path, err_path):  # the command did not start: nothing was sent, this was not a call
            if p.exists():
                p.unlink()
        if res.get("local_error"):
            raise OSError(f"a call could not be prepared on this computer ({res['error']})")
        reason = f"the command did not start ({res['error']})"
        set_down(ctx, rater, reason)
        raise RaterDown(reason)
    log = safe_read_log(rater, read_text(out_path))
    outcome, reason = judge(res, log, rater, ctx.check_mode)
    meta = base_meta(ctx, rater, part, run, story_id, attempt, prompt, log)
    meta.update({"started_utc": res["started_utc"], "ended_utc": res["ended_utc"], "seconds": res["seconds"],
                 "start_id": ctx.start_id, "command_version": ctx.versions.get(rater, "unknown"),
                 "runner_sha256": ctx.runner_sha256, "exit_code": res["exit_code"], "timed_out": res["timed_out"],
                 "stopped": res["stopped"], "outcome": outcome, "reason": reason,
                 "files_left_in_folder": res["files_left_in_folder"], "events_sha256": sha256_file(out_path),
                 "stderr_first_line": (read_text(err_path).strip().splitlines() or [""])[0][:300]})
    if outcome == "answer":
        write_atomic(sd / "answer.txt", log["answer"])
    write_json(sd / f"attempt{attempt}.meta.json", meta)
    if reason.startswith(WRONG_SERVICE):  # a state of the service, not of this story: the rater stops (the call counts)
        set_down(ctx, rater, reason)
        raise RaterDown(reason)
    return meta


def valid_final(final, item_ids: list[str], letters: tuple[str, ...]) -> bool:
    return (isinstance(final, dict) and final.get("outcome") in ("answer", "no answer") and final.get("items") == item_ids
            and isinstance(final.get("labels"), dict) and isinstance(final.get("notes"), dict)
            and set(final["labels"]) == set(item_ids) and all(v in letters + (MISSING,) for v in final["labels"].values()))


def finish(ctx: Context, rater: str, part: str, run: int, story_id: str, attempt: int | None) -> dict:
    """Write the final record of a story: from the call that gave an answer, or X for each item."""
    sd = ctx.story_dir(rater, part, run, story_id)
    item_ids = ctx.item_ids(story_id)
    head = {"rater": rater, "part": part, "run": run, "story_id": story_id, "items": item_ids,
            "read_by_runner_sha256": ctx.runner_sha256}
    if attempt is None:
        final = {**head, "outcome": "no answer", "attempts": MAX_ATTEMPTS, "labels": {i: MISSING for i in item_ids},
                 "notes": {i: f"no answer after {MAX_ATTEMPTS} calls" for i in item_ids}, "n_valid": 0,
                 "lines_for_other_items": 0, "items_with_two_different_letters": []}
    else:
        answer_path = sd / "answer.txt"
        if answer_path.is_file():
            answer = read_text(answer_path)
        else:
            answer = safe_read_log(rater, read_text(sd / f"attempt{attempt}.events.jsonl"))["answer"]
        final = {**head, "outcome": "answer", "attempts": attempt, **parse_answer(answer, item_ids, LETTERS[part])}
    write_json(sd / "final.json", final)
    return final


def settle(ctx: Context, rater: str, part: str, run: int, story_id: str) -> tuple[dict | None, int]:
    """Read the calls of earlier starts of one story. Each one counts. A call with no record is judged
    from its log. Return the final record when the story is done (else None), and the number of
    calls that are used. The number is -1 when a stop came during a wait."""
    sd = ctx.story_dir(rater, part, run, story_id)
    item_ids = ctx.item_ids(story_id)
    tag = f"[{PRETTY[rater]}, {part}, run {run}, {story_id}]"
    final = read_json(sd / "final.json")
    if valid_final(final, item_ids, LETTERS[part]):
        return final, 0
    if not sd.is_dir():
        return None, 0
    numbers = sorted(int(m.group(1)) for p in sd.glob("attempt*.started") if (m := re.fullmatch(r"attempt(\d+)\.started", p.name)))
    numbers += sorted(int(m.group(1)) for p in sd.glob("attempt*.meta.json") if (m := re.fullmatch(r"attempt(\d+)\.meta\.json", p.name)))
    used = max(numbers, default=0)
    if used > MAX_ATTEMPTS:
        raise Refuse(f"{sd} holds a record of call {used}. A story has four calls at most. Stop and ask for help.")
    prompt = ctx.prompts[part][story_id]
    for n in range(1, used + 1):
        meta = read_json(sd / f"attempt{n}.meta.json")
        if not isinstance(meta, dict) or "outcome" not in meta:
            meta = recover_call(ctx, rater, part, run, story_id, n, prompt)
            if meta is None:
                return None, -1
            say(f"{tag} call {n} of an earlier start: {meta['outcome']}" + (f" ({meta['reason']})" if meta["reason"] else ""))
            if meta["outcome"] != "answer":
                note_failure(ctx, rater, meta["reason"])
                if meta["reason"].startswith(WRONG_SERVICE):
                    set_down(ctx, rater, meta["reason"])
        if meta["outcome"] == "answer":
            final = finish(ctx, rater, part, run, story_id, n)
            say(f"{tag} the answer of call {n} is used, valid labels {final['n_valid']} of {len(item_ids)}")
            return final, used
    if used >= MAX_ATTEMPTS:
        final = finish(ctx, rater, part, run, story_id, None)
        say(f"{tag} no answer after {MAX_ATTEMPTS} calls: each item gets X")
        return final, used
    return None, used


def do_story(ctx: Context, rater: str, part: str, run: int, story_id: str) -> dict | None:
    """Get the labels of one story from one rater. Return None when the story stays open (a stop)."""
    sd = ctx.story_dir(rater, part, run, story_id)
    sd.mkdir(parents=True, exist_ok=True)
    final, used = settle(ctx, rater, part, run, story_id)
    if final is not None or used < 0:
        return final
    prompt = ctx.prompts[part][story_id]
    tag = f"[{PRETTY[rater]}, {part}, run {run}, {story_id}]"
    n_items = len(ctx.item_ids(story_id))
    for attempt in range(used + 1, MAX_ATTEMPTS + 1):
        before = _d(read_json(sd / f"attempt{attempt - 1}.meta.json")) if attempt > 1 else {}
        ended = seconds_since(before.get("ended_utc") or before.get("recorded_utc"))
        wait_until = time.monotonic() + max(0.0, ctx.waits[attempt - 1] - ended)
        while True:  # the wait before this call, in short steps
            with ctx.state[rater].mu:
                down = ctx.state[rater].down
            if down:
                raise RaterDown(down)
            left = wait_until - time.monotonic()
            if STOP.is_set() or (left > 0 and STOP.wait(min(1.0, left))):
                return None
            if left <= 0:
                break
        gate(ctx, rater)
        with ctx.state[rater].mu:
            down = ctx.state[rater].down
        if down:
            raise RaterDown(down)
        if STOP.is_set():
            return None
        meta = one_call(ctx, rater, part, run, story_id, attempt, prompt)
        if meta["outcome"] == "answer":
            note_answer(ctx, rater)
            final = finish(ctx, rater, part, run, story_id, attempt)
            say(f"{tag} call {attempt}: answer in {meta['seconds']:.0f} s, valid labels {final['n_valid']} of {n_items}")
            return final
        say(f"{tag} call {attempt}: no answer ({meta['reason']})")
        if meta.get("stopped"):
            return None
        note_failure(ctx, rater, meta["reason"])
    final = finish(ctx, rater, part, run, story_id, None)
    say(f"{tag} no answer after {MAX_ATTEMPTS} calls: each item gets X")
    return final


def seconds_since(stamp) -> float:
    """The seconds from a UTC time of a record to now. A very large number when the record has no time."""
    try:
        then = datetime.strptime(str(stamp), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()
    except ValueError:
        return 1e9
    return max(0.0, time.time() - then)


def label_rows(ctx: Context, rater: str, part: str, run: int) -> list[list[str]] | None:
    rows = []
    for story_id in ctx.order:
        final = read_json(ctx.story_dir(rater, part, run, story_id) / "final.json")
        if not valid_final(final, ctx.item_ids(story_id), LETTERS[part]):
            return None
        for item in final["items"]:
            rows.append([item, final["labels"][item], str(final["notes"].get(item, ""))])
    return rows


def write_label_file(d: Path, ctx: Context, rater: str, part: str, run: int, scorer=None, ids: list[str] | None = None) -> str:
    """Write one label file when each story has a final record. Never write a file again."""
    name = label_file_name(part, rater, run)
    rows = label_rows(ctx, rater, part, run)
    if rows is None:
        return "not complete"
    buf = io.StringIO(newline="")
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["item", "label", "note"])
    w.writerows(rows)
    text = buf.getvalue()
    path = d / name
    if path.exists():
        if read_text(path) == text:
            return "already written"
        raise Refuse(f"{name} exists and is not equal to the stored answers. A label file is never written again. "
                     "Stop and ask for help.")
    if scorer is not None and ids is not None and len(ids) == scorer.N_ITEMS:
        # The scoring tool must accept the file. Test a copy first, so that a file with a fault is never written.
        with tempfile.TemporaryDirectory(prefix="rater_label_") as tmp:
            trial = Path(tmp) / name
            write_atomic(trial, text)
            try:
                scorer.parse_label_file(trial, ids)
            except scorer.Refuse as e:
                raise Refuse(f"{name} was not written: the scoring tool does not accept it ({e}). Stop and ask for help.") from None
    write_atomic(path, text)
    return "written"


def run_tasks(ctx: Context, tasks: list[tuple[str, str, int, str]], jobs: int) -> list[BaseException]:
    """Run the calls. Each rater has its own workers, so one slow rater does not hold the other.
    Return the errors that were not expected. After such an error no new call starts, and the calls
    that are open end in the normal way and are recorded."""
    errors: list[BaseException] = []

    def one(task) -> None:
        with ctx.state[task[0]].mu:
            down = ctx.state[task[0]].down
        if STOP.is_set() or down:
            return
        try:
            do_story(ctx, *task)
        except RaterDown:
            return
        except BaseException as e:  # noqa: BLE001  (a fault in one call must stop the run and be reported)
            errors.append(e)
            STOP.set()

    def work(rater: str) -> None:
        mine = [t for t in tasks if t[0] == rater]
        with ThreadPoolExecutor(max_workers=jobs) as pool:
            for f in [pool.submit(one, t) for t in mine]:
                f.result()

    threads = [threading.Thread(target=work, args=(r,)) for r in RATERS if any(t[0] == r for t in tasks)]
    for th in threads:
        th.start()
    while any(th.is_alive() for th in threads):  # short joins, so that a stop signal is handled at once
        for th in threads:
            th.join(0.2)
    return errors


def save_prompts(root: Path, prompts: dict) -> None:
    """Save the exact message of each part and story, with its hash."""
    hashes: dict[str, dict[str, str]] = {}
    for part, by_story in prompts.items():
        pd = root / "prompts" / part
        pd.mkdir(parents=True, exist_ok=True)
        hashes[part] = {}
        for story_id, text in by_story.items():
            path = pd / f"{story_id}.txt"
            if path.is_file() and read_text(path) != text:
                raise Refuse(f"The stored message {path.name} of part '{part}' is not equal to the message that the files give now. "
                             "The instrument changed after a run started. Stop and ask for help.")
            if not path.is_file():
                write_atomic(path, text)
            hashes[part][story_id] = sha256_text(text)
    write_json(root / "prompts" / "sha256.json", hashes)


def settings_record(versions: dict, jobs: int, waits=WAIT_S, time_limit: float = TIME_LIMIT_S) -> dict:
    return {
        "raters": {
            "gemini": {"command": "agy", "version": versions.get("gemini"), "model": GEMINI_MODEL,
                       "note": "the thinking level 'high' is part of the model name; the log of each call names the model",
                       "command_line": command_for_record("gemini"),
                       "mode": "the command line asks for --mode auto; the account does not offer it, and the log of each call "
                               "shows the mode in effect (mode_in_log)",
                       "guard": "AGENT_COUNCIL_SEAT=1: a hook refuses each tool call, except a read inside the empty folder of the call"},
            "gpt": {"command": "codex", "version": versions.get("gpt"), "model": GPT_MODEL, "reasoning_effort": GPT_EFFORT,
                    "note": "the Codex log does not name the model or the effort: both are the values that the command line asks "
                            "for. A test of 14 November 2026 showed that the service checks the effort value "
                            "(ai_runs_smoke/effort_check_2026-11-14).",
                    "command_line": command_for_record("gpt")},
        },
        "sampling_settings": "the defaults of each command; the commands give no switch for them",
        "time_limit_seconds_for_one_call": time_limit, "calls_for_one_story_at_most": MAX_ATTEMPTS,
        "wait_seconds_before_each_call": list(waits), "calls_of_one_rater_at_the_same_time": jobs,
        "calls_in_a_row_with_an_error_that_stop_a_rater": TECHNICAL_IN_A_ROW,
        "environment_variables_passed": list(ENV_KEEP) + ["AGENT_COUNCIL_SEAT (agy only)"],
    }


def git(args: list[str]):
    """Run git on the thesis repository, with no git variable of the session. None when git does not start."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    try:
        return subprocess.run([GIT, "-C", str(THESIS), *args], capture_output=True, env=env, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None


def start_record(d: Path, versions: dict, start_id: str) -> dict:
    """The state of the tools at one start: versions and hashes."""
    rec = {"start_id": start_id, "started_utc": now_utc(), "versions": versions, "runner_sha256": sha256_file(Path(__file__)),
           "scoring_tool_sha256": sha256_file(HERE / "score_study2_review.py"), "guard_sha256": guard_hashes(),
           "instrument_sha256": instrument_hashes(d)}
    if (d / "manifest.json").is_file():
        rec["manifest_sha256"] = sha256_file(d / "manifest.json")
    r = git(["rev-parse", "HEAD"])
    rec["git_head_of_the_workspace"] = r.stdout.decode(UTF8, "replace").strip() if r is not None and r.returncode == 0 else "unknown"
    return rec


def record_check(root: Path) -> int:
    """Count the record files of the calls that hold a home path or the name of the user account."""
    words = ["/Users/"]
    for key in ("USER", "LOGNAME"):
        if os.environ.get(key):
            words.append(os.environ[key])
    hits = 0
    for folder in (root / "calls", root / "preflight", root / "memory_test"):
        if folder.is_dir():
            for p in folder.rglob("*"):
                if p.is_file() and p.suffix in (".jsonl", ".txt", ".json"):
                    text = read_text(p)
                    hits += any(w in text for w in words)
    return hits


# ---------------------------------------------------------------------------
# sub-command: run
# ---------------------------------------------------------------------------
def require_human_lock(d: Path, check_git: bool = True) -> None:
    scorer = load_scorer()
    try:
        locks = scorer.read_locks(d)
        state = scorer.lock_state(d, locks, scorer.HUMAN)
    except scorer.Refuse as e:
        raise Refuse(str(e)) from e
    if state != "locked_ok":
        raise Refuse(f"{scorer.HUMAN} is not locked (state: {scorer.STATE_TEXT[state]}). "
                     "An AI rater runs only after the human file is locked and committed. No call was made.")
    if locks.get("key_opened_at_utc") or locks.get("key_openings"):
        raise Refuse("The key was already opened. No AI rater runs after that. No call was made.")
    if not check_git:
        return
    human, lockfile = (d / scorer.HUMAN).resolve(), (d / "locks.json").resolve()
    locked = locks["locks"][scorer.HUMAN]["sha256"]
    try:
        rel_human, rel_lock = human.relative_to(THESIS).as_posix(), lockfile.relative_to(THESIS).as_posix()
    except ValueError:
        raise Refuse("The review folder is not inside the thesis repository. No call was made.") from None
    for path in (human, lockfile):
        r = git(["status", "--porcelain", "--", str(path)])
        if r is None:
            raise Refuse("git did not start, so the commit of the human file cannot be checked. No call was made.")
        if r.returncode != 0 or r.stdout.strip():
            raise Refuse(f"{path.name} differs from its commit, or is in no commit. Commit the locked human file and locks.json "
                         "first. No call was made.")
    r = git(["show", f"HEAD:{rel_lock}"])
    try:
        committed = json.loads(r.stdout)["locks"][scorer.HUMAN]["sha256"] if r is not None and r.returncode == 0 else None
    except (ValueError, KeyError, TypeError):
        committed = None
    r = git(["show", f"HEAD:{rel_human}"])
    in_head = hashlib.sha256(r.stdout).hexdigest() if r is not None and r.returncode == 0 else None
    if committed != locked or in_head != locked or sha256_file(human) != locked:
        raise Refuse("The last commit does not hold the locked human file and its lock with the hash of the file on disk. "
                     "Commit the locked human file and locks.json first. No call was made.")


def require_smoke(d: Path, raters: list[str], versions: dict) -> str:
    """A smoke test must have passed with this script, these versions of the commands, these rules and
    instruction texts, the long message and the time limit of the run."""
    here = sha256_file(Path(__file__))
    now = instrument_hashes(d)
    folder = d / "ai_runs_smoke"
    for p in sorted(folder.glob("*/result.json"), reverse=True) if folder.is_dir() else []:
        r = _d(read_json(p))
        if (r.get("passed") is True and r.get("runner_sha256") == here
                and all(_d(r.get("versions")).get(k) == versions.get(k) for k in raters)
                and set(raters) <= set(r.get("raters") or [])
                and isinstance(r.get("pad_kb"), int) and r["pad_kb"] >= SMOKE_PAD_KB
                and r.get("time_limit_seconds") == TIME_LIMIT_S and r.get("instrument_sha256") == now):
            return p.parent.name
    raise Refuse("No smoke test has passed with this script, these rules and instruction texts, and these versions of the commands ("
                 + ", ".join(f"{k}: {versions.get(k)}" for k in raters) + "). Run the sub-command smoke first (with no option). "
                 "No call was made.")


def pick(args, stories: list[dict]) -> tuple[list[str], list[str], list[int], list[str]]:
    raters = list(RATERS) if args.rater == "both" else [args.rater]
    parts = list(PARTS) if args.part == "both" else [args.part]
    try:
        runs = sorted({int(x) for x in str(args.runs).split(",")})
    except ValueError:
        raise Refuse("--runs must be 1, 2 or 1,2") from None
    if not runs or any(r not in RUNS for r in runs):
        raise Refuse("--runs must be 1, 2 or 1,2")
    if not 1 <= args.jobs <= DEFAULT_JOBS:
        raise Refuse(f"--jobs must be between 1 and {DEFAULT_JOBS}")
    all_ids = [st["story_id"] for st in stories]
    wanted = all_ids if not args.stories else [s.strip() for s in args.stories.split(",") if s.strip()]
    unknown = [s for s in wanted if s not in all_ids]
    if unknown:
        raise Refuse("unknown story: " + ", ".join(unknown))
    return raters, parts, runs, [s for s in all_ids if s in wanted]


def close_start(record_path: Path, **facts) -> None:
    record = _d(read_json(record_path))
    if record.get("starts"):
        record["starts"][-1].update({"ended_utc": now_utc(), **facts})
        write_json(record_path, record)


def cmd_run(args, check_git: bool = True, check_manifest: bool = True, check_guard: bool = True, check_smoke: bool = True,
            waits=WAIT_S, time_limit: float = TIME_LIMIT_S, test_limit: float = PREFLIGHT_LIMIT_S) -> int:
    d = Path(args.dir)
    require_human_lock(d, check_git)
    runner_change = check_against_manifest(d, getattr(args, "allow_changed_runner", "") or "") if check_manifest else {}
    inputs = load_inputs(d)
    raters, parts, runs, story_ids = pick(args, inputs["stories"])
    if "gemini" in raters and check_guard and not gemini_guard_ready():
        raise Refuse("The guard of the agy command is not installed (~/.gemini/config/hooks.json). "
                     "Gemini does not run without it. No call was made.")
    root = d / "ai_runs"
    errors: list[BaseException] = []
    down: dict[str, str] = {}
    with RunLock(root):
        clean_tmp(root)
        for p in d.glob("labels_*.csv.tmp[0-9]*_[0-9]*"):
            p.unlink()
        versions = {r: cli_version(r) for r in raters}
        smoke_used = require_smoke(d, raters, versions) if check_smoke else "not checked (test)"
        ctx = Context(root, inputs["prompts"], inputs["stories"], waits=waits, time_limit=time_limit, versions=versions,
                      test_limit=test_limit, check_mode=check_guard)
        files = [(rater, part, run) for rater in raters for part in parts for run in runs]
        save_prompts(root, inputs["prompts"])
        record_path = root / "settings.json"
        record = _d(read_json(record_path))
        record.setdefault("starts", [])
        record["settings"] = settings_record(versions, args.jobs, waits, time_limit)
        start = {**start_record(d, versions, ctx.start_id), "raters": raters, "parts": parts, "runs": runs,
                 "stories": story_ids, "jobs": args.jobs, "smoke_test_used": smoke_used}
        if runner_change:
            start["runner_changed_after_the_manifest"] = runner_change
            say("NOTE: this script differs from the file that manifest.json records. The reason is in the record of this start.")
        record["starts"].append(start)
        write_json(record_path, record)
        try:
            # 1. Calls of earlier starts that have no record yet (one after the other, before any new call).
            for rater, part, run in files:
                for sid in story_ids:
                    if STOP.is_set():
                        break
                    if ctx.story_dir(rater, part, run, sid).is_dir():
                        settle(ctx, rater, part, run, sid)
            for rater, part, run in files:
                name = label_file_name(part, rater, run)
                if (d / name).exists() and label_rows(ctx, rater, part, run) is None:
                    raise Refuse(f"{name} exists, but some of its stories have no final record. Stop and ask for help. "
                                 "No call was made.")
            tasks = [(rater, part, run, sid) for rater in raters for run in runs for part in parts for sid in story_ids
                     if not valid_final(read_json(ctx.story_dir(rater, part, run, sid) / "final.json"),
                                        ctx.item_ids(sid), LETTERS[part])]
            say(f"stories to do: {len(tasks)} (a story that has a final record is not sent again)")
            # 2. One short test call for each rater.
            for rater in raters:
                with ctx.state[rater].mu:
                    already_down = bool(ctx.state[rater].down)
                if any(t[0] == rater for t in tasks) and not STOP.is_set() and not already_down:
                    ok, why = short_test(ctx, rater)
                    if not ok and not STOP.is_set():
                        raise Refuse(f"{PRETTY[rater]} did not pass the short test call ({why}). No story was sent. "
                                     "Look at the folder preflight, correct the cause, and start again.")
                    if ok:
                        with ctx.state[rater].mu:
                            ctx.state[rater].checked_seq = ctx.state[rater].fail_seq
                        say(f"{PRETTY[rater]}: the short test call passed ({versions[rater]})")
            # 3. The stories.
            if tasks and not STOP.is_set():
                errors = run_tasks(ctx, tasks, args.jobs)
            # 4. The label files.
            scorer = load_scorer()
            written = {}
            for rater, part, run in files:
                name = label_file_name(part, rater, run)
                state = write_label_file(d, ctx, rater, part, run, scorer, inputs["ids"])
                say(f"{name}: {state}")
                if state in ("written", "already written"):
                    written[name] = {"sha256": sha256_file(d / name), "final_records": {
                        sid: sha256_file(ctx.story_dir(rater, part, run, sid) / "final.json") for sid in ctx.order}}
            if written:
                check = _d(read_json(root / "label_check.json"))
                check.update(written)
                write_json(root / "label_check.json", check)
        except Refuse as e:
            close_start(record_path, refused=str(e))
            raise
        except BaseException as e:
            close_start(record_path, error=f"{type(e).__name__}: {e}")
            raise
        for r in raters:
            with ctx.state[r].mu:
                if ctx.state[r].down:
                    down[r] = ctx.state[r].down
        no_answer = sum(1 for rater, part, run in files for sid in story_ids
                        if _d(read_json(ctx.story_dir(rater, part, run, sid) / "final.json")).get("outcome") == "no answer")
        close_start(record_path, stopped_by_a_signal=STOP.is_set(), raters_stopped=down,
                    stories_with_no_answer_after_four_calls=no_answer, errors=[f"{type(e).__name__}: {e}" for e in errors])
        say(f"stories with X for each item, because four calls gave no answer: {no_answer}")
        say(f"record check: files of the calls that hold a home path or the name of the user account: {record_check(root)}")
    for e in errors:
        say(f"ERROR in a call ({type(e).__name__}): {e}")
    if errors:
        say("The run stopped because of this error. Each call that was made is recorded. Stop and ask for help.")
        return 1
    if STOP.is_set():
        say("The run was stopped. Each call that was open counts as one of the four calls of its story. "
            "Start again with the same command to continue.")
        return 130
    if down:
        for r, why in down.items():
            say(f"{PRETTY[r]} stopped: {why}. No X was written for its open stories. Start again with the same command "
                "when the service works.")
        return 3
    return 0


# ---------------------------------------------------------------------------
# sub-command: status
# ---------------------------------------------------------------------------
def cmd_status(args) -> int:
    d = Path(args.dir)
    inputs = load_inputs(d)
    ctx = Context(d / "ai_runs", inputs["prompts"], inputs["stories"])
    say("a start of this script uses the folder now: " + ("yes" if lock_is_held(d / "ai_runs") else "no"))
    for rater in RATERS:
        for part in PARTS:
            for run in RUNS:
                answered = no_answer = calls = 0
                for sid in ctx.order:
                    sd = ctx.story_dir(rater, part, run, sid)
                    calls += len(list(sd.glob("attempt*.started"))) if sd.is_dir() else 0
                    final = read_json(sd / "final.json")
                    if valid_final(final, ctx.item_ids(sid), LETTERS[part]):
                        if final["outcome"] == "answer":
                            answered += 1
                        else:
                            no_answer += 1
                name = label_file_name(part, rater, run)
                todo = len(ctx.order) - answered - no_answer
                say(f"{name}: stories with an answer: {answered}; with no answer after {MAX_ATTEMPTS} calls: {no_answer}; "
                    f"not done: {todo}; calls that were started: {calls}; "
                    f"label file: {'written' if (d / name).exists() else 'not written'}")
    return 0


# ---------------------------------------------------------------------------
# sub-command: smoke (invented items only)
# ---------------------------------------------------------------------------
def _req(title: str, description: str, rationale: str = "An unattended session can be used by another person.") -> str:
    return (f"**Title.** {title}\n\n**Description.** {description}\n\n**Rationale.** {rationale}\n\n"
            f"**Acceptance criteria.**\n- The behaviour is covered by a test.\n\n**Implementation notes.** (none)\n")


def _table(rows: list[tuple[str, str, str, str]]) -> str:
    if not rows:
        return "The script found no code-like name in this requirement.\n"
    out = ["| Name | Kind | Did the search find it? | In the story or the system description? |", "|---|---|---|---|"]
    out += [f"| `{n}` | {k} | {f} | {g} |" for n, k, f, g in rows]
    return "\n".join(out) + "\n"


def smoke_sheet(pad_kb: int) -> tuple[dict, dict]:
    """An invented product (ExampleApp) with the examples of the two rules files. No item of the study."""
    idle = ("session-idle-timeout", "name of the product", "found", "no")
    strict = ("session-strict-mode", "name of the product", "not found", "no")
    names_items = [
        (_req("End idle sessions", "Sessions must end after a period with no activity."), _table([]), "G"),
        (_req("Protect the session cookie", "Sign cookies with HMAC-SHA256. Store sessions in Redis."),
         _table([("HMAC-SHA256", "standard name", "found", "no"), ("Redis", "standard name", "found", "yes")]), "G"),
        (_req("Set the idle time", "Set `session-idle-timeout` to 15 minutes."), _table([idle]), "P"),
        (_req("Set the idle time and the strict mode", "Set `session-idle-timeout` to 15 minutes and enable `session-strict-mode`."),
         _table([idle, strict]), "N"),
        (_req("Export a trace of ended sessions", "Add a new flag `--trace-export` that writes each ended session to a file."),
         _table([("--trace-export", "name of the product", "not found", "no")]), "G"),
        (_req("Protect the database password", "Use the environment variable `APP_DB_PASSWORD`, which the server reads at start."),
         _table([("APP_DB_PASSWORD", "name of the product", "not found", "no")]), "N"),
    ]
    support_items = [
        (_req("End idle sessions", "Sessions must end after a period with no activity."), "G"),
        (_req("Default timeout", "The session timeout MUST default to 10 minutes."), "G"),
        (_req("Encrypt session data", "Session data in Redis must be encrypted with AES-256."), "G"),
        (_req("Set the idle time", "Set `session-idle-timeout` to 15, which is lower than the default of 30 minutes."), "S"),
        (_req("Set the idle time and the strict mode", "Set `session-idle-timeout` to 15 seconds and enable `session-strict-mode`."), "U"),
        (_req("End sessions on each node", "ExampleApp ends idle sessions on each node at the same moment."), "G"),
    ]
    story_text = "As an operator, I want idle sessions to end, so that another person cannot use an unattended browser."
    criteria = ["A session with no activity ends after a fixed time.", "Session data is stored in Redis."]
    doc = "# Sessions\n\n`session-idle-timeout`: the idle time of a session in minutes. Default: 30.\n"
    files = [{"name": "admin/sessions.md", "text": doc}]
    if pad_kb > 0:
        filler, k = [], 0
        while sum(len(x) for x in filler) < pad_kb * 1000:
            k += 1
            filler.append(f"## Option {k}\n\n`example-option-{k}`: this option sets the value {k} of the module {k % 17}. "
                          f"Default: {k * 3 % 101}. It has no effect on sessions.\n\n")
        files.append({"name": "admin/other-options.md", "text": "# Other options\n\n" + "".join(filler)})
    names_story = ("Story EX-001. End idle sessions in ExampleApp\n\nProject: exampleapp\n\nStory: " + story_text
                   + "\n\nAcceptance criteria of the story:\n" + "\n".join(f"{i}. {c}" for i, c in enumerate(criteria, 1)))
    story_block = ("**Project.** exampleapp\n\n**Story.** " + story_text + "\n\n**Acceptance criteria of the story.**\n\n"
                   + "\n".join(f"{i}. {c}" for i, c in enumerate(criteria, 1))
                   + "\n\n**Fact sheet.**\n\n- Identifiers that the documentation gives for this story: `session-idle-timeout`\n"
                   "- Standard names: Redis, AES-256, HMAC-SHA256\n"
                   "- Documented facts:\n  1. `session-idle-timeout` sets the idle time in minutes; the default is 30.\n")
    evidence = ("### A. Documentation excerpts for the identifiers of the fact sheet\n\n"
                "`session-idle-timeout` (admin/sessions.md:3): \"`session-idle-timeout`: the idle time of a session in minutes. Default: 30.\"\n\n"
                "### B. Other code-like names in the requirements\n\n| Name | Kind | Where a text search found it |\n|---|---|---|\n"
                "| `session-strict-mode` | name of the product | not found |\n\n"
                "### C. The system description that each generator received\n\nExampleApp is a web application. Sessions are stored in Redis.\n\n"
                "### D. The source files of this story\n\nThe full text of these files follows after the requirements: "
                + ", ".join(f["name"] for f in files) + ".\n")

    def story(items, with_tables):
        return {"story_id": "EX-001", "project": "exampleapp", "title": "End idle sessions in ExampleApp",
                "names_story": names_story, "story_block": story_block, "evidence": evidence, "source_files": files,
                "items": [{"item": f"R{i:02d}", "text": it[0], "names_table": it[1] if with_tables else "", "names_rows": []}
                          for i, it in enumerate(items, 1)]}
    sheets = {"names": {"stories": [story(names_items, True)]}, "support": {"stories": [story(support_items, False)]}}
    expected = {"names": {f"R{i:02d}": it[2] for i, it in enumerate(names_items, 1)},
                "support": {f"R{i:02d}": it[1] for i, it in enumerate(support_items, 1)}}
    return sheets, expected


def memory_test(ctx: Context, rater: str) -> dict:
    """Two calls. The first gives an invented code word. The second, a new call, asks for it.
    A rater that knows the word in the second call has a memory of the first call."""
    word = "KESTREL-" + secrets.token_hex(3).upper()
    folder = ctx.root / "memory_test"
    folder.mkdir(parents=True, exist_ok=True)
    out = {"rater": rater, "code_word": word, "calls": []}
    prompts = [f"This is a test of memory between calls. Do not use tools. The code word is {word}. "
               "Reply with the single word OK and nothing else.",
               "This is a test of memory between calls. Do not use tools: work only with the text of this message and with "
               "what you know now. An earlier call gave you a code word. If you know the code word, reply with the code word. "
               "If you do not know it, reply with the single word NONE."]
    for n, prompt in enumerate(prompts, 1):
        out_path, err_path = folder / f"{rater}_call{n}.events.jsonl", folder / f"{rater}_call{n}.stderr.txt"
        res = run_cli(rater, prompt, ctx.test_limit, out_path, err_path)
        log = safe_read_log(rater, read_text(out_path) if res["started"] else "")
        outcome, reason = judge(res, log, rater, ctx.check_mode) if res["started"] else ("no answer", "the command did not start")
        out["calls"].append({"call": n, "prompt": prompt, "outcome": outcome, "reason": reason, "answer": log["answer"].strip()[:200],
                             "started_utc": res.get("started_utc")})
    second = out["calls"][1]
    out["passed"] = (out["calls"][0]["outcome"] == "answer" and second["outcome"] == "answer" and word not in second["answer"]
                     and second["answer"].strip().strip(".").upper() == "NONE")
    out["second_call_knew_the_code_word"] = word in second["answer"]
    write_json(folder / f"{rater}.json", out)
    return out


def cmd_smoke(args, check_guard: bool = True, waits=(0, 20, 60, 120)) -> int:
    d = Path(args.dir)
    raters = list(RATERS) if args.rater == "both" else [args.rater]
    if "gemini" in raters and check_guard and not gemini_guard_ready():
        raise Refuse("The guard of the agy command is not installed. Gemini does not run without it.")
    sheets, expected = smoke_sheet(args.pad_kb)
    prompts: dict[str, dict[str, str]] = {}
    for part in PARTS:
        rules = sections_1_to_6((d / RULES_FILE[part]).read_text(encoding=UTF8), RULES_FILE[part])
        template = template_below_line((d / PROMPT_FILE[part]).read_text(encoding=UTF8), PROMPT_FILE[part])
        prompts[part] = {"EX-001": build_prompt(part, sheets[part]["stories"][0], rules, template)}
    with RunLock(d / "ai_runs"):  # the same lock as the run: a smoke test and a run never use the raters at the same time
        stamp = now_utc().replace(":", "").replace("-", "")
        root = d / "ai_runs_smoke" / stamp
        root.mkdir(parents=True)
        save_prompts(root, prompts)
        versions = {r: cli_version(r) for r in raters}
        ctxs = {part: Context(root, prompts, sheets[part]["stories"], waits=waits, time_limit=args.time_limit, versions=versions,
                              check_mode=check_guard) for part in PARTS}
        write_json(root / "settings.json", {
            "settings": settings_record(versions, len(PARTS) + 1, waits, args.time_limit),
            "start": start_record(d, versions, ctxs["names"].start_id),
            "note": "invented items only, no item of the study", "pad_kb": args.pad_kb})
        say(f"smoke test with invented items; message sizes: names {len(prompts['names']['EX-001'])} characters, "
            f"support {len(prompts['support']['EX-001'])} characters")
        ok = True
        for rater in raters:
            passed, why = short_test(ctxs["names"], rater)
            say(f"{PRETTY[rater]}: the short test call " + (f"passed ({versions[rater]})" if passed else f"FAILED ({why})"))
            ok = ok and passed
        results, memory = {}, {}
        lock = threading.Lock()

        def one(rater: str, part: str) -> None:
            try:
                final = do_story(ctxs[part], rater, part, 1, "EX-001")
            except RaterDown:
                return
            if final is None:
                return
            same = sum(final["labels"][i] == expected[part][i] for i in final["items"])
            meta = _d(read_json(ctxs[part].story_dir(rater, part, 1, "EX-001") / f"attempt{final['attempts']}.meta.json"))
            with lock:
                results[(rater, part)] = (final, same, meta)

        def mem(rater: str) -> None:
            m = memory_test(ctxs["names"], rater)
            with lock:
                memory[rater] = m

        if ok:
            threads = [threading.Thread(target=one, args=(r, p)) for r in raters for p in PARTS]
            threads += [threading.Thread(target=mem, args=(r,)) for r in raters]
            for th in threads:
                th.start()
            while any(th.is_alive() for th in threads):
                for th in threads:
                    th.join(0.2)
        metas = [_d(read_json(p)) for p in sorted((root / "calls").rglob("attempt*.meta.json"))] if (root / "calls").is_dir() else []
        tool_steps = sum(len(m.get("tool_steps") or []) for m in metas)
        calls = {f"{m.get('rater')}, {m.get('part')}": sum(1 for x in metas if (x.get("rater"), x.get("part")) == (m.get("rater"), m.get("part")))
                 for m in metas}
        for (rater, part), (final, same, meta) in sorted(results.items()):
            n = len(final["items"])
            say(f"{PRETTY[rater]}, {part}: {final['outcome']} after {final['attempts']} call(s), {meta.get('seconds')} s, "
                f"valid labels {final['n_valid']} of {n}, equal to the letters of the rules examples {same} of {n}, "
                f"model in the log: {meta.get('model_in_log') or 'not named'}, mode in the log: {meta.get('mode_in_log') or 'not named'}, "
                f"files left in the folder: {len(meta.get('files_left_in_folder') or [])}")
            ok = ok and final["outcome"] == "answer" and final["n_valid"] == n and final["attempts"] == 1
        say(f"tool steps in the logs of all story calls: {tool_steps}")
        ok = ok and tool_steps == 0
        for rater in raters:
            m = memory.get(rater)
            good = bool(m and m["passed"])
            why = ""
            if m and not good:
                bad_call = next((c for c in m["calls"] if c["outcome"] != "answer"), None)
                why = (f"call {bad_call['call']}: {bad_call['reason']}" if bad_call else "the second call did not answer NONE")
                why += ("; the second call knew the code word" if m.get("second_call_knew_the_code_word")
                        else "; the second call did not know the code word")
            say(f"{PRETTY[rater]}, memory between calls: "
                + ("none (the second call did not know the code word)" if good else f"TEST FAILED ({why}; see the folder memory_test)"))
            ok = ok and good
        if len(results) != len(raters) * len(PARTS):
            ok = False
        ok = ok and not STOP.is_set()
        hits = record_check(root)
        write_json(root / "result.json", {
            "passed": ok, "ended_utc": now_utc(), "versions": versions, "runner_sha256": sha256_file(Path(__file__)),
            "raters": raters, "pad_kb": args.pad_kb, "time_limit_seconds": args.time_limit,
            "instrument_sha256": instrument_hashes(d), "calls_for_each_story": calls, "tool_steps_in_all_logs": tool_steps,
            "record_files_with_a_home_path_or_the_account_name": hits})
        say(f"record check: files of the calls that hold a home path or the name of the user account: {hits}")
        say(f"record: {root}")
        say("smoke test: " + ("PASS (each story needed one call and got a valid letter for each item, no tool step, "
                              "no memory between calls)" if ok else "FAIL"))
    return 0 if ok else 1


# ---------------------------------------------------------------------------
# sub-command: self-test (no call to a model)
# ---------------------------------------------------------------------------
STUB = r'''#!/usr/bin/env python3
"""A stand-in for the agy or the codex command. It reads what to do from plan.json beside it."""
import fcntl, json, os, re, stat, sys, time
from pathlib import Path
here = Path(__file__).resolve().parent
kind = Path(sys.argv[0]).name
if "--version" in sys.argv:
    print("stub 0.0"); sys.exit(0)
prompt = sys.argv[sys.argv.index("-p") + 1] if kind == "agy" else sys.stdin.read()
plan = json.loads((here / "plan.json").read_text())
is_ok = prompt.startswith("Reply with the single word OK")
is_memory = prompt.startswith("This is a test of memory")
count_file = here / f"count_{kind}_{'ok' if is_ok or is_memory else 'story'}.txt"
with open(here / "stub.lock", "w") as lock:  # three calls can run at the same time
    fcntl.flock(lock, fcntl.LOCK_EX)
    n = int(count_file.read_text()) if count_file.exists() else 0
    count_file.write_text(str(n + 1))
    with (here / "calls.jsonl").open("a") as fh:
        fh.write(json.dumps({"kind": kind, "ok_call": is_ok or is_memory, "cwd": os.getcwd(), "files_in_cwd": sorted(os.listdir(".")),
                             "env": sorted(os.environ), "seat": os.environ.get("AGENT_COUNCIL_SEAT"),
                             "stdin_is_a_file": stat.S_ISREG(os.fstat(0).st_mode),
                             "argv": [a for a in sys.argv[1:] if a != prompt], "prompt_chars": len(prompt), "time": time.time()}) + "\n")
def emit(obj): print(json.dumps(obj), flush=True)
def agy_log(text, status="SUCCESS", steps=(), extra_events=(), conv2=False):
    emit({"event": "init", "conversation_id": "c1", "init": {"model": plan.get("agy_model", "gemini-4-argon-high"), "cwd": os.getcwd(),
          "permission_mode": plan.get("agy_mode", "request-review"), "tools": ["run_command", "search_web"]}})
    for s in steps: emit({"event": "step_update", "step_update": s})
    for e in extra_events: emit(e)
    emit({"event": "result", "result": {"conversation_id": "c2" if conv2 else "c1", "status": status, "response": text, "num_turns": 1,
                                        "usage": {"input_tokens": 1}}})
def codex_log(text, before=(), failed=False, completed=True, extra_events=()):
    emit({"type": "thread.started", "thread_id": "t"})
    emit({"type": "item.completed", "item": {"id": "e0", "type": "error", "message": "Code Mode is unavailable because code-mode host is disabled."}})
    emit({"type": "turn.started"})
    for e in extra_events: emit(e)
    for i, t in enumerate(before): emit({"type": "item.completed", "item": {"id": f"p{i}", "type": "agent_message", "text": t}})
    emit({"type": "item.completed", "item": {"id": "m1", "type": "agent_message", "text": text}})
    if failed: emit({"type": "turn.failed", "error": {"message": "an invented failure"}})
    if completed: emit({"type": "turn.completed", "usage": {"input_tokens": 1}})
if is_ok or is_memory:
    answers = plan.get(kind + "_ok") or ["OK"]
    word = answers[min(n, len(answers) - 1)]
    if word == "EXIT":
        print("an invented failure", file=sys.stderr); sys.exit(2)
    if word == "SLOW":
        time.sleep(1.0); word = "OK"
    agy_log(word) if kind == "agy" else codex_log(word)
    sys.exit(0)
steps = plan.get(kind) or [{"do": "answer"}]
step = steps[min(n, len(steps) - 1)]
items = re.findall(r"^### (R\d{2})$", prompt, flags=re.M)
letter = step.get("letter", "G")
full = "\n".join(step.get("line", "{item},{letter},an invented reason").format(item=i, letter=letter) for i in items)
full = step.get("before", "") + full + step.get("after", "")
do = step["do"]
if do == "sleep":
    if os.fork() == 0:  # a child process that must die with the call
        time.sleep(step.get("child_s", 4)); (here / "child_was_alive.txt").write_text("x"); os._exit(0)
    time.sleep(step.get("s", 30))
if do == "slow":
    time.sleep(step.get("s", 2))
if do == "exit_no_log":
    print("429 an invented failure of the service", file=sys.stderr); sys.exit(2)
if do == "garbage":
    print("this is not a log"); sys.exit(0)
if do == "leave_file":
    Path("left.txt").write_text("x")
if do == "delete_binary":
    os.rename(here / kind, here / (kind + ".away"))
if do == "surrogate":
    print('{"type": "thread.started"}' if kind == "codex" else '{"event": "init", "conversation_id": "c1", "init": {"model": "gemini-4-argon-high", "permission_mode": "request-review", "tools": []}}', flush=True)
    text = json.dumps(full)[:-1] + ' \\ud83d"'
    if kind == "codex":
        print('{"type": "item.completed", "item": {"id": "m1", "type": "agent_message", "text": ' + text + '}}', flush=True)
        print('{"type": "turn.completed", "usage": {}}', flush=True)
    else:
        print('{"event": "result", "result": {"conversation_id": "c1", "status": "SUCCESS", "num_turns": 1, "response": ' + text + '}}', flush=True)
    sys.exit(0)
if kind == "agy":
    if do == "tool":
        agy_log(full, steps=[{"step_index": 2, "state": "ACTIVE", "step_type": "tool", "tool_name": "search_web"},
                             {"step_index": 2, "state": "ERROR", "step_type": "tool", "tool_name": "search_web"}])
    elif do == "unknown_step":
        agy_log(full, steps=[{"step_index": 2, "state": "DONE", "step_type": "browser_action"}])
    elif do == "thinking_step":
        agy_log(full, steps=[{"step_index": 1, "state": "DONE", "step_type": "thinking"}])
    elif do == "error_status":
        agy_log(full, status="ERROR")
    elif do == "empty":
        agy_log("")
    else:
        agy_log(full)
else:
    if do == "tool":
        codex_log(full, extra_events=[{"type": "item.started", "item": {"id": "c1", "type": "command_execution", "command": "ls"}},
                                      {"type": "item.completed", "item": {"id": "c1", "type": "command_execution", "command": "ls"}}])
    elif do == "unknown_step":
        codex_log(full, extra_events=[{"type": "session.created"}])
    elif do == "thinking_step":
        codex_log(full, extra_events=[{"type": "item.completed", "item": {"id": "r1", "type": "reasoning", "text": "a thought"}},
                                      {"type": "error", "message": "Reconnecting... 1/5"}])
    elif do == "error_status":
        codex_log(full, failed=True, completed=True)
    elif do == "empty":
        codex_log("")
    elif do == "preamble":
        codex_log(full, before=["\n".join(f"{i},N,a first thought" for i in items)])
    else:
        codex_log(full)
if do == "exit_after_answer":
    sys.exit(3)
'''

DRIVER = r'''import argparse, importlib.util, sys
sys.dont_write_bytecode = True
spec = importlib.util.spec_from_file_location("runner_under_test", sys.argv[1])
mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
mod.BIN = {"gemini": sys.argv[2] + "/agy", "gpt": sys.argv[2] + "/codex"}
mod.install_stop_handlers()
ns = argparse.Namespace(dir=sys.argv[3], rater="both", part="names", runs="1", stories="", jobs=int(sys.argv[4]), allow_changed_runner="")
sys.exit(mod.cmd_run(ns, check_git=False, check_manifest=False, check_guard=False, check_smoke=False, waits=(0, 0, 0, 0), time_limit=30, test_limit=30))
'''


def _test_sheet() -> dict:
    """60 invented items in 15 stories, in the form of sheet.json."""
    stories, n = [], 0
    for s in range(15):
        items = []
        for _ in range(4):
            n += 1
            items.append({"item": f"R{n:02d}", "text": _req(f"Invented requirement {n}", f"Use `option-{n}` for the test {n}."),
                          "names_table": _table([(f"option-{n}", "name of the product", "found", "no")]),
                          "names_rows": [{"name": f"option-{n}", "kind": "product", "found": True, "given": False}]})
        stories.append({"story_id": f"T-{s + 1:02d}", "project": "invented", "title": f"Invented story {s + 1}",
                        "names_story": f"Story T-{s + 1:02d}. Invented story {s + 1}\n\nProject: invented\n\nStory: text {{{{ITEMS}}}} and ${{x}}",
                        "story_block": f"**Project.** invented\n\n**Story.** text {s + 1} with {{{{RULES}}}} inside",
                        "evidence": "### A. Documentation excerpts\n\nnone\n", "source_files": [{"name": "a.md", "text": "{{EVIDENCE}} is only text here"}],
                        "items": items})
    return {"version": 2, "labels_names": ["P", "N", "G"], "labels_support": ["S", "G", "U"], "stories": stories}


def cmd_self_test(args) -> int:  # noqa: C901
    global BIN, THESIS, parse_answer, short_test
    real = Path(args.dir)
    scorer = load_scorer()
    results: list[tuple[str, bool, str]] = []

    def check(name: str, ok, detail: str = "") -> None:
        results.append((name, bool(ok), detail if not ok else ""))

    # --- the message, from the real rules and instruction texts ---
    for part in PARTS:
        rules_text = (real / RULES_FILE[part]).read_text(encoding=UTF8)
        rules = sections_1_to_6(rules_text, RULES_FILE[part])
        check(f"{part}: rules start at section 1 and stop before section 7",
              rules.startswith("## 1. ") and "\n## 6. " in rules and "\n## 7. " not in rules and rules in rules_text)
        template = template_below_line((real / PROMPT_FILE[part]).read_text(encoding=UTF8), PROMPT_FILE[part])
        check(f"{part}: the instruction text starts below the line", template.startswith("You are a rater") and "How it is used" not in template)
        check(f"{part}: the placeholders are the expected ones", sorted(re.findall(r"\{\{([A-Z_]+)\}\}", template)) == sorted(PLACEHOLDERS[part]))
    try:
        fill("a {{RULES}} b {{RULES}}", {"RULES": "x"}, ("RULES",), "t")
        check("fill: a placeholder that is there two times is refused", False)
    except Refuse:
        check("fill: a placeholder that is there two times is refused", True)
    check("fill: text that was put in is not scanned again",
          fill("{{RULES}}|{{STORY}}|{{ITEMS}}", {"RULES": "{{STORY}}", "STORY": "{{ITEMS}}", "ITEMS": "i"}, PLACEHOLDERS["names"], "t")
          == "{{STORY}}|{{ITEMS}}|i")
    real_inputs = load_inputs(real)
    n_items = 0
    all_ok = True
    for part in PARTS:
        for st in real_inputs["stories"]:
            p = real_inputs["prompts"][part][st["story_id"]]
            ids = [it["item"] for it in st["items"]]
            heads = re.findall(r"^### (R\d{2})$", p, flags=re.M)
            all_ok = all_ok and heads == ids and p.startswith("You are a rater") and "\nR00,L" in p
            if part == "names":
                n_items += len(ids)
                all_ok = all_ok and all(it["names_table"].rstrip() in p for it in st["items"]) and st["names_story"] in p
                all_ok = all_ok and p.count("**Names in this requirement.**") == len(ids)
            else:
                all_ok = all_ok and st["evidence"].rstrip() in p and all(f["text"].rstrip() in p for f in st["source_files"])
    check("real sheet: each message holds each item of its story one time, in order, with its table or its evidence", all_ok)
    check("real sheet: 60 items in 15 stories", n_items == 60 and len(real_inputs["stories"]) == 15)
    check("real sheet: the longest message is below 200,000 characters",
          max(len(p) for part in PARTS for p in real_inputs["prompts"][part].values()) < 200_000)

    # --- reading an answer ---
    ids = ["R01", "R02", "R03"]
    L = LETTERS["names"]

    def lab(text, letters=L):
        return [parse_answer(text, ids, letters)["labels"][i] for i in ids]
    base = "R01,P,a\nR02,N,b\nR03,G,c"
    check("answer: three plain lines", lab("R01,P,ok\nR02,N,`x` not found\nR03,G,no name") == ["P", "N", "G"])
    check("answer: spaces, list marks, bold, italic and code marks around the parts",
          lab("- R01 , P , ok\n**R02**,**N**,x\n`R03`,`G`,z") == ["P", "N", "G"] and lab("_R01_,_P_,a\n* R02,*N*,b\n> R03, G ,c") == ["P", "N", "G"])
    check("answer: a numbered list", lab("1. R01,P,ok\n2) R02,N,x\n(3) **R03**,G,z") == ["P", "N", "G"])
    check("answer: a line with no reason", lab("R01,P\nR02,N,\nR03,G") == ["P", "N", "G"])
    check("answer: Windows line ends and a code fence", lab("```\r\nR01,P,a\r\nR02,N,b\r\nR03,G,c\r\n```") == ["P", "N", "G"])
    check("answer: other text around the lines is ignored", lab("Here are my labels:\n" + base + "\nDone.") == ["P", "N", "G"])
    check("answer: a letter in lower case gives X", lab("R01,p,a\nR02,N,b\nR03,G,c") == ["X", "N", "G"])
    check("answer: a letter of the other part gives X", lab("R01,S,a\nR02,U,b\nR03,G,c") == ["X", "X", "G"])
    check("answer: a word in place of the letter gives X", lab("R01,PASS,a\nR02,N/A,b\nR03,G,c") == ["X", "X", "G"])
    check("answer: a missing line gives X", lab("R01,P,a\nR03,G,c") == ["P", "X", "G"])
    check("answer: the same letter two times is that letter", lab("R01,P,a\nR01,P,b\nR02,N,b\nR03,G,c") == ["P", "N", "G"])
    r = parse_answer("R01,P,a\nR01,N,b\nR02,N,b\nR03,G,c", ids, L)
    check("answer: two label lines with different letters give X", r["labels"]["R01"] == "X" and r["items_with_two_different_letters"] == ["R01"])
    check("answer: a valid letter and a letter that is not valid for the same item give X",
          lab("R01,P,a\nR01,S,b\nR02,N,b\nR03,G,c") == ["X", "N", "G"] and lab("R01,P,a\nR01,p,b\nR02,N,b\nR03,G,c") == ["X", "N", "G"])
    withdrawn = ["Correction: R02,P,the name is found", "Correction: R02 should be P, not N.", "Note: on reflection R02 is P.",
                 "R02: change to P", "R02 -> P", "R02 = P", "~~R02,P,x~~"]
    check("answer: another line that names the item with a different valid letter gives X (each form of a correction)",
          all(lab(base + "\n" + w) == ["P", "X", "G"] for w in withdrawn), str([lab(base + "\n" + w) for w in withdrawn]))
    check("answer: the reason of another item's line that names the item with a different valid letter gives X",
          lab("R01,P,a\nR02,N,b\nR03,G,unlike R02 which is P") == ["P", "X", "G"])
    check("answer: the same letter in another line changes nothing",
          lab(base + "\nR02 stays N.") == ["P", "N", "G"] and lab("R01,P,a\nR02,N,b\nR03,G,same reason as R02 (N)") == ["P", "N", "G"])
    check("answer: a line that names the item with no valid letter, or a letter inside a word, changes nothing",
          lab(base + "\nAs in R01, A new name is created.\nSee R02, so no name is left. GPU and PNG are words.") == ["P", "N", "G"])
    check("answer: the letter of another item's own label is not counted against an item that its reason names",
          lab("R01,P,a\nR02,N,b\nR03,G,the same case as R02") == ["P", "N", "G"])
    check("answer: a letter that is part of a name (P-256, G/4, x:P, P+1) does not count as a letter that stands alone",
          lab(base + "\nFor R02 the curve P-256, the ratio G/4, the pair x:P and the count P+1 apply.") == ["P", "N", "G"])
    check("answer: a reason that names its own item, also with another letter, does not change the letter (the limit that the page states)",
          lab("R01,P,a\nR02,N,R02 is not P; on reflection G\nR03,G,c") == ["P", "N", "G"])
    r = parse_answer(base + "\nR44,P,x", ids, L)
    check("answer: a line for an item of another story is ignored and counted", r["lines_for_other_items"] == 1 and r["n_valid"] == 3)
    check("answer: a table row is not a label line", lab("| R01 | P | a |\nR02,N,b\nR03,G,c") == ["X", "N", "G"])
    check("answer: no comma after the letter is not a label line", lab("R01,P - a\nR02,N,b\nR03,G,c") == ["X", "N", "G"])
    check("answer: a word before the item number is not a label line", lab("Item R01,P,a\nR02,N,b\nR03,G,c") == ["X", "N", "G"])
    check("answer: a line with several items gives a letter to its first item only", lab("R01,P,a; R02,N,b; R03,G,c") == ["P", "X", "X"])
    check("answer: an empty answer gives X for each item", lab("") == ["X", "X", "X"])
    check("answer: the note has no comma, no double quote and no line break",
          parse_answer('R01,P,the name "x", is found\nR02,N,b\nR03,G,c', ids, L)["notes"]["R01"] == "the name 'x'; is found")
    check("answer: part 2 accepts S, G and U only", lab("R01,S,a\nR02,P,b\nR03,U,c", LETTERS["support"]) == ["S", "X", "U"])
    t0 = time.monotonic()
    parse_answer("R01" + "," * 60000 + "\n" + "-" * 60000 + "\n" + "R01 P " * 20000 + "\nR02,N,b\nR03,G,c", ids, L)
    check("answer: a very long line of commas, marks or item numbers is read in less than a second", time.monotonic() - t0 < 1.0)

    # --- reading a log ---
    def glog(*steps, result=None, extra=()):
        ev = [{"event": "init", "init": {"model": GEMINI_MODEL, "permission_mode": "request-review", "tools": ["a"]}}]
        ev += [{"event": "step_update", "step_update": s} for s in steps]
        ev += list(extra)
        ev.append({"event": "result", "result": result or {"status": "SUCCESS", "response": "R01,P,a", "num_turns": 1}})
        return "\n".join(json.dumps(e) for e in ev)

    def verdict(rater, text, **res):
        return judge({"exit_code": 0, **res}, safe_read_log(rater, text), rater, True)[0]
    check("log, Gemini: plain steps give an answer",
          verdict("gemini", glog({"step_type": "user_input"}, {"step_type": "agent_response"}, {"step_type": "thinking"})) == "answer")
    check("log, Gemini: a tool step, also a refused one, gives no answer",
          verdict("gemini", glog({"step_type": "tool", "tool_name": "search_web", "step_index": 2, "state": "ERROR"})) == "no answer")
    check("log, Gemini: a step with a tool name or with tool facts and another type gives no answer",
          verdict("gemini", glog({"step_type": "agent_response", "tool_name": "read_file"})) == "no answer"
          and verdict("gemini", glog({"step_type": "agent_response", "tool_info": {"name": "x"}})) == "no answer")
    check("log, Gemini: a step of an unknown type, or with no type, gives no answer",
          verdict("gemini", glog({"step_type": "browser_action"})) == "no answer" and verdict("gemini", glog({"state": "DONE"})) == "no answer")
    check("log, Gemini: an unknown event gives no answer", verdict("gemini", glog() + '\n{"event": "subagent_done"}') == "no answer")
    check("log, Gemini: two result events give no answer",
          verdict("gemini", glog(extra=[{"event": "result", "result": {"status": "SUCCESS", "response": "R01,N,a", "num_turns": 1}}])) == "no answer")
    check("log, Gemini: two conversations give no answer",
          verdict("gemini", glog(extra=[{"event": "step_update", "conversation_id": "a", "step_update": {"step_type": "agent_response"}},
                                        {"event": "step_update", "conversation_id": "b", "step_update": {"step_type": "agent_response"}}])) == "no answer")
    check("log, Gemini: a result with a finished_reason, or with the status ERROR, gives no answer",
          verdict("gemini", glog(result={"status": "SUCCESS", "response": "R01,P,a", "finished_reason": "print_timeout"})) == "no answer"
          and verdict("gemini", glog(result={"status": "ERROR", "response": "R01,P,a"})) == "no answer")
    check("log, Gemini: two turns give no answer",
          verdict("gemini", glog(result={"status": "SUCCESS", "response": "R01,P,a", "num_turns": 2})) == "no answer")
    check("log, Gemini: a result that is not an object, or a response that is a list, gives no answer and no crash",
          verdict("gemini", '{"event": "result", "result": "oops"}') == "no answer"
          and verdict("gemini", '{"event": "result", "result": {"status": "SUCCESS", "response": ["x"]}}') == "no answer")
    other_model = glog().replace(GEMINI_MODEL, "gemini-4-flash")
    other_mode = glog().replace("request-review", "always-proceed")
    check("log, Gemini: another model or another mode in the log gives no answer, with that reason",
          judge({"exit_code": 0}, safe_read_log("gemini", other_model), "gemini", True)[1].startswith(WRONG_SERVICE)
          and judge({"exit_code": 0}, safe_read_log("gemini", other_mode), "gemini", True)[1].startswith(WRONG_SERVICE)
          and judge({"exit_code": 0}, safe_read_log("gemini", other_model), "gemini", False)[0] == "answer")

    def clog(*events):
        return "\n".join(json.dumps(e) for e in events)
    msg = {"type": "item.completed", "item": {"id": "m", "type": "agent_message", "text": "R01,P,a"}}
    done = {"type": "turn.completed", "usage": {}}
    check("log, GPT: a message and a completed turn give an answer", verdict("gpt", clog(msg, done)) == "answer")
    check("log, GPT: the last message is the final text",
          safe_read_log("gpt", clog({"type": "item.completed", "item": {"id": "a", "type": "agent_message", "text": "first"}}, msg, done))["answer"] == "R01,P,a")
    check("log, GPT: a failed turn stays failed, also when a completed turn follows", verdict("gpt", clog(msg, {"type": "turn.failed"}, done)) == "no answer")
    check("log, GPT: no completed turn gives no answer", verdict("gpt", clog(msg)) == "no answer")
    check("log, GPT: an error notice of the command (for example a retry) does not void a completed turn, and it is recorded",
          verdict("gpt", clog({"type": "error", "message": "Reconnecting... 1/5"}, msg, done)) == "answer"
          and len(safe_read_log("gpt", clog({"type": "error", "message": "Reconnecting... 1/5"}, msg, done))["notices"]) == 1)
    check("log, GPT: a command, a web search or a plan item gives no answer",
          all(verdict("gpt", clog({"type": "item.completed", "item": {"id": "x", "type": t}}, msg, done)) == "no answer"
              for t in ("command_execution", "web_search", "mcp_tool_call", "file_change", "todo_list", "collab_tool_call")))
    check("log, GPT: an unknown event gives no answer", verdict("gpt", clog({"type": "session.created"}, msg, done)) == "no answer")
    check("log, GPT: the Code Mode notice is not a fault, and another error item is recorded",
          verdict("gpt", clog({"type": "item.completed", "item": {"type": "error", "message": CODEX_NOTICE + " x"}}, msg, done)) == "answer"
          and safe_read_log("gpt", clog({"type": "item.completed", "item": {"type": "error", "message": "stream lost"}}, msg, done))["notices"] == ["stream lost"])
    check("log, GPT: an item that is not an object gives no crash",
          verdict("gpt", clog({"type": "item.completed", "item": "oops"}, msg, done)) in ("answer", "no answer"))
    check("judge: a completed answer in the log is used, also when the command ended with an error code or the time limit",
          verdict("gpt", clog(msg, done), exit_code=3) == "answer" and verdict("gpt", clog(msg, done), timed_out=True) == "answer")
    odd = '{"event": "init", "init": {"model": "x"}}\n{"event": "error"}'
    check("judge: a call that ended with an error code or at the time limit gets that reason, also when its log has an unknown part",
          judge({"exit_code": 1}, safe_read_log("gemini", odd), "gemini", True)[1].startswith(TECHNICAL)
          and judge({"timed_out": True, "exit_code": -9}, safe_read_log("gemini", odd), "gemini", True)[1] == "the time limit ended"
          and "known form" in judge({"exit_code": 0}, safe_read_log("gemini", odd), "gemini", True)[1])
    no_model = glog().replace('"model": "' + GEMINI_MODEL + '", ', "")
    check("judge: a Gemini log that names no model is not used", judge({"exit_code": 0}, safe_read_log("gemini", no_model), "gemini", True)[1] == WRONG_SERVICE + " no model")
    check("judge: no completed answer and an error code give no answer with that reason",
          judge({"exit_code": 2}, safe_read_log("gpt", ""))[1] == "the command ended with the exit code 2")

    # --- small parts ---
    g, g_in = command("gemini", "MSG", Path("/x"))
    c, c_in = command("gpt", "MSG", Path("/x"))
    check("command: Gemini gets the message as an argument, the model name, the sandbox and the event log form",
          g_in is None and g[1:3] == ["-p", "MSG"] and GEMINI_MODEL in g and "--sandbox" in g and g[-1] == "stream-json")
    check("command: GPT gets the message on standard input, read-only, with no user settings and no session file",
          c_in == "MSG" and "MSG" not in c and all(x in c for x in ("-s", "read-only", "--ignore-user-config", "--ephemeral", "--json", GPT_MODEL)))
    check("command: each tool feature of Codex is switched off", all(c[c.index(f) - 1] == "--disable" for f in CODEX_OFF) and 'web_search="disabled"' in c)
    os.environ["CODEX_COMPANION_SESSION_ID"] = "must-not-pass"
    env = child_env("gemini")
    check("environment: only the listed variables pass, and the guard switch is set for Gemini only",
          set(env) <= set(ENV_KEEP) | {"AGENT_COUNCIL_SEAT"} and env.get("AGENT_COUNCIL_SEAT") == "1" and "AGENT_COUNCIL_SEAT" not in child_env("gpt"))
    del os.environ["CODEX_COMPANION_SESSION_ID"]
    check("process check: a number that is no process number, a text and the first processes are never 'a call that still runs'",
          not any(call_is_alive({"pid": p, "process_started": "Sat Nov 14 22:04:03 2026"}, "gpt") for p in (0, 1, -5, 2 ** 40, "12", None, True))
          and not call_is_alive({"pid": os.getpid(), "process_started": ""}, "gpt")
          and not call_is_alive({"pid": os.getpid(), "process_started": process_line(os.getpid())[:24]}, "gpt"))

    old_bin, old_thesis, old_parse, old_short = dict(BIN), THESIS, parse_answer, short_test
    work = Path(tempfile.mkdtemp(prefix="rater_selftest_")).resolve()
    try:
        tmp_file = work / "x.json"
        write_json(tmp_file, {"a": "\ud83d lone"})
        check("files: a text with a character that UTF-8 cannot hold is written as an escape, and no temporary file stays",
              "\\ud83d" in tmp_file.read_text() and [p.name for p in work.iterdir()] == ["x.json"])
        (work / "y.csv.tmp12_34").write_text("half")
        clean_tmp(work)
        check("files: a temporary file of a killed start is removed", not (work / "y.csv.tmp12_34").exists())

        # --- the gate: a failure that comes during a short test call gets its own test ---
        ctx = Context(work / "gate", {}, [])
        n_tests = []

        def slow_test(c, r):
            n_tests.append(1)
            if len(n_tests) == 1:
                note_failure(ctx, "gpt", "the answer is empty")  # a second failure, while the first test runs
            return True, ""
        short_test = slow_test
        note_failure(ctx, "gpt", "the answer is empty")
        gate(ctx, "gpt")
        gate(ctx, "gpt")
        gate(ctx, "gpt")
        check("gate: a failure during a short test call is not lost; two failures give two tests, and a third call gives none", len(n_tests) == 2, str(len(n_tests)))
        set_down(ctx, "gpt", "an invented reason")
        try:
            gate(ctx, "gpt")
            check("gate: a rater that is down stops each new call", False)
        except RaterDown:
            check("gate: a rater that is down stops each new call", True)
        ctx2 = Context(work / "gate2", {}, [])
        for _ in range(2):
            note_failure(ctx2, "gemini", "the command ended with the exit code 1")
        note_failure(ctx2, "gemini", "the answer is empty")
        for _ in range(2):
            note_failure(ctx2, "gemini", "the time limit ended")
        ok_before = not ctx2.state["gemini"].down
        note_failure(ctx2, "gemini", "the log shows no completed answer")
        check("outage rule: three calls in a row with an error of the command stop the rater; another kind of failure starts the count again",
              ok_before and ctx2.state["gemini"].down.startswith("3 calls in a row"))
        ctx3 = Context(work / "gate3", {}, [])

        def test_that_downs(c, r):
            set_down(ctx3, "gpt", "stopped while the test ran")
            return True, ""
        short_test = test_that_downs
        note_failure(ctx3, "gpt", "the answer is empty")
        try:
            gate(ctx3, "gpt")
            check("gate: a rater that another call stopped during the short test call gets no more call", False)
        except RaterDown:
            check("gate: a rater that another call stopped during the short test call gets no more call", True)
        ctx4 = Context(work / "gate4", {}, [])
        for reason in ("the time limit ended", "the time limit ended"):
            note_failure(ctx4, "gpt", reason)
        note_answer(ctx4, "gpt")
        for reason in ("the time limit ended", "the command ended with the exit code 1"):
            note_failure(ctx4, "gpt", reason)
        two_ok = not ctx4.state["gpt"].down
        note_failure(ctx4, "gpt", "the time limit ended")
        check("outage rule: an answer starts the count again; a call that ends at the time limit counts", two_ok and bool(ctx4.state["gpt"].down))
        short_test = old_short

        # --- the run logic, with two stand-in programs ---
        stub_dir = work / "bin"
        stub_dir.mkdir()
        for name in ("agy", "codex"):
            (stub_dir / name).write_text(STUB, encoding=UTF8)
            (stub_dir / name).chmod(0o755)
        (work / "driver.py").write_text(DRIVER, encoding=UTF8)
        BIN = {"gemini": str(stub_dir / "agy"), "gpt": str(stub_dir / "codex")}

        def new_folder(tag: str, lock_human: bool = True, base: Path = work) -> Path:
            d = base / tag
            d.mkdir(parents=True)
            write_json(d / "sheet.json", _test_sheet())
            for part in PARTS:
                shutil.copy(real / RULES_FILE[part], d / RULES_FILE[part])
                shutil.copy(real / PROMPT_FILE[part], d / PROMPT_FILE[part])
            rows = [[it["item"], "P", ""] for st in _test_sheet()["stories"] for it in st["items"]]
            with (d / scorer.HUMAN).open("w", newline="", encoding=UTF8) as fh:
                w = csv.writer(fh)
                w.writerow(["item", "label", "note"])
                w.writerows(rows)
            if lock_human:
                out, old = io.StringIO(), sys.stdout
                sys.stdout = out
                try:
                    scorer.cmd_lock(argparse.Namespace(dir=str(d), file=scorer.HUMAN))
                finally:
                    sys.stdout = old
            return d

        def plan(p: dict) -> None:
            write_json(stub_dir / "plan.json", p)
            for f in list(stub_dir.glob("count_*.txt")) + [stub_dir / "calls.jsonl", stub_dir / "child_was_alive.txt"]:
                if f.exists():
                    f.unlink()
            for f in stub_dir.glob("*.away"):
                os.rename(f, f.with_suffix(""))

        def calls(story_only: bool = True) -> list[dict]:
            f = stub_dir / "calls.jsonl"
            rows = [json.loads(x) for x in f.read_text().splitlines()] if f.exists() else []
            return [c for c in rows if not c["ok_call"]] if story_only else rows

        def go(d: Path, **kw) -> tuple[str, str, int | None]:
            ns = argparse.Namespace(dir=str(d), rater=kw.pop("rater", "both"), part=kw.pop("part", "both"),
                                    runs=kw.pop("runs", "1,2"), stories=kw.pop("stories", ""), jobs=kw.pop("jobs", 1),
                                    allow_changed_runner=kw.pop("allow_changed_runner", ""))
            out, old = io.StringIO(), sys.stdout
            sys.stdout = out
            err, code = "", None
            STOP.clear()
            try:
                code = cmd_run(ns, check_git=kw.pop("check_git", False), check_manifest=kw.pop("check_manifest", False),
                               check_guard=kw.pop("check_guard", False), check_smoke=kw.pop("check_smoke", False),
                               waits=kw.pop("waits", (0, 0, 0, 0)), time_limit=kw.pop("time_limit", 20), test_limit=10)
            except Refuse as e:
                err = str(e)
            finally:
                sys.stdout = old
            return out.getvalue(), err, code

        def labels_of(d: Path, name: str) -> list[str]:
            return [r[1] for r in list(csv.reader((d / name).open(encoding=UTF8)))[1:]]

        def story(d: Path, rater: str, part_run: str, sid: str) -> Path:
            return d / "ai_runs" / "calls" / rater / part_run / sid

        def driver(d: Path, jobs: int = 2):
            env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
            return subprocess.Popen([sys.executable, "-B", str(work / "driver.py"), str(Path(__file__).resolve()), str(stub_dir), str(d), str(jobs)],
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env)

        all_ids = [f"R{i:02d}" for i in range(1, 61)]
        files = [label_file_name(p, r, k) for r in RATERS for p in PARTS for k in RUNS]

        # 1. no lock, no call
        d0 = new_folder("nolock", lock_human=False)
        plan({})
        out, err, code = go(d0)
        check("run: refused when the human file is not locked, and no call is made", "not locked" in err and not calls(False))

        # 2. a clean run
        d1 = new_folder("clean")
        plan({})
        out, err, code = go(d1, jobs=3)
        made = calls(False)
        check("run: eight label files are written, and the run ends with the code 0",
              not err and code == 0 and all((d1 / f).is_file() for f in files), err + out[-300:])
        check("run: 120 story calls and 2 short test calls", len(calls()) == 120 and len(made) == 122, str(len(made)))
        check("run: each label file is accepted by the scoring tool", all(len(scorer.parse_label_file(d1 / f, all_ids)) == 60 for f in files))
        check("run: each label is G and no label is X", all(set(labels_of(d1, f)) == {"G"} for f in files))
        check("run: each call ran in an empty folder outside the thesis", all(not c["files_in_cwd"] and str(old_thesis) not in c["cwd"] for c in made))
        check("run: no variable of this session reached a rater",
              all(set(c["env"]) <= set(ENV_KEEP) | {"AGENT_COUNCIL_SEAT", "__CF_USER_TEXT_ENCODING"} for c in made))
        check("run: the guard switch is set for each Gemini call and for no GPT call", all((c["seat"] == "1") == (c["kind"] == "agy") for c in made))
        check("run: the output prints no label line", not re.search(r"R\d{2},[A-Z],", out))
        check("run: GPT gets its message from a file (a whole message, also when this script dies), and no copy of it stays",
              all(c["stdin_is_a_file"] for c in made if c["kind"] == "codex") and not list(d1.rglob("*.message.txt")))
        check("run: the exact message of each story is saved with its hash",
              len(list((d1 / "ai_runs" / "prompts" / "names").glob("*.txt"))) == 15 and (d1 / "ai_runs" / "prompts" / "sha256.json").is_file())
        sd = story(d1, "gemini", "names_run1", "T-01")
        meta = read_json(sd / "attempt1.meta.json")
        check("run: the record of a call holds the times, the model, the mode, the hashes of the message and of the log, and the command line",
              meta["outcome"] == "answer" and meta["model_in_log"] == GEMINI_MODEL and meta["mode_in_log"] == "request-review"
              and len(meta["prompt_sha256"]) == 64 and "<THE MESSAGE>" in meta["command"] and meta["started_utc"].endswith("Z")
              and meta["files_left_in_folder"] == [] and meta["tools_listed_in_log"] == 2
              and meta["events_sha256"] == sha256_file(sd / "attempt1.events.jsonl") and meta["command_version"] == "stub 0.0")
        mark = read_json(sd / "attempt1.started")
        check("run: the marker of a call holds the process number, the start time of the process and the start of the script",
              isinstance(mark["pid"], int) and len(mark["process_started"]) >= 20 and mark["start_id"] == meta["start_id"])
        check("run: the final record of a story names the script that read the answer",
              read_json(sd / "final.json")["read_by_runner_sha256"] == sha256_file(Path(__file__)))
        settings = read_json(d1 / "ai_runs" / "settings.json")
        check("run: the settings record names both models, the limits in effect, and the hashes of the tools at this start",
              settings["settings"]["raters"]["gemini"]["model"] == GEMINI_MODEL and settings["settings"]["raters"]["gpt"]["reasoning_effort"] == GPT_EFFORT
              and settings["settings"]["calls_for_one_story_at_most"] == 4 and settings["settings"]["time_limit_seconds_for_one_call"] == 20
              and len(settings["starts"]) == 1 and len(settings["starts"][0]["runner_sha256"]) == 64
              and "guard_sha256" in settings["starts"][0] and settings["starts"][0]["ended_utc"]
              and settings["starts"][0]["stories_with_no_answer_after_four_calls"] == 0)
        lc = read_json(d1 / "ai_runs" / "label_check.json")
        check("run: label_check.json holds the hash of each label file and of each final record",
              sorted(lc) == sorted(files) and all(lc[f]["sha256"] == sha256_file(d1 / f) and len(lc[f]["final_records"]) == 15 for f in files))
        check("run: the lock is free at the end, its file stays, and git ignores it and a leftover copy of a message",
              (d1 / "ai_runs" / "run.lock").exists() and not lock_is_held(d1 / "ai_runs")
              and (d1 / "ai_runs" / ".gitignore").read_text() == "run.lock\n*.tmp*\n*.message.txt\n")
        check("run: no temporary file stays in the folder", not list(d1.rglob("*.tmp*")))

        # 3. a second start makes no call and changes no file
        before = {f: sha256_file(d1 / f) for f in files}
        plan({"agy": [{"do": "answer", "letter": "P"}], "codex": [{"do": "answer", "letter": "P"}]})
        out, err, code = go(d1)
        check("second start: no call is made and no label file changes",
              not err and not calls(False) and before == {f: sha256_file(d1 / f) for f in files}, err)

        # 4. one call with no answer, then an answer; one cause for each fault
        for fault in ("empty", "exit_no_log", "garbage", "tool", "unknown_step", "error_status", "sleep"):
            d2 = new_folder(f"fault_{fault}")
            plan({"agy": [{"do": fault, "s": 30, "letter": "P"}, {"do": "answer", "letter": "N"}],
                  "codex": [{"do": fault, "s": 30, "letter": "P"}, {"do": "answer", "letter": "N"}]})
            t0 = time.monotonic()
            out, err, code = go(d2, part="names", runs="1", stories="T-01,T-02", time_limit=2)
            took = time.monotonic() - t0
            ok = not err and code == 0
            for r in RATERS:
                fin = read_json(story(d2, r, "names_run1", "T-01") / "final.json")
                fin2 = read_json(story(d2, r, "names_run1", "T-02") / "final.json")
                m1 = read_json(story(d2, r, "names_run1", "T-01") / "attempt1.meta.json")
                ok = (ok and fin["attempts"] == 2 and fin["outcome"] == "answer" and set(fin["labels"].values()) == {"N"}
                      and m1["outcome"] == "no answer" and fin2["attempts"] == 1)
                if fault == "tool":
                    ok = ok and m1["tool_steps"] and "tool step" in m1["reason"]
                if fault == "unknown_step":
                    ok = ok and m1["not_in_the_known_form"] and "known form" in m1["reason"]
                if fault == "sleep":
                    ok = ok and m1["timed_out"] is True
                if fault == "exit_no_log":
                    ok = ok and "exit code 2" in m1["reason"] and "429" in m1["stderr_first_line"]
            check(f"fault '{fault}': call 1 gives no answer, one short test call follows, call 2 is used, the next story needs no test",
                  ok and len(calls()) == 6 and len(calls(False)) == 6 + 2 + 2, err + out[-300:] + str(len(calls(False))))
            if fault == "sleep":
                time.sleep(4.5)
                check("time limit: the call ends at the limit, and its child process is ended with it",
                      took < 16 and not (stub_dir / "child_was_alive.txt").exists(), f"{took:.1f}")

        # 5. logs that give an answer although something is unusual
        for mild in ("thinking_step", "exit_after_answer", "leave_file", "surrogate"):
            d2 = new_folder(f"mild_{mild}")
            plan({"agy": [{"do": mild, "letter": "P"}], "codex": [{"do": mild, "letter": "P"}]})
            out, err, code = go(d2, part="names", runs="1", stories="T-01")
            fg, fc = (read_json(story(d2, r, "names_run1", "T-01") / "final.json") for r in RATERS)
            mg = read_json(story(d2, "gemini", "names_run1", "T-01") / "attempt1.meta.json")
            ok = not err and code == 0 and all(f and f["attempts"] == 1 and set(f["labels"].values()) == {"P"} for f in (fg, fc))
            if mild == "leave_file":
                ok = ok and mg["files_left_in_folder"] == ["left.txt"]
            if mild == "exit_after_answer":
                ok = ok and mg["exit_code"] == 3
            if mild == "thinking_step":
                ok = ok and len(read_json(story(d2, "gpt", "names_run1", "T-01") / "attempt1.meta.json")["notices"]) == 1
            check(f"'{mild}': the completed answer of the first call is used", ok, err + out[-300:])

        # 6. the last message of a GPT call is its answer; an earlier message is not used
        d5 = new_folder("forms")
        plan({"agy": [{"do": "answer", "line": "{item},p,lower case"}], "codex": [{"do": "preamble", "letter": "P"}]})
        out, err, code = go(d5, part="names", runs="1", stories="T-04")
        fg = read_json(story(d5, "gemini", "names_run1", "T-04") / "final.json")
        fc = read_json(story(d5, "gpt", "names_run1", "T-04") / "final.json")
        check("forms: lower case letters give X, with one call (a full answer is not sent again)",
              fg["outcome"] == "answer" and fg["attempts"] == 1 and set(fg["labels"].values()) == {"X"})
        check("forms: an earlier message of the same GPT call (with N) is not used; the last message (with P) is",
              set(fc["labels"].values()) == {"P"})

        # 7. four calls with no answer give X; a fifth call is never made
        d3 = new_folder("four")
        plan({"agy": [{"do": "empty"}], "codex": [{"do": "empty"}]})
        out, err, code = go(d3, part="support", runs="2", stories="T-02")
        ok = not err and len(calls()) == 8 and "because four calls gave no answer: 2" in out
        for r in RATERS:
            fin = read_json(story(d3, r, "support_run2", "T-02") / "final.json")
            ok = ok and fin["outcome"] == "no answer" and set(fin["labels"].values()) == {"X"} and fin["attempts"] == 4
        check("four calls with no answer: each item of the story gets X, exactly four calls are made, and the output says it", ok, err + out[-300:])
        plan({})
        out, err, code = go(d3, part="support", runs="2", stories="T-02")
        check("four calls with no answer: a later start does not send the story again", not err and not calls(False))
        check("one story only: no label file is written while other stories are open",
              not (d3 / label_file_name("support", "gemini", 2)).exists() and "not complete" in out)
        (story(d3, "gpt", "support_run2", "T-02") / "attempt5.started").write_text("{}")
        (story(d3, "gpt", "support_run2", "T-02") / "final.json").unlink()
        out, err, code = go(d3, rater="gpt", part="support", runs="2", stories="T-02")
        check("a record of a fifth call stops the start with a plain reason", "four calls at most" in err and not calls(False), err)

        # 8. a rater that fails the short test call after a call with no answer is stopped, and no X is written
        d9 = new_folder("down")
        plan({"agy": [{"do": "empty"}], "agy_ok": ["OK", "EXIT"], "codex": [{"do": "answer"}]})
        out, err, code = go(d9, part="names", runs="1")
        gem = d9 / "ai_runs" / "calls" / "gemini" / "names_run1"
        check("outage: one call with no answer, then the short test call fails: the rater stops with the code 3",
              not err and code == 3 and len([c for c in calls() if c["kind"] == "agy"]) == 1 and "No X was written" in out, err + out[-400:])
        check("outage: no final record and no label file is written for the rater that stopped; the other rater is complete",
              not list(gem.glob("*/final.json")) and not (d9 / label_file_name("names", "gemini", 1)).exists()
              and (d9 / label_file_name("names", "gpt", 1)).exists())
        plan({})
        out, err, code = go(d9, part="names", runs="1")
        f1 = read_json(gem / "T-01" / "final.json")
        check("outage: a later start continues; the call of the outage counts as call 1 of its story",
              not err and code == 0 and (d9 / label_file_name("names", "gemini", 1)).exists() and f1["attempts"] == 2
              and len([c for c in calls() if c["kind"] == "agy"]) == 15, err + out[-300:])

        # 9. an outage that the short test call does not show: three calls in a row with an error stop the rater
        d9b = new_folder("down3")
        plan({"agy": [{"do": "exit_no_log"}], "codex": [{"do": "answer"}]})
        out, err, code = go(d9b, part="names", runs="1", jobs=1)
        gem = d9b / "ai_runs" / "calls" / "gemini" / "names_run1"
        n_meta = len(list(gem.glob("*/attempt*.meta.json")))
        check("outage that the short test does not show: after three calls in a row with an error the rater stops, and no X is written",
              not err and code == 3 and n_meta == 3 and not list(gem.glob("*/final.json")) and "3 calls in a row" in out, f"{code} {n_meta} " + out[-300:])
        d9c = new_folder("down3_parallel")
        plan({"agy": [{"do": "exit_no_log"}], "codex": [{"do": "answer"}]})
        out, err, code = go(d9c, part="names", runs="1", jobs=3)
        gem = d9c / "ai_runs" / "calls" / "gemini" / "names_run1"
        n_meta = len(list(gem.glob("*/attempt*.meta.json")))
        check("the same with three calls at the same time: the rater stops after 3 to 5 calls, and no X is written",
              code == 3 and 3 <= n_meta <= 5 and not list(gem.glob("*/final.json")), f"{code} {n_meta}")

        # 10. a Gemini story call whose log names another model stops the rater; the call counts
        d9d = new_folder("othermodel")
        plan({"agy_model": "gemini-4-flash", "codex": [{"do": "answer"}]})
        out, err, code = go(d9d, rater="gemini", part="names", runs="1", check_guard=True)
        check("another model in the log of the short test call: the run does not start", "did not pass the short test call" in err and "the model" in err, err)
        plan({"agy_model": "gemini-4-flash"})
        ctx = Context(d9d / "ai_runs", load_inputs(d9d)["prompts"], load_inputs(d9d)["stories"], waits=(0, 0, 0, 0), time_limit=20, check_mode=True)
        try:
            do_story(ctx, "gemini", "names", 1, "T-01")
            raised = False
        except RaterDown:
            raised = True
        m1 = read_json(story(d9d, "gemini", "names_run1", "T-01") / "attempt1.meta.json")
        check("another model in the log of a story call: the answer is not used, the call counts, and the rater stops",
              raised and m1["outcome"] == "no answer" and m1["reason"].startswith(WRONG_SERVICE) and ctx.state["gemini"].down
              and not (story(d9d, "gemini", "names_run1", "T-01") / "final.json").exists())

        d9e = new_folder("stop_in_gate")
        plan({})
        ins = load_inputs(d9e)
        ctx = Context(d9e / "ai_runs", ins["prompts"], ins["stories"], waits=(0, 0, 0, 0), time_limit=20, check_mode=False)
        note_failure(ctx, "gpt", "the answer is empty")

        def test_that_stops(c, r):
            STOP.set()
            return True, ""
        short_test = test_that_stops
        res_stop = do_story(ctx, "gpt", "names", 1, "T-01")
        short_test = old_short
        STOP.clear()
        check("a stop during the short test call before a call: the call does not start",
              res_stop is None and not calls(False) and not list(story(d9e, "gpt", "names_run1", "T-01").glob("attempt*")))
        old_line = globals()["process_line"]

        def no_ps(pid):
            raise OSError("an invented fault of this computer")
        globals()["process_line"] = no_ps
        try:
            do_story(ctx, "gpt", "names", 1, "T-02")
            failed = False
        except OSError:
            failed = True
        globals()["process_line"] = old_line
        sdm = story(d9e, "gpt", "names_run1", "T-02")
        time.sleep(0.3)
        check("a command that started but whose marker could not be completed: the command is ended, and the call counts",
              failed and (sdm / "attempt1.started").is_file() and "pid" not in read_json(sdm / "attempt1.started")
              and settle(ctx, "gpt", "names", 1, "T-02")[1] == 1)

        # 11. a rater whose command does not start
        d10 = new_folder("nostart")
        plan({})
        BIN["gemini"] = str(stub_dir / "not_there")
        out, err, code = go(d10, rater="gemini", part="names", runs="1")
        BIN["gemini"] = str(stub_dir / "agy")
        check("command that does not start: the run stops before any story, and no call is counted",
              "did not pass the short test call" in err and not (d10 / "ai_runs" / "calls").exists(), err)
        d10b = new_folder("nostart_midrun")
        plan({"agy": [{"do": "delete_binary"}], "codex": [{"do": "answer"}]})
        out, err, code = go(d10b, rater="gemini", part="names", runs="1", stories="T-01,T-02")
        sd2 = story(d10b, "gemini", "names_run1", "T-02")
        check("command that is gone in the middle of a run: the rater stops, the call that did not start is not counted and leaves no file",
              not err and code == 3 and "did not start" in out and not list(sd2.glob("*"))
              and read_json(story(d10b, "gemini", "names_run1", "T-01") / "final.json")["attempts"] == 1, f"{code} " + out[-300:])
        plan({})
        out, err, code = go(d10b, rater="gemini", part="names", runs="1", stories="T-01,T-02")
        check("command that is back: a later start sends the open story as its call 1", not err and code == 0 and read_json(sd2 / "final.json")["attempts"] == 1)

        # 12. calls of an earlier start that have no record
        d4 = new_folder("lost")
        sd = story(d4, "gemini", "names_run1", "T-03")
        sd.mkdir(parents=True)
        for n in (1, 3):
            (sd / f"attempt{n}.started").write_text("x")
        plan({"agy": [{"do": "empty"}]})
        out, err, code = go(d4, rater="gemini", part="names", runs="1", stories="T-03")
        fin = read_json(sd / "final.json")
        check("lost calls: markers of call 1 and call 3 mean that three calls are used, so one call is left",
              not err and fin["outcome"] == "no answer" and len(calls()) == 1
              and read_json(sd / "attempt2.meta.json")["reason"] == "the script stopped during this call", err)
        d4b = new_folder("lost_with_log")
        sd = story(d4b, "gpt", "names_run1", "T-03")
        sd.mkdir(parents=True)
        (sd / "attempt1.started").write_text(json.dumps({"started_utc": now_utc(), "pid": 0, "command_version": "old 9.9", "start_id": "s0"}))
        (sd / "attempt1.events.jsonl").write_text(clog({"type": "item.completed", "item": {
            "id": "m", "type": "agent_message", "text": "\n".join(f"R{n:02d},N,from the log" for n in range(9, 13))}}, done))
        (sd / "attempt1.events.jsonl.message.txt").write_text("the copy of the message that a killed start left")
        plan({"codex": [{"do": "answer", "letter": "P"}]})
        out, err, code = go(d4b, rater="gpt", part="names", runs="1", stories="T-03")
        fin, m1 = read_json(sd / "final.json"), read_json(sd / "attempt1.meta.json")
        check("lost call with a completed answer in its log: the answer is used and the story is not sent again",
              not err and not calls() and fin["attempts"] == 1 and set(fin["labels"].values()) == {"N"}
              and m1["recorded_by_a_later_start"] is True and m1["command_version"] == "old 9.9" and m1["start_id"] == "s0"
              and not (sd / "attempt1.events.jsonl.message.txt").exists(), err + str(fin))
        log_text = (sd / "attempt1.events.jsonl").read_text()
        crashed = []
        for cut in range(0, len(log_text) + 1, 7):
            dcut = work / "cut" / str(cut)
            sdc = story(dcut, "gpt", "names_run1", "T-03")
            sdc.mkdir(parents=True)
            (sdc / "attempt1.started").write_text("{}")
            (sdc / "attempt1.events.jsonl").write_text(log_text[:cut])
            cctx = Context(dcut / "ai_runs", real_inputs["prompts"], _test_sheet()["stories"])
            cctx.prompts = {"names": {"T-03": "x"}}
            try:
                meta = recover_call(cctx, "gpt", "names", 1, "T-03", 1, "x")
                if (meta["outcome"] == "answer") != (cut == len(log_text) or log_text[:cut].rstrip().endswith('"usage": {}}')):
                    crashed.append(cut)
            except Exception:  # noqa: BLE001
                crashed.append(cut)
        check("a log that is cut at any place never gives a crash and never gives an answer before its turn is complete", not crashed, str(crashed[:5]))
        d4c = new_folder("answer_no_final")
        plan({"codex": [{"do": "answer", "letter": "N"}]})
        go(d4c, rater="gpt", part="names", runs="1", stories="T-03")
        sd = story(d4c, "gpt", "names_run1", "T-03")
        (sd / "final.json").unlink()
        plan({"codex": [{"do": "answer", "letter": "P"}]})
        out, err, code = go(d4c, rater="gpt", part="names", runs="1", stories="T-03")
        check("stop between the record of the answer and the final record: the answer is used and the story is not sent again",
              not err and not calls() and set(read_json(sd / "final.json")["labels"].values()) == {"N"}, err)
        for damaged in ("{half a fi", "{}", json.dumps({**read_json(sd / "final.json"), "labels": {"R09": "Q"}})):
            (sd / "final.json").write_text(damaged)
            out, err, code = go(d4c, rater="gpt", part="names", runs="1", stories="T-03")
            check(f"a final record that is damaged ({damaged[:12]}...) is made again from the record of the call, with no new call",
                  not err and not calls() and set(read_json(sd / "final.json")["labels"].values()) == {"N"}, err)

        # 13. a call of an earlier start whose command still runs
        d4d = new_folder("orphan")
        inputs = load_inputs(d4d)
        sd = story(d4d, "gpt", "names_run1", "T-03")
        sd.mkdir(parents=True)

        def orphan(n: int):
            argv, text = command("gpt", inputs["prompts"]["names"]["T-03"], work)
            with open(sd / f"attempt{n}.events.jsonl", "wb") as fo:
                proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=fo, stderr=subprocess.DEVNULL, start_new_session=True,
                                        env=child_env("gpt"), cwd=work)
            proc.stdin.write(text.encode(UTF8))
            proc.stdin.close()
            time.sleep(0.4)
            write_json(sd / f"attempt{n}.started", {"started_utc": now_utc(), "pid": proc.pid, "process_started": process_line(proc.pid)[:24],
                                                    "command_version": "stub 0.0", "start_id": "earlier"})
            return proc
        plan({"codex": [{"do": "slow", "s": 2.5, "letter": "N"}, {"do": "answer", "letter": "P"}]})
        proc = orphan(1)
        info = read_json(sd / "attempt1.started")
        check("process check: the same process number with another start time is not the call of the earlier start",
              call_is_alive(info, "gpt") and not call_is_alive({**info, "process_started": "Thu Jan  1 02:00:00 1970"}, "gpt"))
        octx = Context(d4d / "ai_runs", inputs["prompts"], inputs["stories"], time_limit=20, check_mode=False)
        threading.Timer(0.7, STOP.set).start()
        waited = recover_call(octx, "gpt", "names", 1, "T-03", 1, inputs["prompts"]["names"]["T-03"])
        STOP.clear()
        check("Ctrl-C during the wait for a call of an earlier start: the call is not ended and gets no record yet",
              waited is None and call_is_alive(info, "gpt") and not (sd / "attempt1.meta.json").exists())
        out, err, code = go(d4d, rater="gpt", part="names", runs="1", stories="T-03", time_limit=20)
        proc.wait(timeout=10)
        fin = read_json(sd / "final.json")
        check("a call of an earlier start that still runs: the start waits for it, uses its answer, and sends nothing",
              not err and code == 0 and len(calls()) == 1 and fin["attempts"] == 1 and set(fin["labels"].values()) == {"N"}
              and "waiting for call 1" in out, err + out[-300:])
        d4e = new_folder("orphan_slow")
        inputs = load_inputs(d4e)
        sd = story(d4e, "gpt", "names_run1", "T-03")
        sd.mkdir(parents=True)
        plan({"codex": [{"do": "slow", "s": 60, "letter": "N"}, {"do": "answer", "letter": "P"}]})
        proc = orphan(1)
        out, err, code = go(d4e, rater="gpt", part="names", runs="1", stories="T-03", time_limit=3)
        gone = proc.wait(timeout=10) is not None
        fin = read_json(sd / "final.json")
        check("a call of an earlier start that runs past its time limit is ended; it counts, and call 2 is used",
              not err and code == 0 and gone and fin["attempts"] == 2 and set(fin["labels"].values()) == {"P"}, err + out[-300:])

        # 14. only one start at a time
        d11 = new_folder("twostarts")
        plan({})
        with RunLock(d11 / "ai_runs"):
            check("status: it says that a start uses the folder", lock_is_held(d11 / "ai_runs"))
            out, err, code = go(d11, part="names", runs="1")
            st = read_json(d11 / "ai_runs" / "settings.json")
        check("two starts: the second start is refused and makes no call", "Another start" in err and not calls(False) and st is None, err)

        # 15. Ctrl-C and a stop signal stop the run; the open calls count; a later start continues
        for signame, sig in (("Ctrl-C", signal.SIGINT), ("the signal TERM", signal.SIGTERM)):
            d12 = new_folder("stop_" + signame.replace(" ", "_"))
            plan({"agy": [{"do": "slow", "s": 3}], "codex": [{"do": "slow", "s": 3}]})
            proc = driver(d12)
            time.sleep(1.8)
            sent = time.time()
            proc.send_signal(sig)
            try:
                out, _ = proc.communicate(timeout=20)
            except subprocess.TimeoutExpired:
                proc.kill()
                out, _ = proc.communicate()
            after = [c for c in calls() if c["time"] > sent + 0.3]
            started = len(list((d12 / "ai_runs" / "calls").rglob("attempt*.started")))
            stopped = [read_json(p) for p in (d12 / "ai_runs" / "calls").rglob("attempt1.meta.json")]
            check(f"{signame}: the run ends in a few seconds with the code 130, and no call starts after the stop",
                  proc.returncode == 130 and not after and "The run was stopped" in out, f"code {proc.returncode}, later calls {len(after)}: {out[-300:]}")
            check(f"{signame}: each call that was open is recorded as a call with no answer",
                  started == 4 and len(stopped) == 4 and all(m["outcome"] == "no answer" and "stopped" in m["reason"] for m in stopped),
                  f"{started} {len(stopped)} {out[-300:]}")
            plan({})
            out, err, code = go(d12, part="names", runs="1")
            check(f"{signame}: a later start continues and completes; a stopped story used two calls",
                  not err and code == 0 and (d12 / label_file_name("names", "gpt", 1)).exists()
                  and sorted({read_json(p)["attempts"] for p in (d12 / "ai_runs" / "calls").rglob("final.json")}) == [1, 2], err + out[-300:])

        # 16. a fault of the script in one call: exit code 1, the other calls are recorded, no answer is lost
        d15 = new_folder("worker_error")
        plan({})

        def boom(answer, item_ids, letters):
            if item_ids[0] == "R05":
                raise RuntimeError("an invented fault of the script")
            return old_parse(answer, item_ids, letters)
        parse_answer = boom
        out, err, code = go(d15, rater="gpt", part="names", runs="1", stories="T-01,T-02,T-03")
        parse_answer = old_parse
        sd2 = story(d15, "gpt", "names_run1", "T-02")
        check("a fault of the script in one call: the exit code is 1, the error is printed, and the answer of that call stays on disk",
              code == 1 and "ERROR in a call" in out and (sd2 / "answer.txt").is_file() and not (sd2 / "final.json").exists()
              and read_json(story(d15, "gpt", "names_run1", "T-01") / "final.json")["outcome"] == "answer", f"{code} " + out[-300:])
        plan({})
        out, err, code = go(d15, rater="gpt", part="names", runs="1", stories="T-01,T-02,T-03")
        check("after the fault is corrected: the stored answer is used, and only the story that was not sent is sent",
              not err and code == 0 and len(calls()) == 1 and read_json(sd2 / "final.json")["attempts"] == 1, err + str(len(calls())))

        # 17. a label file that differs is never replaced; a label file with open stories stops the start
        d6 = new_folder("differs")
        plan({})
        go(d6, rater="gpt", part="names", runs="1")
        target = d6 / label_file_name("names", "gpt", 1)
        target.write_text(target.read_text().replace("R01,G", "R01,P"))
        out, err, code = go(d6, rater="gpt", part="names", runs="1")
        st = read_json(d6 / "ai_runs" / "settings.json")["starts"][-1]
        check("label file: a file that differs from the stored answers is refused and left as it is, and the start record says why",
              "never written again" in err and "R01,P" in target.read_text() and "never written again" in st.get("refused", ""))
        d6b = new_folder("early_file")
        (d6b / label_file_name("names", "gpt", 1)).write_text("item,label,note\n")
        plan({})
        out, err, code = go(d6b, rater="gpt", part="names", runs="1")
        check("label file: a file that exists while its stories are open stops the start before any call",
              "no final record" in err and not calls(False), err)

        class NoScorer:
            N_ITEMS = 60
            Refuse = scorer.Refuse

            @staticmethod
            def parse_label_file(path, ids):
                raise scorer.Refuse("an invented refusal")
        target.unlink()
        ctx = Context(d6 / "ai_runs", load_inputs(d6)["prompts"], load_inputs(d6)["stories"])
        try:
            write_label_file(d6, ctx, "gpt", "names", 1, NoScorer, all_ids)
            refused = False
        except Refuse as e:
            refused = "does not accept" in str(e)
        check("label file: a file that the scoring tool does not accept is not written", refused and not target.exists())

        # 18. the short test call
        d7 = new_folder("preflight")
        plan({"agy_ok": ["NO"]})
        out, err, code = go(d7)
        st = read_json(d7 / "ai_runs" / "settings.json")["starts"][-1]
        check("short test call: an answer that is not OK stops the run before any story is sent, and the start record says why",
              "did not pass the short test call" in err and "not the word OK" in err and not calls() and "short test" in st.get("refused", ""), err)
        ctx = Context(d7 / "x", {}, [], check_mode=True)
        plan({"agy_mode": "auto"})
        ok2, why2 = short_test(ctx, "gemini")
        plan({})
        ok3, why3 = short_test(ctx, "gemini")
        check("short test call: another mode in the Gemini log fails it; the tested model and mode pass", not ok2 and "mode" in why2 and ok3, f"{why2} | {why3}")

        # 19. options
        d8 = new_folder("options")
        plan({})
        bad = [go(d8, jobs=0)[1], go(d8, jobs=9)[1], go(d8, runs="abc")[1], go(d8, runs="1,,2")[1], go(d8, runs="3")[1], go(d8, stories="T-99")[1]]
        check("options: --jobs outside 1 to 3, bad --runs and an unknown story are refused with a plain reason, and no call is made",
              all(bad) and not calls(False), str(bad))

        # 20. the smoke test must have passed with this script, these versions, these texts, the long message and the time limit
        good = {"passed": True, "runner_sha256": sha256_file(Path(__file__)), "versions": {"gemini": "stub 0.0", "gpt": "stub 0.0"},
                "raters": ["gemini", "gpt"], "pad_kb": 90, "time_limit_seconds": TIME_LIMIT_S, "instrument_sha256": instrument_hashes(d8)}
        (d8 / "ai_runs_smoke" / "x").mkdir(parents=True)
        plan({})
        out, err, code = go(d8, part="names", runs="1", stories="T-01", check_smoke=True)
        check("smoke record: the run is refused when no smoke test has passed", "smoke" in err and not calls(False), err)
        wrong = {"passed": False, "runner_sha256": "0" * 64, "versions": {"gemini": "stub 0.0", "gpt": "old 1.0"}, "raters": ["gpt"],
                 "pad_kb": 0, "time_limit_seconds": 5, "instrument_sha256": {}}
        for key, value in wrong.items():
            write_json(d8 / "ai_runs_smoke" / "x" / "result.json", {**good, key: value})
            out, err, code = go(d8, part="names", runs="1", stories="T-01", check_smoke=True)
            check(f"smoke record: a smoke test with another value of '{key}' does not count", "smoke" in err and not calls(False), err)
        write_json(d8 / "ai_runs_smoke" / "x" / "result.json", good)
        out, err, code = go(d8, part="names", runs="1", stories="T-01", check_smoke=True)
        check("smoke record: with a smoke test that passed, the run starts", not err and len(calls()) == 2, err)

        # 21. the manifest
        d13 = new_folder("manifest")
        (d13 / "ai_rater_procedure.md").write_text("an invented procedure page\n")
        man = {"labelling_rules_sha256": sha256_file(d13 / "labelling_rules.md"), "sha256": {
            **{f: sha256_file(d13 / f) for f in ("sheet.json", "names_rules.md", "ai_rater_prompt_names.md", "ai_rater_prompt.md",
                                                 "ai_rater_procedure.md")},
            "scoring_tool": sha256_file(HERE / "score_study2_review.py"), "ai_rater_runner": "0" * 64}}
        write_json(d13 / "manifest.json", man)
        plan({})
        out, err, code = go(d13, part="names", runs="1", stories="T-01", check_manifest=True)
        check("manifest: a script that differs from the record is refused", "ai_rater_runner" in err and not calls(False), err)
        out, err, code = go(d13, part="names", runs="1", stories="T-01", check_manifest=True, allow_changed_runner="a fault was corrected")
        st = read_json(d13 / "ai_runs" / "settings.json")["starts"][-1]
        check("manifest: with a reason, the start is allowed and the reason and both hashes are recorded",
              not err and st["runner_changed_after_the_manifest"]["reason"] == "a fault was corrected" and "NOTE" in out, err)
        del man["sha256"]["ai_rater_runner"]
        write_json(d13 / "manifest.json", man)
        out, err, code = go(d13, part="names", runs="1", stories="T-02", check_manifest=True, allow_changed_runner="a reason")
        check("manifest: a record with no hash of the script is refused, also with a reason", "holds no hash of this script" in err, err)
        man["sha256"]["ai_rater_runner"] = sha256_file(Path(__file__))
        man["sha256"]["sheet.json"] = "0" * 64
        write_json(d13 / "manifest.json", man)
        out, err, code = go(d13, part="names", runs="1", stories="T-02", check_manifest=True)
        check("manifest: a sheet that differs from the record is refused", "sheet.json is not the file" in err, err)

        # 22. the commit of the human file (a small git repository of its own)
        repo = work / "repo"
        repo.mkdir()
        THESIS = repo

        def g(*a):
            return subprocess.run([GIT, "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.com", *a], capture_output=True, text=True)
        g("init", "-q")
        d16 = new_folder("review", base=repo)

        def lock_check() -> str:
            try:
                require_human_lock(d16, True)
                return ""
            except Refuse as e:
                return str(e)
        r1 = lock_check()
        g("add", "review/locks.json")
        g("commit", "-q", "-m", "lock only")
        r2 = lock_check()
        g("add", "review")
        g("commit", "-q", "-m", "human file and lock")
        r3 = lock_check()
        old_cwd = os.getcwd()
        os.chdir(d16)
        try:
            r3b = ""
            try:
                require_human_lock(Path("."), True)
            except Refuse as e:
                r3b = str(e)
        finally:
            os.chdir(old_cwd)
        (d16 / "locks.json").unlink()
        rows = (d16 / scorer.HUMAN).read_text().replace("R01,P", "R01,N")
        (d16 / scorer.HUMAN).write_text(rows)
        out, old = io.StringIO(), sys.stdout
        sys.stdout = out
        try:
            scorer.cmd_lock(argparse.Namespace(dir=str(d16), file=scorer.HUMAN))
        finally:
            sys.stdout = old
        r4 = lock_check()
        g("add", "review/" + scorer.HUMAN)
        g("commit", "-q", "-m", "new human file, old lock")
        r5 = lock_check()
        THESIS = old_thesis
        check("commit check: a locked human file that is in no commit is refused", "in no commit" in r1 and "in no commit" in r2, r1 + " | " + r2)
        check("commit check: a locked and committed human file passes, also with a relative folder path", r3 == "" and r3b == "", r3 + " | " + r3b)
        check("commit check: a new lock or a new human file that the last commit does not hold is refused", bool(r4) and bool(r5), r4 + " | " + r5)

        # 23. a sheet that changed after a start
        d14 = new_folder("changed_sheet")
        plan({})
        go(d14, part="names", runs="1", stories="T-01")
        sheet = read_json(d14 / "sheet.json")
        sheet["stories"][1]["items"][0]["text"] += " changed"
        (d14 / "sheet.json").write_text(json.dumps(sheet))
        plan({})
        out, err, code = go(d14, part="names", runs="1", stories="T-02")
        check("changed sheet: a message that is not equal to the stored message stops the start, and no call is made",
              "instrument changed" in err and not calls(False), err)

        # 24. status prints counts only
        out, old = io.StringIO(), sys.stdout
        sys.stdout = out
        try:
            cmd_status(argparse.Namespace(dir=str(d1)))
        finally:
            sys.stdout = old
        text = out.getvalue()
        check("status: eight lines with counts, and no label", text.count("stories with an answer: 15") == 8 and not re.search(r"R\d{2},", text))

        # 25. the smoke data, the memory test and the smoke command
        sheets, expected = smoke_sheet(5)
        rules = sections_1_to_6((real / RULES_FILE["support"]).read_text(encoding=UTF8), "r")
        template = template_below_line((real / PROMPT_FILE["support"]).read_text(encoding=UTF8), "p")
        sp = build_prompt("support", sheets["support"]["stories"][0], rules, template)
        check("smoke data: six invented items for each part, and a long invented file when asked",
              len(expected["names"]) == 6 and len(expected["support"]) == 6 and len(sp) > 5000 + len(rules) and "ExampleApp" in sp)
        plan({"agy_ok": ["OK", "OK"]})
        m1 = memory_test(Context(work / "mem1", {}, [], check_mode=False), "gemini")
        plan({"agy_ok": ["OK", "NONE"]})
        m2 = memory_test(Context(work / "mem2", {}, [], check_mode=False), "gemini")
        check("memory test: it passes only when the second call answers NONE", m1["passed"] is False and m2["passed"] is True and len(m2["calls"]) == 2)

        def smoke(d: Path, p: dict) -> tuple[int, str, dict]:
            plan(p)
            out, old = io.StringIO(), sys.stdout
            sys.stdout = out
            STOP.clear()
            try:
                code = cmd_smoke(argparse.Namespace(dir=str(d), rater="both", pad_kb=3, time_limit=20), check_guard=False, waits=(0, 0, 0, 0))
            finally:
                sys.stdout = old
            folder = sorted((d / "ai_runs_smoke").iterdir())[-1]
            return code, out.getvalue(), read_json(folder / "result.json")
        d17 = new_folder("smoke")
        code, out, res = smoke(d17, {"agy_ok": ["OK", "OK", "NONE"], "codex_ok": ["OK", "OK", "NONE"], "agy": [{"do": "answer"}], "codex": [{"do": "answer"}]})
        check("smoke command: the calls run; the result holds the length of the long file, the time limit and the hashes of the texts",
              res["pad_kb"] == 3 and res["time_limit_seconds"] == 20 and res["instrument_sha256"] == instrument_hashes(d17)
              and res["tool_steps_in_all_logs"] == 0 and len(res["calls_for_each_story"]) == 4 and "smoke test:" in out, out[-300:])
        time.sleep(1.1)
        code, out, res = smoke(d17, {"agy_ok": ["OK", "OK", "NONE"], "codex_ok": ["OK", "OK", "NONE"],
                                     "agy": [{"do": "tool"}, {"do": "answer"}], "codex": [{"do": "answer"}]})
        time.sleep(1.1)
        code2, out2, res2 = smoke(d17, {"agy_ok": ["OK", "OK", "NONE"], "codex_ok": ["OK", "OK", "NONE"],
                                        "agy": [{"do": "answer"}], "codex": [{"do": "empty"}, {"do": "answer"}]})
        check("smoke command: a story that needs two calls fails the smoke test", code2 == 1 and res2["passed"] is False, f"{code2}")
        check("smoke command: a tool step in the log of any call fails the smoke test, also when a later call answers",
              code == 1 and res["passed"] is False and res["tool_steps_in_all_logs"] >= 1, f"{code} {res}")
    finally:
        BIN, THESIS, parse_answer, short_test = old_bin, old_thesis, old_parse, old_short
        STOP.clear()
        shutil.rmtree(work, ignore_errors=True)

    bad = [r for r in results if not r[1]]
    for name, ok, detail in results:
        if not ok:
            print(f"FAIL: {name}" + (f" [{detail[:400]}]" if detail else ""))
    print(f"self-test: {len(results) - len(bad)} of {len(results)} checks pass")
    return 1 if bad else 0


# ---------------------------------------------------------------------------
def main() -> None:
    ap = argparse.ArgumentParser(description="Run the two AI raters of the blinded review of Study 2.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("self-test", "smoke", "run", "status"):
        sp = sub.add_parser(name)
        sp.add_argument("--dir", default=str(DEFAULT_DIR), help="the review folder (another folder is for tests only)")
        if name in ("smoke", "run"):
            sp.add_argument("--rater", choices=("gemini", "gpt", "both"), default="both")
        if name == "smoke":
            sp.add_argument("--pad-kb", type=int, default=SMOKE_PAD_KB,
                            help="length of the invented long file of part 2, in thousands of characters (the run needs the default)")
            sp.add_argument("--time-limit", type=float, default=TIME_LIMIT_S,
                            help="time limit of one call, in seconds (the run needs the default)")
        if name == "run":
            sp.add_argument("--part", choices=("names", "support", "both"), default="both")
            sp.add_argument("--runs", default="1,2", help="1, 2 or 1,2")
            sp.add_argument("--stories", default="", help="story ids (for example KC-S2-003) with commas between them; default: all")
            sp.add_argument("--jobs", type=int, default=DEFAULT_JOBS, help=f"calls of one rater at the same time, 1 to {DEFAULT_JOBS}")
            sp.add_argument("--allow-changed-runner", default="", metavar="REASON",
                            help="start although this script differs from the record in manifest.json; the reason is recorded")
    args = ap.parse_args()
    if args.cmd in ("smoke", "run"):
        install_stop_handlers()
    try:
        code = {"self-test": cmd_self_test, "smoke": cmd_smoke, "run": cmd_run, "status": cmd_status}[args.cmd](args)
    except Refuse as e:
        say(f"STOP: {e}")
        raise SystemExit(2) from None
    raise SystemExit(code)


if __name__ == "__main__":
    main()
