"""Use case: make a draft paragraph "About the product".

The source is a codebase folder or a system context file. For a folder, a
model writes the system context (run_system_context). For a file, no model
runs. In both cases plain code makes the paragraph from the checked context
with a fixed rule. run_about writes a file only when an output path is given.
It never replaces a file that exists.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from secreqgen.application.clients import ClientFactory
from secreqgen.application.errors import InputError
from secreqgen.application.system_context import check_output_path

if TYPE_CHECKING:
    from secreqgen.application.steps import StepCallback
    from secreqgen.config import SecReqGenConfig
    from secreqgen.context.about import AboutDraft


@dataclass
class AboutOptions:
    """The options of run_about."""

    source: Path  # a codebase folder, or a system context file (YAML)
    product: str | None = None
    writer_model: str | None = None
    output: Path | None = None  # None: write no file


@dataclass
class AboutRun:
    """The result of run_about."""

    draft: AboutDraft
    writer: str | None  # None: no model ran (the source was a file)
    result: dict | None  # the dict of SystemContextWriter.generate, or None
    output: Path | None  # the file that was written, or None


def about_draft(result: dict) -> AboutDraft:
    """Make the draft from the result of the system context job.

    It raises ContextError when the result has no context.
    """
    from secreqgen.context import ContextError, about_from_context

    context = result.get("context") if isinstance(result, dict) else None
    if not isinstance(context, dict):
        raise ContextError("The result has no system context.")
    return about_from_context(context)


def _read_context_file(path: Path) -> dict:
    """Read a system context file. Raise InputError when it is no YAML mapping."""
    import yaml

    bad = InputError(f"{path} is not a system context file (YAML).")
    try:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as e:
        raise bad from e
    if not isinstance(data, dict):
        raise bad
    return data


def _with_product(data: dict, product: str) -> dict:
    """Return a copy of the file data with the product name as the name."""
    key = next(
        (k for k in ("project", "system") if isinstance(data.get(k), dict)), "project"
    )
    head = data[key] if isinstance(data.get(key), dict) else {}
    return {**data, key: {**head, "name": product.strip()}}


def run_about(
    options: AboutOptions,
    *,
    cfg: SecReqGenConfig | None = None,
    make_client: ClientFactory | None = None,
    on_step: StepCallback | None = None,
) -> AboutRun:
    """Make a draft paragraph from a codebase folder or a context file.

    It raises InputError, ModelServerError or ContextError. With on_step, the
    stages of the writer go to the callback, as in run_system_context.
    """
    from secreqgen.context import about_from_file_data

    source = Path(options.source)
    if options.output is not None:
        check_output_path(options.output)
    if source.is_dir():
        from secreqgen.application.system_context import (
            ContextOptions,
            run_system_context,
        )

        run = run_system_context(
            ContextOptions(source, options.product, options.writer_model),
            cfg=cfg,
            make_client=make_client,
            on_step=on_step,
        )
        draft = about_draft(run.result)
        writer: str | None = run.writer
        result: dict | None = run.result
    elif source.is_file():
        if options.writer_model is not None:
            raise InputError(
                "The option --model has no use with a system context file."
            )
        data = _read_context_file(source)
        if options.product is not None and options.product.strip():
            data = _with_product(data, options.product)
        draft = about_from_file_data(data)
        writer, result = None, None
    else:
        raise InputError(f"Not found: {source}")
    if options.output is not None:
        out = Path(options.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        try:
            # Mode "x": a file that appeared while the model worked is kept.
            with out.open("x", encoding="utf-8") as handle:
                handle.write(draft.paragraph + "\n")
        except FileExistsError as e:
            raise InputError(
                f"The output file exists: {out}. Give the name of a new file, "
                "or delete the file first."
            ) from e
    return AboutRun(draft=draft, writer=writer, result=result, output=options.output)
