"""One run at a time, in a worker thread.

The worker thread changes plain Python data only. A page reads that data with
a timer. The page compares the version number and redraws only when it
changed. A run goes on when the browser tab is closed.
"""

from __future__ import annotations

import copy
import dataclasses
import inspect
import json
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from secreqgen_gui import __version__, pipeline_boards, storage
from secreqgen_gui.settings import expand_path, load_settings, make_config

SHOWN_SKIPPED = 5  # the skipped files that the manifest names
TESTED_THREAT_SET = "threats_v5b_labels"
TESTED_REQUIREMENTS_SET = "s1_word_3000"
_SET_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")

# One lock for the start check of a run and of a context job. It is always
# taken first. The other locks are taken for a short time inside it, never
# the other way round. So two starts never wait for each other.
START_LOCK = threading.RLock()


@dataclass
class StoryState:
    """The state of one story in a run."""

    id: str
    title: str
    # "waiting", "running", "done", "failed" or "not run"
    state: str = "waiting"
    seconds: float | None = None
    summary: str = ""
    error: str = ""


@dataclass
class RunState:
    """The state of one run. A page reads a copy of it."""

    id: str
    kind: str  # "threats" or "requirements"
    # "checking", "running", "done", "stopped" or "failed"
    state: str
    message: str
    stories: list[StoryState] = field(default_factory=list)
    started: float = 0.0
    finished: float | None = None
    stop_requested: bool = False
    version: int = 0
    # Changes only when the head or the story list shows something new.
    story_version: int = 0
    # The progress of the story in work (see pipeline_boards.new_progress).
    progress: dict | None = None


@dataclass
class ThreatRunSpec:
    """What the person chose for a threat list run."""

    stories_file: Path
    docs: Path
    product: str | None = None
    story_ids: tuple[str, ...] | None = None
    writer_model: str = "gemma4:26b"
    check: bool = True
    checker_model: str | None = "qwen3.8:27b"
    labels: bool = True
    about: str | None = None
    prompt_set: str | None = None  # the name of a user prompt set
    other_formats: bool = False  # convert files of other types to Markdown
    about_generated: bool = False  # a model wrote the facts of the paragraph


@dataclass
class RequirementsRunSpec:
    """What the person chose for a requirements run.

    system_context is the path of a context file. The run reads its text.
    """

    stories_file: Path
    docs: Path
    product: str | None = None
    story_ids: tuple[str, ...] | None = None
    model: str = "gemma4:26b"
    system_context: str | None = None
    prompt_set: str | None = None  # the name of a user prompt set
    other_formats: bool = False  # convert files of other types to Markdown


class RunFailedError(Exception):
    """A run ends with a message for the person."""


RUN_ENDED = "The run ended before this story finished."


def _now_text() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def error_dict(exc: BaseException) -> dict:
    """Return a JSON safe description of an error."""
    message = getattr(exc, "message", None) or str(exc)
    return {
        "type": type(exc).__name__,
        "message": message,
        "details": getattr(exc, "details", None),
        "model": getattr(exc, "model", None),
    }


def _error_text(exc: BaseException, base_url: str) -> str:
    """Return a plain message that says what happened and what to do."""
    name = type(exc).__name__
    if name == "ModelServerError":
        model = getattr(exc, "model", None)
        text = f"{exc} Check Ollama at {base_url}"
        if model:
            text += f" and the model {model}"
        return text + "."
    if name in ("InputError", "NothingToDoError", "RunFailedError"):
        return str(exc)
    return f"Unexpected error. {name}: {exc}"


def _default_check_server(base_url: str):
    from secreqgen.application.server import check_model_server

    return check_model_server(base_url=base_url)


def _model_present(status: Any, name: str) -> bool:
    names = {getattr(m, "name", str(m)) for m in (status.models or ())}
    return bool(name) and (name in names or f"{name}:latest" in names)


def server_problems(status: Any, names: list[str], base_url: str) -> list[str]:
    """Return plain sentences about Ollama. Empty: all is well."""
    if not status.reachable:
        return [f"Ollama is not running at {base_url}. Start Ollama, then check again."]
    problems = []
    for name in names:
        if not _model_present(status, name):
            problems.append(f"{name} is not installed. Run: ollama pull {name}")
    return problems


