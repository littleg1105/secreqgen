"""Use case: write a draft system context for a codebase.

A model writes the draft from the facts that plain code collects. Plain code
checks it. A person must read the draft before use. run_system_context writes
a file only when an output path is given. It has no default path, and it never
replaces a file that exists.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from secreqgen.application.clients import ClientFactory
from secreqgen.application.errors import InputError, ModelServerError
from secreqgen.application.inputs import check_codebase_folder

if TYPE_CHECKING:
    from secreqgen.application.steps import StepCallback
    from secreqgen.config import SecReqGenConfig

CONTEXT_STEPS = ("scan", "write", "check")


@dataclass
class ContextOptions:
    """The options of run_system_context."""

    folder: Path
    product: str | None = None
    writer_model: str | None = None
    output: Path | None = None  # None: write no file


@dataclass
class ContextRun:
    """The result of run_system_context."""

    writer: str
    result: dict  # the dict of SystemContextWriter.generate
    output: Path | None  # the file that was written, or None


def resolve_context_model(cfg: SecReqGenConfig, writer_model: str | None) -> str:
    """Return the model that writes the context.

    The option wins. Then a model that the user set in the settings. Then the
    tested writer. An empty option is an InputError.
    """
    if writer_model is not None:
        name = writer_model.strip()
        if not name:
            raise InputError("The model name is empty.")
        return name
    if "model_name" in cfg.llm.model_fields_set and cfg.llm.model_name.strip():
        return cfg.llm.model_name.strip()
    from secreqgen.threats import TESTED_WRITER

    return TESTED_WRITER


def check_output_path(path: Path) -> None:
    """Raise InputError when the output file cannot be written as a new file.

    The check runs before the model works, so that no finished draft is lost.
    A file that exists is never replaced.
    """
    path = Path(path)
    if path.is_dir():
        raise InputError(f"The output path is a folder: {path}. Give a file name.")
    if path.exists():
        raise InputError(
            f"The output file exists: {path}. Give the name of a new file, "
            "or delete the file first."
        )
    parent = path.parent
    while not parent.exists() and parent != parent.parent:
        parent = parent.parent
    if not parent.is_dir() or not os.access(parent, os.W_OK | os.X_OK):
        raise InputError(
            f"Cannot write to {path}: {parent} is not a folder to write in."
        )


def write_context_file(path: Path, result: dict) -> None:
    """Write result["yaml"] as UTF-8 text. An OSError passes through."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(result["yaml"], encoding="utf-8")


def context_lines(result: dict) -> list[dict]:
    """The lines of a draft with the source of each, for a review page.

    Each entry has "where" (the place in the YAML text), "text" and "path"
    (the file of the codebase that the line comes from). The last entry is the
    security rating. The model chose it; it comes from no file.
    """
    from secreqgen.context import source_lines

    context = result.get("context") if isinstance(result, dict) else None
    return source_lines(context) if isinstance(context, dict) else []


def _stage_sender(on_step: StepCallback | None, name: str):
    """Return the on_stage function for the writer, and a record of the stages."""
    seen: list[str] = []

    def on_stage(stage: str, state: str, info: dict) -> None:
        seen.append(f"{stage}:{state}")
        if on_step is None:
            return
        from secreqgen.application.steps import StepEvent

        index = int(info.get("attempt", 0)) if stage == "write" else 0
        try:
            on_step(
                StepEvent(
                    story_id=name,
                    step=stage,
                    state=state,
                    index=index,
                    numbers=dict(info),
                )
            )
        except Exception:  # the run never depends on the view  # noqa: BLE001
            pass

    return on_stage, seen


def run_system_context(
    options: ContextOptions,
    *,
    cfg: SecReqGenConfig | None = None,
    make_client: ClientFactory | None = None,
    on_step: StepCallback | None = None,
) -> ContextRun:
    """Write a draft system context for a codebase folder.

    It raises InputError, ModelServerError or ContextError. A ContextError
    about the folder (no README, no manifest) becomes InputError. A
    ContextError about the model answer (no answer that can be read, nothing
    that the files support) passes through unchanged.

    With on_step, each stage goes to the callback as a StepEvent. The story_id
    is the name of the folder. An error inside the callback is ignored.
    """
    from secreqgen.context import ContextError, SystemContextWriter

    check_codebase_folder(options.folder)
    if options.output is not None:
        check_output_path(options.output)
    if cfg is None:
        from secreqgen.config import get_config

        cfg = get_config()
    if make_client is None:
        from secreqgen.application.clients import build_ollama_client

        make_client = build_ollama_client
    writer = resolve_context_model(cfg, options.writer_model)
    client = make_client(cfg, tested_setup=True, model_name=writer)
    if not client.check_health():
        raise ModelServerError(
            f"Ollama does not answer, or it does not have the model {writer}.",
            model=writer,
        )
    on_stage, seen = _stage_sender(on_step, Path(options.folder).name)
    try:
        result = SystemContextWriter(client).generate(
            options.folder, options.product, on_stage=on_stage
        )
    except ContextError as e:
        if "write:start" in seen:
            raise
        raise InputError(str(e)) from e
    if options.output is not None:
        write_context_file(options.output, result)
    return ContextRun(writer=writer, result=result, output=options.output)
