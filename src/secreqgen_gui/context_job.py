"""The job that writes a system context with the model.

It is built like the job manager, for one task. The work runs in a daemon
thread. The thread changes plain data only. A page reads a copy of that data
with a timer. One model job runs at a time on this computer: a run and this
job refuse to start while the other one works.
"""

from __future__ import annotations

import copy
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from secreqgen.application.system_context import CONTEXT_TRIES

from secreqgen_gui import jobs
from secreqgen_gui.settings import load_settings, make_config

BUSY = "Another run is in progress."
TRIES = CONTEXT_TRIES  # the tries of the writer for one answer that can be read

STAGES = ("scan", "write", "check")
STAGE_NAMES = {
    "scan": "Scan the folder",
    "write": "Write the context",
    "check": "Check each line",
}


def _fresh_stages() -> dict:
    return {name: {"state": "waiting", "numbers": {}} for name in STAGES}


def _empty_state() -> dict:
    return {
        "state": "idle",
        "stage": "",
        "stages": _fresh_stages(),
        "message": "",
        "result": None,
        "serial": 0,  # changes with each start
        "owner": "",  # the dialog that started the job
    }


def error_text(exc: BaseException, base_url: str) -> str:
    """Return one plain line for an error of the job."""
    name = type(exc).__name__
    if name == "ContextError":
        return str(exc)
    return jobs._error_text(exc, base_url)


class ContextJob:
    """Runs one job at a time that writes a draft system context.

    run_context is a stand-in for tests. None means the function
    run_system_context of the application layer.
    """

    def __init__(self, run_context: Callable | None = None) -> None:
        self._run_context = run_context
        self._lock = threading.RLock()
        self._state = _empty_state()
        self._thread: threading.Thread | None = None
        self._serial = 0

    def busy(self) -> bool:
        with self._lock:
            return self._state["state"] == "running"

    def state(self) -> dict:
        """Return a copy of the state."""
        with self._lock:
            return copy.deepcopy(self._state)

    def join(self, timeout: float | None = None) -> None:
        """Wait for the worker thread. Tests use this."""
        thread = self._thread
        if thread is not None:
            thread.join(timeout)

    def start(
        self, folder: Path, product: str | None, model: str, owner: str = "context"
    ) -> None:
        """Start the job. A run or another job in progress gives RuntimeError.

        Two dialogs share this job. Owner names the dialog that starts it, so
        that a dialog never shows the draft of the other one as its own.
        """
        with jobs.START_LOCK:
            if self.busy() or jobs.manager.busy():
                raise RuntimeError(BUSY)
            settings = load_settings()
            with self._lock:
                self._serial += 1
                self._state = _empty_state()
                self._state["serial"] = self._serial
                self._state["owner"] = owner
                self._state["state"] = "running"
                self._state["stage"] = "scan"
                self._state["stages"]["scan"]["state"] = "running"
                self._thread = threading.Thread(
                    target=self._work,
                    args=(Path(folder), product, model, settings),
                    daemon=True,
                    name="secreqgen-context",
                )
                self._thread.start()

    def _work(self, folder: Path, product: str | None, model: str, settings) -> None:
        try:
            run_fn = self._run_context
            if run_fn is None:
                from secreqgen.application.system_context import run_system_context

                run_fn = run_system_context
            from secreqgen.application.system_context import ContextOptions

            options = ContextOptions(folder=folder, product=product, writer_model=model)
            outcome = run_fn(options, cfg=make_config(settings), on_step=self._on_step)
            result = dict(outcome.result)
        except BaseException as exc:  # noqa: BLE001 - the thread must not die
            with self._lock:
                for item in self._state["stages"].values():
                    if item["state"] == "running":
                        item["state"] = "failed"
                self._state["state"] = "failed"
                self._state["message"] = error_text(exc, settings.base_url)
            return
        with self._lock:
            for item in self._state["stages"].values():
                item["state"] = "done"
            self._state["state"] = "done"
            self._state["stage"] = ""
            self._state["result"] = result

    def _on_step(self, event: Any) -> None:
        """Keep a step report. A problem here never stops the job."""
        try:
            name = str(getattr(event, "step", ""))
            if name not in STAGES:
                return
            numbers = dict(getattr(event, "numbers", None) or {})
            with self._lock:
                stages = self._state["stages"]
                item = stages[name]
                item["numbers"].update(numbers)
                if event.state == "start":
                    item["state"] = "running"
                    self._state["stage"] = name
                elif event.state == "done":
                    answered = numbers.get("answered", 1)
                    if name == "write" and not answered:
                        # The model gave no answer. The writer tries again.
                        item["state"] = "running"
                    else:
                        item["state"] = "done"
                    if name == "scan":
                        stages["write"]["state"] = "running"
                        self._state["stage"] = "write"
                    elif name == "write" and answered:
                        stages["check"]["state"] = "running"
                        self._state["stage"] = "check"
        except Exception:  # noqa: BLE001 - the job never depends on the view
            pass


writer = ContextJob()
