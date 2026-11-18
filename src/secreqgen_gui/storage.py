"""All file work of the GUI. Every path stays inside the data folder.

A bad id raises ValueError. A missing file gives None.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from datetime import UTC, datetime
from pathlib import Path

from secreqgen_gui.settings import data_home, packed_examples_dir

EXAMPLE_STORIES = "generic_stories.json"

MAX_UPLOAD_BYTES = 1_000_000
UPLOAD_SUFFIXES = (".json", ".yaml", ".yml", ".txt", ".md")
KINDS = ("threats", "requirements")
VERDICTS = ("keep", "delete")

_RUN_ID = re.compile(r"^\d{8}-\d{6}-(threats|requirements)(-\d+)?$")
_ROW_ID = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
_FOLDER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def _sub_dir(name: str) -> Path:
    folder = data_home() / name
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def runs_dir() -> Path:
    return _sub_dir("runs")


def uploads_dir() -> Path:
    return _sub_dir("uploads")


def prompt_sets_dir() -> Path:
    return _sub_dir("prompt_sets")


def docs_cache_dir() -> Path:
    """Return the folder that keeps the converted documentation."""
    return _sub_dir("doc_cache")


def contexts_dir() -> Path:
    """Return the folder of the system contexts that a model wrote."""
    return _sub_dir("contexts")


def _check_run_id(run_id: str) -> str:
    if not isinstance(run_id, str) or not _RUN_ID.match(run_id):
        raise ValueError("The run id is not valid.")
    return run_id


def run_dir(run_id: str) -> Path:
    """Return the folder of a run. The id is checked first."""
    return runs_dir() / _check_run_id(run_id)


def new_run_id(kind: str) -> str:
    """Make a run id like 20261117-214500-threats and its folder.

    The folder is made at once, so that two runs never get the same id.
    A clash gets the suffix -2, -3 and so on.
    """
    if kind not in KINDS:
        raise ValueError("The kind of run is not valid.")
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    number = 1
    while True:
        suffix = "" if number == 1 else f"-{number}"
        run_id = f"{stamp}-{kind}{suffix}"
        try:
            (runs_dir() / run_id).mkdir()
        except FileExistsError:
            number += 1
            continue
        return run_id


def _write_json(path: Path, data: object) -> None:
    """Write JSON to a temp file in the same folder, then rename it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    os.replace(temp, path)


def _read_json(path: Path) -> object | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def write_manifest(run_id: str, manifest: dict) -> None:
    """Write run.json of a run. The write is atomic."""
    _write_json(run_dir(run_id) / "run.json", manifest)


def read_manifest(run_id: str) -> dict | None:
    """Return the manifest of a run, or None when it is missing or damaged."""
    data = _read_json(run_dir(run_id) / "run.json")
    return data if isinstance(data, dict) else None


def examples_dir() -> Path:
    """Return the folder of the example files in the data folder."""
    return _sub_dir("examples")


def ensure_examples() -> Path:
    """Copy the example files into the data folder. Return the stories file.

    A file that is in the data folder stays as it is: the person can change
    the copy. A file that is missing there comes from the GUI again.
    """
    target = examples_dir()
    for source in sorted(packed_examples_dir().glob("*.json")):
        copy = target / source.name
        if not copy.exists():
            shutil.copyfile(source, copy)
    return target / EXAMPLE_STORIES


def run_date_text(item: dict) -> str:
    """Return the start of a run in the local time zone.

    The text is like "17 November 2026, 22:43". The manifest holds the time
    in UTC (created_at). The run id holds the same time. An item with no
    time that can be read gives an empty text.
    """
    stamp = str(item.get("created_at") or "")
    try:
        when = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        try:
            when = datetime.strptime(str(item.get("id", ""))[:15], "%Y%m%d-%H%M%S")
        except ValueError:
            return ""
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    local = when.astimezone()
    return f"{local.day} {local:%B %Y, %H:%M}"


