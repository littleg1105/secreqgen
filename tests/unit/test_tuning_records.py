"""Tests for tuning_lib.records. No network, no registered folder written."""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))

from tuning_lib import records as R  # noqa: E402
from tuning_lib.guard import GuardError, WriteGuard  # noqa: E402


def full(**over):
    d = {}
    for name, types in R.REQUIRED_RECORD_FIELDS.items():
        t = types[0]
        d[name] = {str: "x", int: 1, float: 1.0, list: [], bool: True}[t]
    for name in ("doc_context_ref", "doc_context_sha256", "provider", "error", "error_kind", "raw_reply"):
        d[name] = None
    d.update(model="m", story_id="S1", arm="A2", run=1, attempt=1, max_attempts=3, config_id="old")
    d.update(over)
    return d


def test_new_record_valid_and_errors():
    assert R.new_record(**full())["model"] == "m"
    bad = full(); del bad["arm"]
    with pytest.raises(R.RecordError, match="arm"):
        R.new_record(**bad)
    with pytest.raises(R.RecordError):
        R.new_record(**full(run="1"))
    with pytest.raises(R.RecordError):
        R.new_record(**full(run=True))
    with pytest.raises(R.RecordError, match="unknown"):
        R.new_record(**full(extra=1))
    with pytest.raises(R.RecordError):
        R.new_record(**full(latency_ms=float("nan")))
    with pytest.raises(R.RecordError):
        R.new_record(**full(requirements=[object()]))
    with pytest.raises(GuardError):
        R.new_record(**full(raw_reply=f"see {Path.home()}/x"))


def test_read_and_append(tmp_path):
    p = tmp_path / "a" / "records.jsonl"
    assert R.read_jsonl(p) == []
    g = WriteGuard(allowed_roots=[tmp_path])
    (tmp_path / "a").mkdir()
    rec = full()
    R.append_record(p, rec, guard=g)
    assert R.read_jsonl(p) == [rec]
    with pytest.raises(GuardError):
        R.append_record(Path("/private/tmp/zz_outside/records.jsonl"), rec, guard=g)
    p.write_text(p.read_text() + "\n{bad\n")
    with pytest.raises(R.RecordError, match="line 3"):
        R.read_jsonl(p)


def test_states():
    ok = full(error=None)
    err = full(error="E", error_kind="transport")
    assert R.key_states([]) == {}
    s = R.key_states([ok])[R.record_key(ok)]
    assert R.is_final(s, 3) and R.next_attempt(s) == 2
    s2 = R.key_states([err, dict(err, attempt=2)])[R.record_key(err)]
    assert not R.is_final(s2, 3) and R.next_attempt(s2) == 3
    s3 = R.key_states([err, dict(err, attempt=2), dict(err, attempt=3)])[R.record_key(err)]
    assert R.is_final(s3, 3)
    s4 = R.key_states([err, dict(err, attempt=2), dict(ok, attempt=3)])[R.record_key(err)]
    assert R.is_final(s4, 3)
    assert not R.is_final(None, 3) and R.next_attempt(None) == 1
    two = R.key_states([ok, dict(ok, model="n"), dict(ok, config_id="new")])
    assert len(two) == 3


def manifest(**over):
    m = {k: {} for k in R._MANIFEST_KEYS}
    m.update(schema_version=1, study="tuning", step="anchor", run_name="r", state="running",
             stop_reason=None, started_utc="t", ended_utc=None, command_line=[], notes=[])
    m["hash_lists"] = {"before": {}, "after": {}}
    m.update(over)
    return m


def test_manifest():
    R.validate_manifest(manifest())
    with pytest.raises(R.RecordError):
        R.validate_manifest({k: v for k, v in manifest().items() if k != "jobs"})
    with pytest.raises(R.RecordError):
        R.validate_manifest(manifest(hash_lists={"after": {}}))
    with pytest.raises(R.RecordError):
        R.validate_manifest(manifest(client={"api_key": "x"}))
    with pytest.raises(R.RecordError):
        R.validate_manifest(manifest(client={"Authorization": "x"}))
    with pytest.raises(GuardError):
        R.validate_manifest(manifest(notes=[str(Path.home())]))
