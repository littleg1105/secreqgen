"""Record and manifest schema for the tuning runner (DESIGN.md sections 6.1, 8.1, 8.2).

The schema is closed: a missing field, a wrong type or an unknown field raises RecordError.
The module has no side effect on import and uses no network.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from tuning_lib.guard import DEFAULT_GUARD, WriteGuard, assert_no_private_text, guarded_append_line

SCHEMA_VERSION = 1
_N = type(None)
_NUM = (int, float)

REQUIRED_RECORD_FIELDS: dict[str, tuple[type, ...]] = {
    "schema_version": (int,),
    "study": (str,),
    "step": (str,),
    "run_name": (str,),
    "config_id": (str,),
    "project": (str,),
    "model": (str,),
    "hosted_model": (str,),
    "story_id": (str,),
    "arm": (str,),
    "run": (int,),
    "attempt": (int,),
    "max_attempts": (int,),
    "ts": (str,),
    "source_study": (str,),
    "source_run": (int,),
    "source_file": (str,),
    "source_line": (int,),
    "arm_description": (str,),
    "prompt_system": (str,),
    "prompt_user_sent": (str,),
    "prompt_user_non_doc": (str,),
    "doc_context_ref": (str, _N),
    "doc_context_sha256": (str, _N),
    "prompt_system_sha256": (str,),
    "prompt_user_sha256": (str,),
    "prompt_est_tokens": (int,),
    "prompt_check": (str,),
    "n_calls_sent": (int,),
    "request_sha256": (str,),
    "provider_tag": (str,),
    "provider": (str, _N),
    "provider_source": (str, _N),
    "generation_id": (str, _N),
    "response_model": (str, _N),
    "finish_reason": (str, _N),
    "transport_retries": (int,),
    "latency_ms": _NUM + (_N,),
    "prompt_tokens": (int, _N),
    "completion_tokens": (int, _N),
    "reasoning_tokens": (int, _N),
    "reasoning_tokens_source": (str, _N),
    "cached_tokens": (int, _N),
    "cost_usd": _NUM + (_N,),
    "llm_calls": (list,),
    "raw_reply": (str, _N),
    "parse_method": (str, _N),
    "parsed_by_fallback": (bool, _N),
    "old_parser_result": (str, _N),
    "requirements": (list,),
    "n_requirements": (int,),
    "n_requirements_raw": (int,),
    "n_requirements_failed_validation": (int,),
    "validation_failures": (list,),
    "n_threat_mappings": (int,),
    "n_threat_mappings_failed": (int,),
    "asvs_defaulted": (int,),
    "error": (str, _N),
    "error_kind": (str, _N),
}


class RecordError(Exception):
    """A record or a manifest does not follow the schema."""


def validate_record(rec: Mapping) -> None:
    """Check a record against the closed schema. Raise RecordError on the first fault."""
    if not isinstance(rec, Mapping):
        raise RecordError(f"record is not a mapping: {type(rec).__name__}")
    for name, types in REQUIRED_RECORD_FIELDS.items():
        if name not in rec:
            raise RecordError(f"record is missing the field {name!r}")
        value = rec[name]
        ok = isinstance(value, types)
        if ok and isinstance(value, bool) and bool not in types:
            ok = False  # a bool is not an int or a float here
        if not ok:
            allowed = "/".join(t.__name__ for t in types)
            raise RecordError(f"field {name!r} has type {type(value).__name__}, expected {allowed}")
    extra = sorted(set(rec) - set(REQUIRED_RECORD_FIELDS))
    if extra:
        raise RecordError(f"record has unknown fields: {extra}")
    if rec["schema_version"] != SCHEMA_VERSION:
        raise RecordError(f"schema_version is {rec['schema_version']}, expected {SCHEMA_VERSION}")
    if rec["attempt"] < 1 or rec["run"] < 1:
        raise RecordError("attempt and run must be 1 or more")
    if rec["attempt"] > rec["max_attempts"]:
        raise RecordError("attempt is above max_attempts")


def _json_text(rec: Mapping) -> str:
    try:
        return json.dumps(rec, ensure_ascii=False, sort_keys=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise RecordError(f"record is not JSON-safe: {exc}") from exc


def new_record(**fields: Any) -> dict:
    """Validate the fields, run the privacy guard on the JSON text and return the record."""
    rec = dict(fields)
    validate_record(rec)
    assert_no_private_text(_json_text(rec), what="record")
    return rec


def read_jsonl(path: Path) -> list[dict]:
    """Read a JSON lines file. Return [] when the file is absent. A bad line raises with its number."""
    p = Path(path)
    if not p.exists():
        return []
    out: list[dict] = []
    with open(p, "r", encoding="utf-8") as fh:
        for number, line in enumerate(fh, start=1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise RecordError(f"{p.name} line {number}: bad JSON ({exc})") from exc
            if not isinstance(value, dict):
                raise RecordError(f"{p.name} line {number}: not a JSON object")
            out.append(value)
    return out


def append_record(path: Path, rec: Mapping, *, guard: WriteGuard = DEFAULT_GUARD) -> None:
    """Validate a record and append it as one line through the write guard."""
    validate_record(rec)
    text = _json_text(rec)
    assert_no_private_text(text, what="record")
    guarded_append_line(path, text, guard=guard)


def record_key(rec: Mapping) -> tuple:
    """Return (config_id, model, story_id, arm, run). config_id is None when absent."""
    return (rec.get("config_id"), rec["model"], rec["story_id"], rec["arm"], rec["run"])


@dataclass(frozen=True)
class KeyState:
    """State of one key: has an ok line, count of error lines, highest attempt number."""

    ok: bool
    errors: int
    last_attempt: int


def key_states(records: Iterable[Mapping]) -> dict[tuple, KeyState]:
    """Count ok and error lines for each key. A line with error not None is an error."""
    out: dict[tuple, KeyState] = {}
    for rec in records:
        key = record_key(rec)
        old = out.get(key, KeyState(False, 0, 0))
        failed = rec["error"] is not None
        out[key] = KeyState(
            ok=old.ok or not failed,
            errors=old.errors + (1 if failed else 0),
            last_attempt=max(old.last_attempt, int(rec.get("attempt", 1))),
        )
    return out


def is_final(state: KeyState | None, max_attempts: int) -> bool:
    """A key is final when it has an ok line or used all its attempts."""
    return state is not None and (state.ok or state.errors >= max_attempts)


def next_attempt(state: KeyState | None) -> int:
    """Attempt number for the next try: 1 for a new key, else last attempt plus 1."""
    return 1 if state is None else state.last_attempt + 1


_MANIFEST_KEYS = (
    "schema_version", "study", "step", "run_name", "state", "stop_reason", "started_utc",
    "ended_utc", "command_line", "options", "configuration", "models", "jobs", "cost",
    "tokens", "versions", "code", "inputs", "hash_lists", "client", "notes",
)
_FORBIDDEN_KEY = re.compile(r"api[_-]?key|authorization|bearer|secret|token_value", re.I)


def _scan_keys(value: Any, where: str) -> None:
    if isinstance(value, Mapping):
        for k, v in value.items():
            if _FORBIDDEN_KEY.search(str(k)) and str(k) != "headers_sent":
                raise RecordError(f"manifest key {where}/{k} looks like a secret field")
            _scan_keys(v, f"{where}/{k}")
    elif isinstance(value, (list, tuple)):
        for i, v in enumerate(value):
            _scan_keys(v, f"{where}[{i}]")


def validate_manifest(m: Mapping) -> None:
    """Check the manifest shape (section 8.2), that no secret-like key exists and no private text."""
    if not isinstance(m, Mapping):
        raise RecordError("manifest is not a mapping")
    for key in _MANIFEST_KEYS:
        if key not in m:
            raise RecordError(f"manifest is missing the key {key!r}")
    if m["schema_version"] != SCHEMA_VERSION:
        raise RecordError("manifest schema_version is wrong")
    hl = m["hash_lists"]
    if not isinstance(hl, Mapping) or "before" not in hl or "after" not in hl:
        raise RecordError("manifest hash_lists needs 'before' and 'after'")
    for name in ("options", "configuration", "models", "jobs", "cost", "tokens", "versions", "code", "inputs", "client"):
        if not isinstance(m[name], Mapping):
            raise RecordError(f"manifest {name!r} must be an object")
    _scan_keys(m, "manifest")
    try:
        text = json.dumps(m, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise RecordError(f"manifest is not JSON-safe: {exc}") from exc
    assert_no_private_text(text, what="manifest")