def list_runs() -> list[dict]:
    """Return all runs, newest first.

    Each item is the manifest plus the key "id". A run with a damaged
    manifest has the status "damaged". A folder with no manifest is skipped.
    """
    runs: list[dict] = []
    for folder in runs_dir().iterdir():
        if folder.is_symlink() or not folder.is_dir():
            continue
        if not _RUN_ID.match(folder.name):
            continue
        path = folder / "run.json"
        if not path.exists():
            continue
        manifest = _read_json(path)
        if isinstance(manifest, dict):
            runs.append({**manifest, "id": folder.name})
        else:
            runs.append({"id": folder.name, "status": "damaged"})
    runs.sort(key=lambda item: item["id"], reverse=True)
    return runs


def mark_interrupted_runs() -> None:
    """Change the status "running" to "interrupted" in every manifest.

    Call this when the GUI starts. A run that still says "running" then was
    cut off, because the process ended.
    """
    for item in list_runs():
        if item.get("status") == "running":
            manifest = {k: v for k, v in item.items() if k != "id"}
            manifest["status"] = "interrupted"
            write_manifest(item["id"], manifest)


def delete_run(run_id: str) -> None:
    """Delete a run folder. The folder must be inside the runs folder."""
    folder = run_dir(run_id)
    if folder.is_symlink():
        raise ValueError("The run folder is a link.")
    if folder.parent.resolve() != runs_dir().resolve():
        raise ValueError("The run folder is outside the data folder.")
    if folder.is_dir():
        shutil.rmtree(folder)


def story_folder(run_id: str, story_id: str) -> Path | None:
    """Return the result folder of a story, from the manifest.

    The story id must be in the manifest. A folder name from the manifest must
    be a plain name and must stay inside the run folder.
    """
    manifest = read_manifest(run_id)
    if manifest is None:
        return None
    for entry in manifest.get("per_story") or []:
        if not isinstance(entry, dict) or entry.get("story_id") != story_id:
            continue
        name = entry.get("folder")
        if not isinstance(name, str) or not _FOLDER.match(name):
            return None
        stories = (run_dir(run_id) / "stories").resolve()
        folder = (stories / name).resolve()
        if folder.parent != stories or not folder.is_dir():
            return None
        return folder
    return None


def read_threats(run_id: str, story_id: str) -> dict | None:
    """Return threats.json of a story."""
    folder = story_folder(run_id, story_id)
    if folder is None:
        return None
    data = _read_json(folder / "threats.json")
    return data if isinstance(data, dict) else None


def read_threats_page(run_id: str, story_id: str) -> str | None:
    """Return threats.md of a story."""
    folder = story_folder(run_id, story_id)
    if folder is None:
        return None
    try:
        return (folder / "threats.md").read_text(encoding="utf-8")
    except OSError:
        return None


def _quote_hash(quote: object) -> str:
    return hashlib.sha256(str(quote or "").encode("utf-8")).hexdigest()


def _verdict_file(run_id: str, story_id: str) -> Path | None:
    folder = story_folder(run_id, story_id)
    return None if folder is None else folder / "verdicts.json"


def read_verdicts(run_id: str, story_id: str) -> dict[str, str]:
    """Return the verdicts of the reader: row id to "keep" or "delete".

    A verdict that was made for another quote is left out. This happens when
    a result file was replaced.
    """
    path = _verdict_file(run_id, story_id)
    if path is None:
        return {}
    data = _read_json(path)
    rows = data.get("rows") if isinstance(data, dict) else None
    if not isinstance(rows, dict):
        return {}
    model = read_threats(run_id, story_id) or {}
    quotes = {
        row.get("id"): row.get("quote")
        for row in model.get("rows") or []
        if isinstance(row, dict)
    }
    verdicts: dict[str, str] = {}
    for row_id, entry in rows.items():
        if not isinstance(entry, dict) or entry.get("verdict") not in VERDICTS:
            continue
        if row_id in quotes and entry.get("quote_sha256") != _quote_hash(
            quotes[row_id]
        ):
            continue
        verdicts[row_id] = entry["verdict"]
    return verdicts