def _plural(count: int, word: str) -> str:
    return f"{count} {word}" if count == 1 else f"{count} {word}s"


def _faults(outcome: Any) -> int:
    """Return the number of threats that the checker flagged."""
    rows = (getattr(outcome, "model", None) or {}).get("rows") or []
    return sum(
        1
        for row in rows
        if isinstance(row, dict) and row.get("second_model") == "refuses"
    )


def _threat_summary(outcome: Any, checked: bool) -> str:
    summary = outcome.summary
    parts = [_plural(summary.rows, "threat")]
    if checked:
        parts.append(f"{summary.accepted} accepted")
        refused = _faults(outcome)
        if refused:
            parts.append(f"{refused} flagged")
    return ", ".join(parts)


SUBSET_LINE = "The command runs all {total} stories, not your {picked}."
PROMPT_LINE = "The command uses the tested prompts, not your set."
RUN_PROMPT_LINE = "The command uses the tested prompts, not the set of this run."


def _plain(value: Any) -> Any:
    """Return the value with only what JSON can write.

    A YAML file can hold dates and other values that JSON cannot write. The
    command line reads such a file as it is, so the copy must keep it
    readable. A key becomes text. A date or any other value becomes text.
    """
    if isinstance(value, dict):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, list | tuple | set | frozenset):
        return [_plain(item) for item in value]
    if value is None or isinstance(value, str | int | float | bool):
        return value
    return str(value)


def absolute(path: str | Path) -> Path:
    """Return the path made absolute against the folder of the GUI.

    The GUI reads a relative path from its own working folder. A terminal can
    be in another folder, so the command must not hold a relative path.
    """
    found = Path(path)
    return found if found.is_absolute() else Path.cwd() / found


def project_key_is_text(path: Path) -> bool:
    """Return False when the key project of a story file is not text.

    The command that makes requirements gives the raw key to the search, while
    the GUI gives the key as text (a number becomes text). The two are the
    same only when the key is text or is not there. It reads the file, so a
    page calls it off the event loop. A file that cannot be read gives True.
    """
    from secreqgen.application.inputs import read_story_data

    try:
        data = read_story_data(Path(path), encoding="utf-8", require_object=True)
    except Exception:  # noqa: BLE001 - the page shows the error of the file
        return True
    return data.get("project") is None or isinstance(data.get("project"), str)


def command_for_threats(
    spec: ThreatRunSpec,
    *,
    total_stories: int,
    default_product: str | None,
    about_file: Path | None,
    base_url: str,
):
    """Return the command that gives the run of a threat list spec.

    The values come from the spec, the same values that a run gets.
    """
    from secreqgen.application.commands import threat_command

    return threat_command(
        stories_file=absolute(spec.stories_file),
        docs=absolute(spec.docs),
        product=spec.product,
        default_product=default_product,
        total_stories=total_stories,
        story_ids=spec.story_ids,
        writer_model=spec.writer_model,
        check=spec.check,
        checker_model=spec.checker_model if spec.check else None,
        labels=spec.labels,
        about_file=about_file,
        prompt_set=spec.prompt_set,
        other_formats=spec.other_formats,
        base_url=base_url,
    )


def command_for_requirements(
    spec: RequirementsRunSpec,
    *,
    total_stories: int,
    default_product: str | None,
    base_url: str,
):
    """Return the command that gives the run of a requirements spec.

    default_product is the product name of the stories file. Give None when
    the key project of the file is not text (see project_key_is_text), so
    that the command names the product.
    """
    from secreqgen.application.commands import requirements_command

    return requirements_command(
        stories_file=absolute(spec.stories_file),
        docs=absolute(spec.docs),
        product=spec.product,
        default_product=default_product,
        total_stories=total_stories,
        story_ids=spec.story_ids,
        model=spec.model,
        system_context=(absolute(spec.system_context) if spec.system_context else None),
        prompt_set=spec.prompt_set,
        other_formats=spec.other_formats,
        base_url=base_url,
    )


def command_lines(command: Any, *, picked: int, total: int) -> list[str]:
    """Return the lines under a command of a form: what it cannot do."""
    lines = []
    if "stories_subset" in command.differences:
        lines.append(SUBSET_LINE.format(picked=picked, total=total))
    if "prompt_set" in command.differences:
        lines.append(PROMPT_LINE)
    return lines


