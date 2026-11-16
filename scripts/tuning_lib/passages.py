"""Read stored prompts and stored passages (read only) and split a context into passages.

Nothing in this module writes a file. It never opens the sealed folders.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import tempfile
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from pathlib import Path

from tuning_lib.paths import (
    FALLBACK_CONTEXT,
    MODELS,
    PROJECTS,
    STUDY2,
    STUDY2B,
    WORKSPACE,
    model_slug,
)

MAX_PROMPT_TOKENS_EST = 12000
SEPARATOR = "\n\n---\n\n"

# Roots of the registered data. A test can point them to a temporary folder.
STUDY2B_ROOT: Path = STUDY2B
STUDY2_ROOT: Path = STUDY2

_SEALED_NAMES = frozenset({"review", "review_v2", "key_do_not_open", "study2_threat"})
_HEADER_RE = re.compile(
    r"^### \[(\d+)\] (.+) \(Relevance: ([0-9]+\.[0-9]+)\)$", re.MULTILINE
)
_CODE_LINE_RE = re.compile(r"^### code search hit \(pattern: '(.*)'\)$")
_TIKTOKEN_URL = "https://openaipublic.blob.core.windows.net/encodings/cl100k_base.tiktoken"


class PassagesError(Exception):
    """A stored prompt or context is missing, damaged or not as expected."""


@dataclass(frozen=True)
class Passage:
    """One passage of a context."""

    index: int
    kind: str
    source: str
    relevance: float
    text: str
    code_pattern: str | None
    code_location: str | None


@dataclass(frozen=True)
class StoredContext:
    """A frozen context as stored."""

    story_id: str
    text: str
    sha256: str
    n_chunks_stored: int


@dataclass(frozen=True)
class StoredPrompt:
    """One stored prompt with the local figures of its record."""

    study: str
    project: str
    model: str
    story_id: str
    arm: str
    run: int
    attempt: int
    system: str
    user_sent: str | None
    user_non_doc: str
    doc_context_ref: str | None
    doc_context_sha256: str | None
    system_sha256: str | None
    user_sha256: str | None
    local_prompt_tokens: int | None
    local_completion_tokens: int | None
    local_n_requirements: int
    source_file: str
    source_line: int


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def split_context(text: str) -> list[Passage]:
    """Split a stored context into passages. Raise PassagesError on any odd shape."""
    if not isinstance(text, str) or not text.strip():
        raise PassagesError("the context is empty")
    if text == FALLBACK_CONTEXT:
        raise PassagesError("the context is the 41-character fallback string")
    matches = list(_HEADER_RE.finditer(text))
    if not matches:
        raise PassagesError("the context has no passage header")
    if matches[0].start() != 0:
        raise PassagesError("there is text before the first passage header")
    numbers = [int(m.group(1)) for m in matches]
    if numbers != list(range(1, len(matches) + 1)):
        raise PassagesError(f"passage numbers are not 1..n: {numbers}")
    passages: list[Passage] = []
    for i, m in enumerate(matches):
        last = i == len(matches) - 1
        end = len(text) if last else matches[i + 1].start()
        segment = text[m.start() : end]
        if not last:
            if not segment.endswith(SEPARATOR):
                raise PassagesError(f"passage {i + 1} does not end with the separator")
            segment = segment[: -len(SEPARATOR)]
        lines = segment.split("\n")
        second = lines[1] if len(lines) > 1 else ""
        code = _CODE_LINE_RE.match(second)
        pattern: str | None = None
        location: str | None = None
        kind = "doc"
        if code:
            kind = "code"
            pattern = code.group(1)
            for line in lines[2:]:
                if line.startswith("File: "):
                    location = line[len("File: ") :].strip()
                    break
            if not location:
                raise PassagesError(f"code hit {i + 1} has no 'File:' line")
        passages.append(
            Passage(
                index=int(m.group(1)),
                kind=kind,
                source=m.group(2),
                relevance=float(m.group(3)),
                text=segment,
                code_pattern=pattern,
                code_location=location,
            )
        )
    return passages


def doc_passages(passages: Sequence[Passage]) -> list[Passage]:
    """Return the documentation passages."""
    return [p for p in passages if p.kind == "doc"]


def code_passages(passages: Sequence[Passage]) -> list[Passage]:
    """Return the code-search hits."""
    return [p for p in passages if p.kind == "code"]


def _tiktoken_cache_dir() -> Path:
    env = os.environ.get("TIKTOKEN_CACHE_DIR") or os.environ.get("DATA_GYM_CACHE_DIR")
    return Path(env) if env else Path(tempfile.gettempdir()) / "data-gym-cache"


def estimate_prompt_tokens(*texts: str, method: str = "bytes4") -> int:
    """Estimate the token count of the texts together."""
    if method not in ("bytes4", "bytes32", "tiktoken"):
        raise ValueError(f"unknown method: {method!r}")
    if method == "tiktoken":
        key = hashlib.sha1(_TIKTOKEN_URL.encode()).hexdigest()
        cached = _tiktoken_cache_dir() / key
        if not cached.is_file():
            raise PassagesError(
                "the cl100k_base file is not in the tiktoken cache; "
                "this module never downloads it"
            )
        import tiktoken

        enc = tiktoken.get_encoding("cl100k_base")
        return sum(len(enc.encode(t, disallowed_special=())) for t in texts)
    n_bytes = sum(len(t.encode("utf-8")) for t in texts)
    if method == "bytes4":
        return math.ceil(n_bytes / 4)
    return math.ceil(n_bytes / 3.2)


def _rel(path: Path, root: Path) -> str:
    """Workspace-relative path text, never an absolute path."""
    try:
        return path.relative_to(WORKSPACE).as_posix()
    except ValueError:
        return path.relative_to(root.parent).as_posix()


def _check_not_sealed(path: Path, root: Path) -> None:
    parts = {p.lower() for p in path.relative_to(root).parts}
    if parts & _SEALED_NAMES:
        raise PassagesError("refused: the path is inside a sealed folder")


def study2b_cell_dir(project: str, model: str) -> Path:
    """Folder of one project and model in Study 2b."""
    return STUDY2B_ROOT / project / model_slug(model)


def _study2_cell_dir(project: str, model: str) -> Path:
    return STUDY2_ROOT / project / "results" / model_slug(model)


def _read_json_file(path: Path, root: Path) -> object:
    _check_not_sealed(path, root)
    if not path.is_file():
        raise PassagesError(f"stored file not found: {_rel(path, root)}")
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _read_context(cell: Path, root: Path, story_id: str) -> StoredContext:
    frozen = _read_json_file(cell / "retrieval_frozen.json", root)
    if not isinstance(frozen, dict) or story_id not in frozen:
        raise PassagesError(f"no stored context for story {story_id!r}")
    entry = frozen[story_id]
    try:
        text = entry["context"]
        sha = entry["context_sha256"]
        n_chunks = int(entry["n_chunks"])
    except (KeyError, TypeError, ValueError) as exc:
        raise PassagesError(f"stored context of {story_id!r} is damaged: {exc!r}") from exc
    if _sha(text) != sha:
        raise PassagesError(f"stored context of {story_id!r} does not match its hash")
    return StoredContext(story_id=story_id, text=text, sha256=sha, n_chunks_stored=n_chunks)


def read_study2b_context(project: str, model: str, story_id: str) -> StoredContext:
    """Return the frozen Study 2b context of one story."""
    return _read_context(study2b_cell_dir(project, model), STUDY2B_ROOT, story_id)


def read_study2_context(project: str, model: str, story_id: str) -> StoredContext:
    """Return the frozen Study 2 context of one story."""
    return _read_context(_study2_cell_dir(project, model), STUDY2_ROOT, story_id)


def _read_last_lines(path: Path, root: Path) -> dict[tuple[str, str, int], tuple[int, dict]]:
    """Map (story, arm, run) to (line number, record) for the LAST line of each key."""
    _check_not_sealed(path, root)
    if not path.is_file():
        raise PassagesError(f"stored file not found: {_rel(path, root)}")
    out: dict[tuple[str, str, int], tuple[int, dict]] = {}
    with path.open("r", encoding="utf-8") as fh:
        for n, line in enumerate(fh, start=1):
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
                key = (rec["story_id"], rec["arm"], int(rec["run"]))
            except (ValueError, KeyError, TypeError) as exc:
                raise PassagesError(f"{_rel(path, root)} line {n} is not a record: {exc!r}") from exc
            out[key] = (n, rec)
    return out


def _to_prompt(study: str, rec: dict, n: int, rel: str) -> StoredPrompt:
    try:
        return StoredPrompt(
            study=study,
            project=rec["project"],
            model=rec["model"],
            story_id=rec["story_id"],
            arm=rec["arm"],
            run=int(rec["run"]),
            attempt=int(rec.get("attempt", 1)),
            system=rec["prompt_system"],
            user_sent=rec.get("prompt_user_sent"),
            user_non_doc=rec["prompt_user_non_doc"],
            doc_context_ref=rec.get("doc_context_ref"),
            doc_context_sha256=rec.get("doc_context_sha256"),
            system_sha256=rec.get("prompt_system_sha256"),
            user_sha256=rec.get("prompt_user_sha256"),
            local_prompt_tokens=rec.get("prompt_tokens"),
            local_completion_tokens=rec.get("completion_tokens"),
            local_n_requirements=int(rec["n_requirements"]),
            source_file=rel,
            source_line=n,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise PassagesError(f"{rel} line {n}: record is damaged: {exc!r}") from exc


def _read_prompts(
    study: str,
    cell_dir,
    root: Path,
    projects: Sequence[str],
    models: Sequence[str],
    arms: Sequence[str],
    runs: Sequence[int] | None,
    stories: Collection[str] | None,
) -> list[StoredPrompt]:
    result: list[StoredPrompt] = []
    for project in projects:
        for model in models:
            cell = cell_dir(project, model)
            frozen = _read_json_file(cell / "retrieval_frozen.json", root)
            if not isinstance(frozen, dict):
                raise PassagesError("retrieval_frozen.json is not an object")
            known = set(frozen)
            if stories is not None:
                unknown = set(stories) - known
                if unknown:
                    raise PassagesError(f"unknown story ids: {sorted(unknown)}")
                wanted = sorted(set(stories))
            else:
                wanted = sorted(known)
            path = cell / "records.jsonl"
            last = _read_last_lines(path, root)
            rel = _rel(path, root)
            for arm in arms:
                for story in wanted:
                    keys = [k for k in last if k[0] == story and k[1] == arm]
                    if runs is not None:
                        keys = [k for k in keys if k[2] in runs]
                        if len(keys) != len(runs):
                            raise PassagesError(
                                f"missing record: {project}/{model}/{arm}/{story} run(s) {list(runs)}"
                            )
                    elif not keys:
                        raise PassagesError(f"missing record: {project}/{model}/{arm}/{story}")
                    for key in sorted(keys):
                        n, rec = last[key]
                        if rec.get("error") is not None:
                            raise PassagesError(
                                f"{rel} line {n}: the last record of this key has an error"
                            )
                        result.append(_to_prompt(study, rec, n, rel))
    result.sort(key=lambda p: (p.project, p.model, p.arm, p.story_id, p.run))
    return result


def read_study2b_prompts(
    *,
    projects: Sequence[str] = PROJECTS,
    models: Sequence[str] = MODELS,
    arms: Sequence[str] = ("A2", "B2"),
    run: int = 1,
    stories: Collection[str] | None = None,
) -> list[StoredPrompt]:
    """Read the stored Study 2b prompts (last line of each key, no error allowed)."""
    return _read_prompts(
        "study2b", study2b_cell_dir, STUDY2B_ROOT, projects, models, arms, [run], stories
    )


def read_study2_prompts(
    *,
    projects: Sequence[str] = PROJECTS,
    models: Sequence[str] = MODELS,
    arms: Sequence[str] = ("A", "B", "C", "D"),
    stories: Collection[str] | None = None,
) -> list[StoredPrompt]:
    """Read the stored Study 2 prompts. They hold no full user prompt (user_sent is None)."""
    return _read_prompts(
        "study2", _study2_cell_dir, STUDY2_ROOT, projects, models, arms, None, stories
    )


def verify_stored_prompt(sp: StoredPrompt, context: StoredContext | None) -> None:
    """Check a Study 2b prompt against its hashes and its context. Raise PassagesError."""
    where = f"{sp.source_file} line {sp.source_line}"
    if sp.user_sent is None:
        raise PassagesError(f"{where}: no stored user prompt")
    if _sha(sp.user_sent) != sp.user_sha256:
        raise PassagesError(f"{where}: user prompt does not match its hash")
    if _sha(sp.system) != sp.system_sha256:
        raise PassagesError(f"{where}: system prompt does not match its hash")
    if sp.doc_context_ref == "frozen":
        if context is None:
            raise PassagesError(f"{where}: the record has a context but none was given")
        if _sha(context.text) != sp.doc_context_sha256:
            raise PassagesError(f"{where}: context hash differs from the record")
        if sp.user_sent.count(context.text) != 1:
            raise PassagesError(f"{where}: the context is not in the prompt exactly one time")
        if sp.user_sent.replace(context.text, "", 1) != sp.user_non_doc:
            raise PassagesError(f"{where}: the prompt without the context differs from the stored one")
        if not doc_passages(split_context(context.text)):
            raise PassagesError(f"{where}: the context holds no documentation passage")
    else:
        if sp.doc_context_ref is not None:
            raise PassagesError(f"{where}: unknown doc_context_ref {sp.doc_context_ref!r}")
        if context is not None:
            raise PassagesError(f"{where}: a context was given for a prompt without one")
        if sp.user_non_doc != sp.user_sent:
            raise PassagesError(f"{where}: a prompt without context must equal its non-doc form")
