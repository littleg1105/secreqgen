"""Plain dicts from the results of the run functions.

The dicts are safe for json.dumps. A GUI writes them to files. An HTTP API can
send them. The result classes stay as they are. Fields that an older result
class may not have are read with getattr and a default.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any


def error_to_dict(exc: BaseException) -> dict:
    """Describe an error as a dict: type, message, details and model."""
    from secreqgen.exceptions import SecReqGenError

    if isinstance(exc, SecReqGenError):
        message, details = exc.message, exc.details
    else:
        message, details = str(exc), None
    return {
        "type": type(exc).__name__,
        "message": message,
        "details": details,
        "model": getattr(exc, "model", None),
    }


def story_to_dict(story: Any) -> dict:
    """Describe a user story as a dict."""
    return {
        "id": story.id,
        "title": story.title,
        "story": story.story,
        "acceptance_criteria": list(story.acceptance_criteria),
    }


def _failures(items: list) -> list[dict]:
    return [
        {"story_id": story_id, "error": error_to_dict(error)}
        for story_id, error in items
    ]


def _skipped(items: list) -> list[dict]:
    return [
        {"position": position, "message": str(error)} for position, error in items
    ]


def _aborted(run: Any) -> dict | None:
    error = getattr(run, "aborted", None)
    return None if error is None else error_to_dict(error)


def threat_outcome_to_dict(outcome: Any) -> dict:
    """Describe the threat list of one story: id, summary, folder and model."""
    return {
        "story_id": outcome.story_id,
        "summary": asdict(outcome.summary),
        "folder": None if outcome.folder is None else str(outcome.folder),
        "model": outcome.model,
    }


def threat_run_to_dict(run: Any, *, include_models: bool = True) -> dict:
    """Describe a threat run.

    With include_models False, each list holds no "model" dict. The model dict
    is the whole threats.json of a story. It can be large.
    """
    lists = []
    for outcome in run.lists:
        item = threat_outcome_to_dict(outcome)
        if not include_models:
            del item["model"]
        lists.append(item)
    return {
        "writer": run.writer,
        "checker": run.checker,
        "lists": lists,
        "failures": _failures(run.failures),
        "skipped": _skipped(run.skipped),
        "stopped": bool(getattr(run, "stopped", False)),
        "aborted": _aborted(run),
        "not_run": list(getattr(run, "not_run", [])),
    }


def requirement_result_to_dict(result: Any) -> dict:
    """Describe the requirements of one story, with its passages.

    "passages" is an empty list when the stored context has an odd shape.
    """
    from secreqgen.retrieval.passage_edits import PassagesError, split_context

    data = result.model_dump(mode="json")
    passages: list[dict] = []
    context = getattr(result, "retrieval_context", None)
    if context:
        try:
            passages = [
                {"n": p.index, "header": p.source, "text": p.text}
                for p in split_context(context)
            ]
        except PassagesError:
            passages = []
    data["passages"] = passages
    return data


def requirements_run_to_dict(run: Any) -> dict:
    """Describe a requirements run."""
    return {
        "results": [requirement_result_to_dict(r) for r in run.results],
        "failures": _failures(run.failures),
        "skipped": _skipped(run.skipped),
        "model": getattr(run, "model", ""),
        "prompt_set": getattr(run, "prompt_set", ""),
        "tested_prompts": bool(getattr(run, "tested_prompts", True)),
        "stopped": bool(getattr(run, "stopped", False)),
        "aborted": _aborted(run),
        "not_run": list(getattr(run, "not_run", [])),
    }