def stored_run_command(run_id: str, manifest: dict | None):
    """Return (command, lines) for a stored run, or None when it is not known.

    The command reads the copy of the story file in the folder of the run. It
    reads files, so a page calls it off the event loop. A run that the command
    cannot give gets no command, because a command that does something else
    is not shown. That is the case for a run of some stories that kept the
    whole file, for an old run whose paragraph or context file is not known,
    and for a run with a relative path.
    """
    from secreqgen.application.commands import (
        requirements_command,
        threat_command,
    )
    from secreqgen.application.inputs import read_story_file

    if not isinstance(manifest, dict) or manifest.get("kind") not in storage.KINDS:
        return None
    docs = manifest.get("docs")
    if not isinstance(docs, str) or not docs.strip():
        return None
    if not Path(docs).is_absolute():
        return None  # the folder that the run started in is not known
    threats = manifest["kind"] == "threats"
    folder = storage.run_dir(run_id)
    stories = folder / (Path(str(manifest.get("stories_file") or "")).name or "x")
    if not stories.is_file():
        stories = folder / "stories.json"
    if not stories.is_file():
        return None
    try:
        found = read_story_file(stories)
        default_product, total = found.project, len(found.stories)
    except Exception:  # noqa: BLE001 - an old file: build what is known
        default_product, total = None, int(manifest.get("stories_total") or 0)
    if not threats and not project_key_is_text(stories):
        default_product = None  # the command must name the product
    ids = [str(i) for i in manifest.get("story_ids") or []]
    # An old run kept the whole file. A new run keeps only its stories.
    chosen = tuple(ids) if ids and total > len(ids) else None
    prompt = manifest.get("prompt_set")
    own = isinstance(prompt, dict) and not prompt.get("tested", True)
    set_name = str(prompt.get("name") or "set") if own else None
    base_url = str(manifest.get("server") or "").strip() or load_settings().base_url
    common = {
        "stories_file": stories,
        "docs": Path(docs),
        "product": str(manifest.get("product") or "") or None,
        "default_product": default_product,
        "total_stories": total,
        "story_ids": chosen,
        "prompt_set": set_name,
        "other_formats": bool(manifest.get("other_formats")),
        "base_url": base_url,
    }
    if threats:
        writer = str(manifest.get("writer") or "")
        if not writer:
            return None
        about_file = None
        text = manifest.get("about")
        has_text = isinstance(text, str) and bool(text.strip())
        if "about_words" not in manifest and not has_text:
            return None  # it does not say if the run had a paragraph
        if has_text or int(manifest.get("about_words") or 0) > 0:
            about_file = storage.run_about_path(run_id)
            if not about_file.is_file():
                if not has_text:
                    return None  # an old run: the paragraph is gone
                storage.write_run_about(run_id, text)
        checker = str(manifest.get("checker") or "")
        check = bool(manifest.get("check", True)) and bool(checker)
        command = threat_command(
            **common,
            writer_model=writer,
            check=check,
            checker_model=checker if check else None,
            labels=bool(manifest.get("labels", True)),
            about_file=about_file,
        )
    else:
        model = str(manifest.get("model") or "")
        if not model:
            return None
        if "system_context" not in manifest:
            return None  # an old run: it does not say if it had a context file
        context = manifest.get("system_context")
        context = context if isinstance(context, str) and context.strip() else None
        if context and not Path(context).is_absolute():
            return None  # the folder that the run started in is not known
        command = requirements_command(
            **common,
            model=model,
            system_context=Path(context) if context else None,
        )
    if "stories_subset" in command.differences:
        return None
    lines = [RUN_PROMPT_LINE] if "prompt_set" in command.differences else []
    return command, lines