def write_verdict(run_id: str, story_id: str, row_id: str, verdict: str | None) -> None:
    """Set the verdict of one row. None clears it. The write is atomic."""
    if verdict is not None and verdict not in VERDICTS:
        raise ValueError("The verdict must be keep, delete or None.")
    if not isinstance(row_id, str) or not _ROW_ID.match(row_id):
        raise ValueError("The row id is not valid.")
    path = _verdict_file(run_id, story_id)
    if path is None:
        raise ValueError("The story is not part of the run.")
    model = read_threats(run_id, story_id) or {}
    quotes = {
        row.get("id"): row.get("quote")
        for row in model.get("rows") or []
        if isinstance(row, dict)
    }
    if row_id not in quotes:
        raise ValueError("The row is not part of the story.")
    data = _read_json(path)
    rows = data.get("rows") if isinstance(data, dict) else None
    rows = dict(rows) if isinstance(rows, dict) else {}
    if verdict is None:
        rows.pop(row_id, None)
    else:
        rows[row_id] = {
            "verdict": verdict,
            "quote_sha256": _quote_hash(quotes[row_id]),
            "at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
    _write_json(path, {"schema_version": 1, "rows": rows})


def read_requirements(run_id: str) -> dict | None:
    """Return requirements.json of a run."""
    data = _read_json(run_dir(run_id) / "requirements.json")
    return data if isinstance(data, dict) else None


def safe_upload_name(name: str) -> str:
    """Return a safe file name made from a name that the browser gave."""
    base = str(name).replace("\\", "/").split("/")[-1].strip()
    base = re.sub(r"[^A-Za-z0-9._-]+", "_", base).lstrip("._-")
    suffix = Path(base).suffix.lower()
    stem = base[: len(base) - len(suffix)] if suffix else base
    stem = stem[:60] or "upload"
    return stem + suffix


def save_upload(name: str, data: bytes) -> Path:
    """Save an uploaded file under the uploads folder and return its path.

    Only the suffixes .json, .yaml, .yml, .txt and .md are allowed. A file of
    more than 1 MB is refused. The name is made safe. An old file is never
    replaced: a clash gets the suffix -2, -3 and so on.
    """
    safe = safe_upload_name(name)
    suffix = Path(safe).suffix.lower()
    if suffix not in UPLOAD_SUFFIXES:
        raise ValueError(
            "This file type is not allowed. Use " + ", ".join(UPLOAD_SUFFIXES) + "."
        )
    if len(data) > MAX_UPLOAD_BYTES:
        raise ValueError("The file is larger than 1 MB.")
    if not data:
        raise ValueError("The file is empty.")
    folder = uploads_dir()
    stem = safe[: len(safe) - len(suffix)]
    target = folder / safe
    number = 1
    while target.exists():
        number += 1
        target = folder / f"{stem}-{number}{suffix}"
    target.write_bytes(data)
    return target


def _safe_part(text: str) -> str:
    """Keep letters, digits, dot, hyphen and underscore. Cut to 40 characters."""
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", str(text or ""))
    return cleaned.strip("-._")[:40] or "context"


def save_context(yaml_text: str, result: dict, name: str) -> Path:
    """Write a system context and the result of its job beside it.

    The file is <name>-<date and time>.yaml in the folder contexts. The result
    goes to the .json file with the same name. Return the path of the YAML.
    """
    folder = contexts_dir()
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    base = f"{_safe_part(name)}-{stamp}"
    number = 1
    while True:
        suffix = "" if number == 1 else f"-{number}"
        target = folder / f"{base}{suffix}.yaml"
        if not target.exists() and not target.with_suffix(".json").exists():
            break
        number += 1
    _write_json(target.with_suffix(".json"), result)
    target.write_text(yaml_text, encoding="utf-8")
    return target


def is_generated_context(path: str | Path | None) -> bool:
    """Say if a file is a system context that a model wrote in the GUI.

    It must lie in the folder contexts and have its .json file beside it.
    """
    if not path:
        return False
    try:
        file = Path(path).expanduser().resolve()
        return (
            file.parent == contexts_dir().resolve()
            and file.suffix == ".yaml"
            and file.is_file()
            and file.with_suffix(".json").is_file()
        )
    except (OSError, RuntimeError, ValueError):
        return False