class JobManager:
    """Runs one job at a time in a worker thread.

    The three arguments are stand-ins for tests. None means the real
    functions of the application layer.
    """

    def __init__(self, run_threats=None, run_requirements=None, check_server=None):
        self._run_threats = run_threats
        self._run_requirements = run_requirements
        self._check_server = check_server
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._state: RunState | None = None
        self._thread: threading.Thread | None = None
        self._settings = None

    # reading

    def busy(self) -> bool:
        with self._lock:
            return self._state is not None and self._state.state in (
                "checking",
                "running",
            )

    def current(self) -> RunState | None:
        """Return a copy of the run in work or the last run that ended."""
        with self._lock:
            return copy.deepcopy(self._state)

    def get(self, run_id: str) -> RunState | None:
        with self._lock:
            if self._state is not None and self._state.id == run_id:
                return copy.deepcopy(self._state)
        return None

    def join(self, timeout: float | None = None) -> None:
        """Wait for the worker thread. Tests use this."""
        thread = self._thread
        if thread is not None:
            thread.join(timeout)

    def server_status(self, base_url: str):
        """Check Ollama. It never raises."""
        check = self._check_server or _default_check_server
        try:
            return check(base_url)
        except Exception as exc:  # the status line must never fail
            return _Status(False, (), f"The check failed: {exc}")

    # starting

    def start_threats(self, spec: ThreatRunSpec) -> str:
        return self._start("threats", spec)

    def start_requirements(self, spec: RequirementsRunSpec) -> str:
        return self._start("requirements", spec)

    def request_stop(self) -> None:
        """Stop after the story that runs."""
        with self._lock:
            if self._state is None or not self.busy():
                return
            self._stop.set()
            self._state.stop_requested = True
            self._state.message = "Stopping after the current story."
            self._bump()

    def _start(self, kind: str, spec: Any) -> str:
        from secreqgen_gui import context_job  # here: it imports this module

        with START_LOCK:
            if self.busy() or context_job.writer.busy():
                raise RuntimeError("Another run is in progress.")
            return self._begin(kind, spec)

    def _begin(self, kind: str, spec: Any) -> str:
        """Set the state and start the worker. START_LOCK is held."""
        with self._lock:
            run_id = storage.new_run_id(kind)
            self._stop = threading.Event()
            self._settings = load_settings()
            self._state = RunState(
                id=run_id,
                kind=kind,
                state="checking",
                message="Checking Ollama",
                started=time.time(),
            )
            self._bump()
            manifest = self._base_manifest(run_id, kind, spec)
            target = self._work_threats if kind == "threats" else self._work_req
            self._thread = threading.Thread(
                target=self._guarded,
                args=(target, run_id, spec, manifest),
                daemon=True,
                name=f"secreqgen-run-{run_id}",
            )
            self._thread.start()
        return run_id

    def _bump(self) -> None:
        if self._state is not None:
            self._state.version += 1
            self._state.story_version += 1

    def _bump_steps(self) -> None:
        """Change the version only. The head and the story list stay."""
        if self._state is not None:
            self._state.version += 1

    # the worker

    def _base_manifest(self, run_id: str, kind: str, spec: Any) -> dict:
        threats = kind == "threats"
        tested = spec.prompt_set is None
        default_name = TESTED_THREAT_SET if threats else TESTED_REQUIREMENTS_SET
        manifest: dict = {
            "schema_version": 1,
            "run_id": run_id,
            "kind": kind,
            "status": "running",
            "created_at": _now_text(),
            "finished_at": None,
            "tool_version": __version__,
            "product": spec.product,
            "docs": str(spec.docs),
            "stories_file": "stories.json",
            "story_ids": list(spec.story_ids) if spec.story_ids else [],
            "stories_total": 0,
        }
        if threats:
            manifest.update(
                writer=spec.writer_model,
                checker=spec.checker_model if spec.check else None,
                labels=spec.labels,
                check=spec.check,
                about_words=len(spec.about.split()) if spec.about else 0,
            )
            if spec.about:
                manifest["about"] = spec.about  # for the command of the run
                if spec.about_generated:
                    manifest["generated_about"] = True
        else:
            manifest["model"] = spec.model
            # For the command of the run. The key is always there: None says
            # that the run had no system context file.
            manifest["system_context"] = (
                str(spec.system_context) if spec.system_context else None
            )
        if spec.other_formats:
            manifest["other_formats"] = True
        if not threats and storage.is_generated_context(spec.system_context):
            manifest["generated_context"] = True
        manifest.update(
            prompt_set={
                "kind": kind,
                "name": default_name if tested else spec.prompt_set,
                "tested": tested,
            },
            server=self._settings.base_url,
            per_story=[],
            failures=[],
            skipped=[],
            not_run=[],
            error=None,
        )
        return manifest

    def _guarded(self, target, run_id: str, spec: Any, manifest: dict) -> None:
        """Run the work. An exception ends the run, never the server."""
        try:
            target(run_id, spec, manifest)
        except BaseException as exc:  # noqa: BLE001 - the thread must not die
            base_url = self._settings.base_url
            self._finish(run_id, manifest, "failed", "failed", error=exc, url=base_url)

    def _finish(
        self,
        run_id: str,
        manifest: dict,
        state: str,
        status: str,
        *,
        message: str | None = None,
        error: BaseException | None = None,
        url: str = "",
    ) -> None:
        with self._lock:
            run = self._state
            if run is not None and run.id == run_id:
                for story in run.stories:
                    if story.state == "waiting":
                        story.state = "not run"
                    elif story.state == "running":
                        story.state = "failed"
                        story.error = story.error or RUN_ENDED
                if error is not None:
                    message = _error_text(error, url)
                run.state = state
                run.message = message or ""
                run.finished = time.time()
                self._bump()
                manifest["not_run"] = [
                    s.id for s in run.stories if s.state == "not run"
                ]
            manifest["status"] = status
            manifest["finished_at"] = _now_text()
            if error is not None:
                manifest["error"] = error_dict(error)
        try:
            storage.write_manifest(run_id, manifest)
        except OSError:
            pass

    def _save(self, run_id: str, manifest: dict) -> None:
        try:
            storage.write_manifest(run_id, manifest)
        except OSError:
            pass

    def _prepare(self, run_id: str, spec: Any, manifest: dict) -> Path:
        """Copy the story file, list the stories and check Ollama."""
        from secreqgen.application.inputs import (
            build_user_stories,
            check_input_file,
            read_story_data,
        )

        folder = storage.run_dir(run_id)
        source = Path(spec.stories_file)
        check_input_file(source)
        name = "stories" + (source.suffix.lower() or ".json")
        copy_path = folder / name
        copy_path.write_bytes(source.read_bytes())
        manifest["stories_file"] = name
        data = read_story_data(copy_path)
        items = data.get("user_stories") if isinstance(data, dict) else None
        if not isinstance(items, list):
            raise RunFailedError("The stories file needs a list named user_stories.")
        stories, skipped = build_user_stories(items)
        if spec.story_ids:
            wanted = set(spec.story_ids)
            everything = len(stories)
            stories = [s for s in stories if s.id in wanted]
            if stories and len(stories) < everything:
                # The copy holds only the stories of the run. So the command
                # of the run page, with this file, gives the same run. A copy
                # that cannot be made must not stop the run: the whole file
                # stays, and the run page then shows no command for the run.
                try:
                    copy_path = self._chosen_copy(
                        folder, copy_path, data, items, wanted
                    )
                except Exception:  # noqa: BLE001 - the copy serves only the command
                    pass
                else:
                    # The run reads the copy, which has no entry that is not
                    # valid. The record of the run still names the entries
                    # of the file that were skipped (see _end_of_loop).
                    manifest["skipped"] = [
                        {"position": position, "message": str(error)}
                        for position, error in skipped
                    ]
                manifest["stories_file"] = copy_path.name
        if not stories:
            raise RunFailedError("The stories file has no story to run.")
        about = getattr(spec, "about", None)
        if about:
            storage.write_run_about(run_id, about)
        manifest["stories_total"] = len(stories)
        self._expect_formats(manifest, spec)
        self._save(run_id, manifest)  # before the bump: the page then sees the tag
        with self._lock:
            self._state.stories = [StoryState(s.id, s.title) for s in stories]
            self._bump()
        return copy_path

    @staticmethod
    def _chosen_copy(
        folder: Path, copy_path: Path, data: dict, items: list, wanted: set
    ) -> Path:
        """Replace the copy of the story file by one with the chosen stories.

        The new file is JSON and keeps the other keys of the file. An entry
        with no id keeps the id that the run gave it. It raises when the copy
        cannot be made with the same product name. The caller then keeps the
        whole file.
        """
        from secreqgen.application.inputs import build_user_stories

        project = data.get("project")
        if _plain(project) != project or type(_plain(project)) is not type(project):
            # JSON would change the product name of the file (a YAML date
            # becomes text). The run must read what the file holds.
            raise ValueError("the key project cannot be written as JSON")
        kept = []
        for position, item in enumerate(items, start=1):
            built, _skipped = build_user_stories([item])
            if not built:
                continue
            story_id = built[0].id if "id" in item else f"US-{position:03d}"
            if story_id in wanted:
                kept.append({**item, "id": story_id})
        target = folder / "stories.json"
        text = json.dumps(
            _plain({**data, "user_stories": kept}), indent=2, ensure_ascii=False
        )
        # The new file takes the place of the copy in one step, so a write
        # that fails leaves the copy as it was.
        temp = folder / "stories.json.tmp"
        try:
            temp.write_text(text + "\n", encoding="utf-8")
            temp.replace(target)
        finally:
            temp.unlink(missing_ok=True)
        if target != copy_path:
            copy_path.unlink(missing_ok=True)
        return target

    @staticmethod
    def _expect_formats(manifest: dict, spec: Any) -> None:
        """Note the files that the run will convert, before the run starts.

        The page then shows the tag during the run and after a run that was
        cut off. The real report replaces this note at the end of the run.
        """
        if not spec.other_formats:
            return
        from secreqgen.application.documentation import count_documentation

        counts = count_documentation(Path(spec.docs)).others
        if sum(counts.values()) > 0:
            manifest["docs_formats"] = {
                "converted": {str(k): int(v) for k, v in counts.items()},
                "skipped": 0,
            }

    def _check_models(self, run_id: str, manifest: dict, names: list[str]) -> None:
        base_url = self._settings.base_url
        status = self.server_status(base_url)
        problems = server_problems(status, names, base_url)
        if problems:
            raise RunFailedError(" ".join(problems))

    def _prompts_dir(self, kind: str, spec: Any) -> Path | None:
        if spec.prompt_set is None:
            return None
        if not _SET_NAME.match(spec.prompt_set):
            raise RunFailedError(
                "Name not allowed. "
                "Use lowercase letters, digits, hyphens, underscores."
            )
        folder = storage.prompt_sets_dir() / kind / spec.prompt_set
        if not folder.is_dir():
            raise RunFailedError(f"The prompt set {spec.prompt_set} does not exist.")
        return folder

    def _hooks(self, run_id: str, manifest: dict, checked: bool):
        """Return the four callbacks of a run function."""
        started: dict[int, float] = {}

        def on_story_start(story_id: str, number: int, total: int) -> None:
            with self._lock:
                run = self._state
                if run is None or not 0 < number <= len(run.stories):
                    return
                run.stories[number - 1].state = "running"
                run.progress = pipeline_boards.new_progress(run.kind, story_id)
                started[number] = time.time()
                self._bump()

        def on_progress(event: Any) -> None:
            with self._lock:
                run = self._state
                if run is None or not 0 < event.done <= len(run.stories):
                    return
                story = run.stories[event.done - 1]
                story.seconds = round(
                    time.time() - started.get(event.done, run.started), 1
                )
                outcome = getattr(event, "outcome", None)
                if event.error is not None:
                    story.state = "failed"
                    story.error = str(event.error)
                else:
                    story.state = "done"
                    story.summary = self._summary(run.kind, outcome, checked)
                self._bump()
                entry = {
                    "story_id": story.id,
                    "status": "failed" if event.error is not None else "ok",
                    "seconds": story.seconds,
                }
                folder = getattr(outcome, "folder", None)
                if folder is not None:
                    entry["folder"] = Path(folder).name
                summary = getattr(outcome, "summary", None)
                if summary is not None and dataclasses.is_dataclass(summary):
                    entry.update(dataclasses.asdict(summary))
                    entry["seconds"] = story.seconds
                if run.kind == "requirements" and outcome is not None:
                    entry["requirements"] = len(outcome.requirements)
                if run.kind == "threats" and outcome is not None and checked:
                    entry["faults"] = _faults(outcome)
                manifest["per_story"].append(entry)
                if event.error is not None:
                    manifest["failures"].append(
                        {"story_id": story.id, **error_dict(event.error)}
                    )
            self._save(run_id, manifest)

        def on_step(event: Any) -> None:
            """Keep the step reports. A problem here never stops the run."""
            try:
                with self._lock:
                    run = self._state
                    if run is None or run.id != run_id:
                        return
                    if run.progress is None:
                        story_id = str(getattr(event, "story_id", "") or "")
                        run.progress = pipeline_boards.new_progress(run.kind, story_id)
                    pipeline_boards.apply_event(run.progress, event)
                    self._bump_steps()
            except Exception:  # noqa: BLE001 - the run never depends on the view
                pass

        def should_stop() -> bool:
            return self._stop.is_set()

        return on_story_start, on_progress, should_stop, on_step

    @staticmethod
    def _step_arguments(run_fn: Any, on_step: Any) -> dict:
        """Return {"on_step": ...} when the run function takes the callback."""
        try:
            parameters = inspect.signature(run_fn).parameters.values()
        except (TypeError, ValueError):
            return {}
        for parameter in parameters:
            if parameter.name == "on_step" or parameter.kind is parameter.VAR_KEYWORD:
                return {"on_step": on_step}
        return {}

    @staticmethod
    def _summary(kind: str, outcome: Any, checked: bool) -> str:
        if outcome is None:
            return ""
        if kind == "threats":
            return _threat_summary(outcome, checked)
        return _plural(len(outcome.requirements), "requirement")

    @staticmethod
    def _docs_formats(manifest: dict, result: Any) -> None:
        """Note the converted files of the run in the manifest."""
        report = getattr(result, "docs_report", None)
        converted = getattr(report, "converted", None)
        if report is None or not isinstance(converted, dict):
            return
        skipped = list(getattr(report, "skipped", None) or [])
        manifest["docs_formats"] = {
            "converted": {str(k): int(v) for k, v in converted.items()},
            "skipped": len(skipped),
        }
        pairs = []
        for item in skipped[:SHOWN_SKIPPED]:
            try:
                name, reason = item
            except (TypeError, ValueError):
                continue
            pairs.append([str(name), str(reason)])
        if pairs:
            manifest["skipped_files"] = pairs

    def _end_of_loop(self, run_id: str, manifest: dict, result: Any) -> None:
        """Set the final state from the result of a run function."""
        self._docs_formats(manifest, result)
        if getattr(result, "docs_report", None) is None:
            # No file was converted. Drop the note from the start of the run.
            manifest.pop("docs_formats", None)
        aborted = getattr(result, "aborted", None)
        stopped = bool(getattr(result, "stopped", False))
        found = [
            {"position": position, "message": str(exc)}
            for position, exc in getattr(result, "skipped", None) or []
        ]
        if found or not manifest.get("skipped"):
            # A run of some stories noted the skipped entries of the file at
            # its start (see _prepare). Its copy has none, so keep the note.
            manifest["skipped"] = found
        if aborted is not None:
            self._finish(
                run_id,
                manifest,
                "failed",
                "aborted",
                error=aborted,
                url=self._settings.base_url,
            )
        elif stopped:
            self._finish(run_id, manifest, "stopped", "stopped", message="Stopped.")
        else:
            self._finish(run_id, manifest, "done", "finished", message="Done.")

    def _work_threats(self, run_id: str, spec: ThreatRunSpec, manifest: dict) -> None:
        stories_copy = self._prepare(run_id, spec, manifest)
        names = [spec.writer_model]
        if spec.check and spec.checker_model:
            names.append(spec.checker_model)
        self._check_models(run_id, manifest, names)
        prompts_dir = self._prompts_dir("threats", spec)
        with self._lock:
            self._state.state = "running"
            self._state.message = "Running"
            self._bump()
        run_fn = self._run_threats
        stand_in = run_fn is not None
        if run_fn is None:
            from secreqgen.application.threats import run_threat_lists as run_fn

        from secreqgen.application.threats import ThreatOptions

        values = {
            "input_file": stories_copy,
            "docs": Path(spec.docs),
            "product": spec.product,
            "story_ids": tuple(spec.story_ids) if spec.story_ids else None,
            "labels": spec.labels,
            "writer_model": spec.writer_model,
            "check": spec.check,
            "checker_model": spec.checker_model if spec.check else None,
            "about": spec.about,
            "output": storage.run_dir(run_id) / "stories",
            "strict_input": True,
            "keep_finished": True,
            "prompts_dir": prompts_dir,
            "other_formats": spec.other_formats,
            "docs_cache": storage.docs_cache_dir(),
        }
        if stand_in:
            # A stand-in may run before the new option fields exist.
            known = {f.name for f in dataclasses.fields(ThreatOptions)}
            values = {k: v for k, v in values.items() if k in known}
        options = ThreatOptions(**values)
        on_story_start, on_progress, should_stop, on_step = self._hooks(
            run_id, manifest, bool(spec.check and spec.checker_model)
        )
        result = run_fn(
            options,
            cfg=make_config(self._settings),
            on_progress=on_progress,
            on_story_start=on_story_start,
            should_stop=should_stop,
            **self._step_arguments(run_fn, on_step),
        )
        self._end_of_loop(run_id, manifest, result)

    def _work_req(self, run_id: str, spec: RequirementsRunSpec, manifest: dict) -> None:
        stories_copy = self._prepare(run_id, spec, manifest)
        self._check_models(run_id, manifest, [spec.model])
        prompts_dir = self._prompts_dir("requirements", spec)
        context_text = None
        if spec.system_context:
            try:
                context_text = Path(spec.system_context).read_text(encoding="utf-8")
            except (OSError, UnicodeError) as exc:
                raise RunFailedError(
                    f"The system context file cannot be read: {exc}"
                ) from exc
        with self._lock:
            self._state.state = "running"
            self._state.message = "Running"
            self._bump()
        run_fn = self._run_requirements
        stand_in = run_fn is not None
        if run_fn is None:
            from secreqgen.application.requirements import run_requirements as run_fn

        from secreqgen.application.requirements import RequirementsOptions

        values = {
            "input_file": stories_copy,
            "docs": Path(spec.docs),
            "product": spec.product,
            "story_ids": tuple(spec.story_ids) if spec.story_ids else None,
            "model": spec.model,
            "system_context": context_text,
            "output_format": "json",
            "strict_input": True,
            "keep_finished": True,
            "prompts_dir": prompts_dir,
            "other_formats": spec.other_formats,
            "docs_cache": storage.docs_cache_dir(),
        }
        if stand_in:
            known = {f.name for f in dataclasses.fields(RequirementsOptions)}
            values = {k: v for k, v in values.items() if k in known}
        options = RequirementsOptions(**values)
        on_story_start, on_progress, should_stop, on_step = self._hooks(
            run_id, manifest, False
        )
        result = run_fn(
            options,
            cfg=make_config(self._settings),
            on_progress=on_progress,
            on_story_start=on_story_start,
            should_stop=should_stop,
            **self._step_arguments(run_fn, on_step),
        )
        from secreqgen.application.serialise import requirements_run_to_dict

        data = requirements_run_to_dict(result)
        path = storage.run_dir(run_id) / "requirements.json"
        path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        self._end_of_loop(run_id, manifest, result)


@dataclass(frozen=True)
class _Status:
    """A small status for a check that failed."""

    reachable: bool
    models: tuple
    error: str | None


def spec_from_dict(data: dict) -> ThreatRunSpec | RequirementsRunSpec:
    """Make a run spec from the dict of a start file.

    The key "kind" is "threats" or "requirements". The other keys are the
    fields of the spec. A path is made from text. Unknown keys are refused.
    """
    data = dict(data)
    kind = data.pop("kind", "threats")
    cls: Callable = ThreatRunSpec if kind == "threats" else RequirementsRunSpec
    if kind not in storage.KINDS:
        raise ValueError("The kind of run is not valid.")
    known = {f.name for f in dataclasses.fields(cls)}
    unknown = set(data) - known
    if unknown:
        raise ValueError("Unknown field: " + ", ".join(sorted(unknown)))
    for key in ("stories_file", "docs"):
        if key in data:
            data[key] = expand_path(data[key])
    if data.get("system_context"):
        data["system_context"] = str(expand_path(data["system_context"]))
    if data.get("story_ids") is not None:
        data["story_ids"] = tuple(data["story_ids"])
    return cls(**data)


manager = JobManager()
