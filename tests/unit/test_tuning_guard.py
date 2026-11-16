"""Tests of scripts/tuning_lib/guard.py. No network, no model, no key. Temporary folders only."""

from __future__ import annotations

import getpass
import hashlib
import json
import os
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "scripts"))
sys.path.insert(0, str(_ROOT / "src"))

from tuning_lib import guard  # noqa: E402
from tuning_lib.guard import (  # noqa: E402
    GuardError,
    HashListResult,
    ProcessRow,
    RunLock,
    WriteGuard,
)
from tuning_lib.paths import STUDY2, WORKSPACE  # noqa: E402


@pytest.fixture
def env(tmp_path: Path):
    allowed = tmp_path / "allowed"
    forbidden = tmp_path / "Forbidden"
    outside = tmp_path / "outside"
    for d in (allowed, forbidden, outside):
        d.mkdir()
    g = WriteGuard(allowed_roots=[allowed], forbidden_roots=[forbidden], forbidden_names=["study2", "study2b"])
    return g, allowed, forbidden, outside


# ----- write guard -----


def test_relative_path_refused(env):
    g, *_ = env
    with pytest.raises(GuardError, match="rule 1"):
        g.check("allowed/x.txt")
    with pytest.raises(GuardError, match="rule 1"):
        g.check("~/x.txt")


def test_allowed_new_file_ok(env):
    g, allowed, *_ = env
    assert g.check(allowed / "sub" / "new.txt") == (allowed / "sub" / "new.txt").resolve()
    assert g.check(allowed) == allowed.resolve()


def test_outside_refused(env):
    g, _, _, outside = env
    with pytest.raises(GuardError, match="rule 5"):
        g.check(outside / "x.txt")


def test_dotdot_escape_refused(env):
    g, allowed, _, outside = env
    with pytest.raises(GuardError, match="rule 5"):
        g.check(allowed / ".." / "outside" / "x.txt")


def test_forbidden_root_refused_even_with_case_change(env):
    g, _, forbidden, _ = env
    with pytest.raises(GuardError, match="rule 3"):
        g.check(forbidden / "x.txt")
    with pytest.raises(GuardError, match="rule 3"):
        g.check(forbidden.parent / "FORBIDDEN" / "x.txt")
    with pytest.raises(GuardError, match="rule 3"):
        g.check(forbidden.parent / "forbidden" / "deep" / "x.txt")


def test_symlink_inside_allowed_to_forbidden_refused(env):
    g, allowed, forbidden, _ = env
    link = allowed / "link"
    link.symlink_to(forbidden, target_is_directory=True)
    with pytest.raises(GuardError, match="rule 3"):
        g.check(link / "x.txt")


def test_symlink_inside_allowed_to_outside_refused(env):
    g, allowed, _, outside = env
    link = allowed / "out"
    link.symlink_to(outside, target_is_directory=True)
    with pytest.raises(GuardError, match="rule 5"):
        g.check(link / "x.txt")


def test_symlink_outside_to_allowed_resolves_inside(env):
    g, allowed, _, outside = env
    link = outside / "in"
    link.symlink_to(allowed, target_is_directory=True)
    assert g.check(link / "x.txt") == (allowed / "x.txt").resolve()


def test_dangling_symlink_to_forbidden_refused(env):
    g, allowed, forbidden, _ = env
    link = allowed / "dangling"
    link.symlink_to(forbidden / "not_yet")
    with pytest.raises(GuardError, match="rule 3"):
        g.check(link)


def test_forbidden_name_part_refused(env):
    g, allowed, *_ = env
    with pytest.raises(GuardError, match="rule 4"):
        g.check(allowed / "Study2" / "x.txt")
    with pytest.raises(GuardError, match="rule 4"):
        g.check(allowed / "run" / "STUDY2B")
    # A name that only contains the word is fine.
    g.check(allowed / "study2_copy" / "x.txt")


def test_default_guard_refuses_registered_folders():
    for p in (STUDY2 / "x.json", WORKSPACE / "coded_project" / "secreqgen" / "data" / "x"):
        with pytest.raises(GuardError):
            guard.assert_writable(p)
    with pytest.raises(GuardError, match="rule 5"):
        guard.assert_writable(WORKSPACE / "somewhere_else.txt")


def test_default_guard_allows_tuning_root_without_access():
    from tuning_lib.paths import FINAL_TEST_ROOT, TUNING_ROOT

    guard.assert_writable(TUNING_ROOT / "run_x" / "records.jsonl")
    guard.assert_writable(FINAL_TEST_ROOT / "run_x" / "records.jsonl")


def test_guarded_write_text(env):
    g, allowed, _, outside = env
    sub = guard.guarded_mkdir(allowed / "a" / "b", guard=g)
    assert sub.is_dir()
    target = guard.guarded_write_text(sub / "f.txt", "one", guard=g)
    assert target.read_text() == "one"
    guard.guarded_write_text(sub / "f.txt", "two", guard=g)
    assert target.read_text() == "two"
    assert [p.name for p in sub.iterdir()] == ["f.txt"]  # no temporary file left
    with pytest.raises(GuardError):
        guard.guarded_write_text(outside / "f.txt", "x", guard=g)
    assert not (outside / "f.txt").exists()
    with pytest.raises(GuardError):
        guard.guarded_mkdir(outside / "new", guard=g)
    assert not (outside / "new").exists()


def test_guarded_write_text_failure_leaves_no_temp_file(env):
    g, allowed, *_ = env
    with pytest.raises(FileNotFoundError):
        guard.guarded_write_text(allowed / "missing_dir" / "f.txt", "x", guard=g)
    with pytest.raises(UnicodeEncodeError):
        guard.guarded_write_text(allowed / "f.txt", "\ud800", guard=g)
    assert list(allowed.iterdir()) == []


def test_guarded_append_line(env):
    g, allowed, _, outside = env
    f = allowed / "r.jsonl"
    guard.guarded_append_line(f, '{"a": 1}', guard=g)
    guard.guarded_append_line(f, '{"a": 2}\n', guard=g, fsync=False)
    assert f.read_text() == '{"a": 1}\n{"a": 2}\n'
    with pytest.raises(GuardError, match="one line"):
        guard.guarded_append_line(f, "a\nb", guard=g)
    with pytest.raises(GuardError):
        guard.guarded_append_line(outside / "r.jsonl", "x", guard=g)
    assert not (outside / "r.jsonl").exists()


# ----- run lock -----


def test_lock_acquire_release(env):
    g, allowed, *_ = env
    run = allowed / "run1"
    lock = RunLock(run, guard=g)
    lock.acquire()
    data = json.loads((run / guard.LOCK_NAME).read_text())
    assert data["pid"] == os.getpid() and data["token"] == lock.token and data["started_utc"]
    lock.release()
    assert not (run / guard.LOCK_NAME).exists()


def test_lock_second_acquire_raises_and_keeps_file(env):
    g, allowed, *_ = env
    run = allowed / "run1"
    first = RunLock(run, guard=g, pid_alive=lambda pid: True)
    first.acquire()
    second = RunLock(run, guard=g, pid_alive=lambda pid: True)
    with pytest.raises(GuardError, match="still running"):
        second.acquire()
    assert (run / guard.LOCK_NAME).exists()
    dead = RunLock(run, guard=g, pid_alive=lambda pid: False)
    with pytest.raises(GuardError, match="is gone; delete this file by hand and start again"):
        dead.acquire()
    assert (run / guard.LOCK_NAME).exists()
    with pytest.raises(GuardError, match="did not acquire"):
        second.release()
    assert (run / guard.LOCK_NAME).exists()
    first.release()


def test_lock_release_with_foreign_token_raises(env):
    g, allowed, *_ = env
    lock = RunLock(allowed / "r", guard=g)
    lock.acquire()
    path = allowed / "r" / guard.LOCK_NAME
    path.write_text(json.dumps({"pid": 1, "started_utc": "x", "token": "other"}))
    with pytest.raises(GuardError, match="belongs to another run"):
        lock.release()
    assert path.exists()


def test_lock_unreadable_existing_file(env):
    g, allowed, *_ = env
    (allowed / "r").mkdir()
    (allowed / "r" / guard.LOCK_NAME).write_text("not json")
    with pytest.raises(GuardError, match="cannot be read"):
        RunLock(allowed / "r", guard=g).acquire()


def test_lock_context_manager_and_guard(env):
    g, allowed, _, outside = env
    with RunLock(allowed / "ctx", guard=g):
        assert (allowed / "ctx" / guard.LOCK_NAME).exists()
    assert not (allowed / "ctx" / guard.LOCK_NAME).exists()
    with pytest.raises(GuardError):
        RunLock(outside / "ctx", guard=g).acquire()


def test_pid_alive():
    assert guard._pid_alive(os.getpid()) is True
    assert guard._pid_alive(2**22 + 12345) is False


# ----- other processes -----


def _row(pid: int, ppid: int, cmd: str) -> ProcessRow:
    return ProcessRow(pid, ppid, cmd)


@pytest.mark.parametrize(
    "cmd,expected",
    [
        ("/venv/bin/python scripts/run_tuning.py --n 2", True),
        ("python3.13 -u scripts/run_study2b.py", True),
        ("caffeinate -i /venv/bin/python3 scripts/run_study2.py --x", True),
        ("nohup env PYTHONPATH=src python scripts/run_final_test.py", True),
        ("/path/run_study2_ai_raters.py --go", True),
        ("grep run_tuning.py notes.txt", False),
        ("vim scripts/run_tuning.py", False),
        ("python -m pytest tests/test_run_tuning.py", False),
        ("python -c 'import run_tuning.py'", False),
        ("python -m run_tuning.py", False),
        ("python scripts/other_script.py run_tuning.py", False),
        ("tail -f logs/run_study2b.py.log", False),
        ("python", False),
        ("python 'unterminated", False),
    ],
)
def test_match_rule(cmd, expected):
    rows = [_row(100, 1, cmd), _row(200, 1, "/bin/zsh")]
    got = guard.find_other_experiment_processes(rows, own_pid=200)
    assert (len(got) == 1) is expected


def test_python_after_six_tokens_not_matched():
    cmd = "a b c d e f python scripts/run_tuning.py"
    assert guard.find_other_experiment_processes([_row(1000, 1, cmd)], own_pid=5) == []


def test_own_process_and_ancestors_are_skipped():
    rows = [
        _row(10, 1, "python scripts/run_tuning.py"),  # grandparent (a wrapper script)
        _row(20, 10, "python scripts/run_tuning.py"),  # parent
        _row(30, 20, "python scripts/run_tuning.py"),  # own
        _row(40, 1, "python scripts/run_tuning.py"),  # another
    ]
    got = guard.find_other_experiment_processes(rows, own_pid=30)
    assert [r.pid for r in got] == [40]


def test_assert_no_other_experiment():
    ok_rows = [_row(1, 0, "/sbin/launchd"), _row(os.getpid(), 1, "python scripts/run_tuning.py")]
    guard.assert_no_other_experiment(ok_rows)
    many = [_row(100 + i, 1, f"python scripts/run_study2b.py --{'x' * 200}") for i in range(5)]
    with pytest.raises(GuardError) as e:
        guard.assert_no_other_experiment(many)
    msg = str(e.value)
    assert "pid 100" in msg and "pid 102" in msg and "pid 103" not in msg and "2 more" in msg
    assert "x" * 121 not in msg


def test_list_processes_real_ps_contains_self():
    rows = guard.list_processes()
    assert os.getpid() in {r.pid for r in rows}


def test_list_processes_failure_raises(monkeypatch):
    def boom(*a, **k):
        raise OSError("no ps")

    monkeypatch.setattr(guard.subprocess, "run", boom)
    with pytest.raises(GuardError, match="process list"):
        guard.list_processes()


# ----- hash lists -----


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _make_list(tmp_path: Path, files: dict[str, bytes], digests: dict[str, str] | None = None) -> Path:
    root = tmp_path / "root"
    lines = []
    for rel, data in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        lines.append(f"{(digests or {}).get(rel, _sha(data))}  {rel}")
    lst = tmp_path / "list.sha256"
    lst.write_text("\n".join(lines) + "\n")
    return lst


def test_hash_list_ok_with_spaces_in_path(tmp_path):
    lst = _make_list(tmp_path, {"a/one.txt": b"1", "Uni Docs/two.txt": b"22"})
    res = guard.check_hash_list(lst, root=tmp_path / "root", chunk_size=1)
    assert res.ok and res.n_listed == 2 and res.n_hashed == 2 and res.n_existence_only == 0
    assert res.changed == () and res.missing == ()
    assert str(tmp_path) not in res.path
    guard.assert_hash_lists_ok({"x": res})


def test_hash_list_changed_and_missing(tmp_path):
    lst = _make_list(tmp_path, {"a.txt": b"1", "b.txt": b"2"}, digests={"b.txt": "0" * 64})
    (tmp_path / "root" / "a.txt").unlink()
    with open(lst, "a") as fh:
        fh.write(f"{_sha(b'z')}  c.txt\n")
    res = guard.check_hash_list(lst, root=tmp_path / "root")
    assert not res.ok
    assert res.changed == ("b.txt",)
    assert set(res.missing) == {"a.txt", "c.txt"}
    with pytest.raises(GuardError, match="b.txt"):
        guard.assert_hash_lists_ok({"x": res})


def test_hash_list_sealed_files_are_not_opened(tmp_path):
    lst = _make_list(tmp_path, {"sealed/k.json": b"secret", "open/o.txt": b"o"}, digests={"sealed/k.json": "f" * 64})
    seen: list[Path] = []

    def sealed(p: Path) -> bool:
        seen.append(p)
        return "sealed" in p.parts

    root = tmp_path / "root"
    # Any open() of the sealed file would show a changed hash. It must not.
    res = guard.check_hash_list(lst, root=root, existence_only=sealed)
    assert res.ok and res.n_existence_only == 1 and res.n_hashed == 1
    # A sealed file that is gone is reported as missing.
    (root / "sealed" / "k.json").unlink()
    res = guard.check_hash_list(lst, root=root, existence_only=sealed)
    assert res.missing == ("sealed/k.json",) and not res.changed


def test_hash_list_sealed_file_not_read(tmp_path, monkeypatch):
    lst = _make_list(tmp_path, {"sealed/k.json": b"secret"})
    real_open = open
    opened: list[str] = []

    def spy(file, *a, **k):
        opened.append(str(file))
        return real_open(file, *a, **k)

    monkeypatch.setattr(guard, "open", spy, raising=False)
    guard.check_hash_list(lst, root=tmp_path / "root", existence_only=lambda p: "sealed" in p.parts)
    assert not any("k.json" in o for o in opened)


@pytest.mark.parametrize(
    "content",
    [
        "",
        "\n",
        "abc  file.txt\n",
        ("a" * 64) + " file.txt\n",  # one space
        ("A" * 64) + "  file.txt\n",  # upper case hex
        ("a" * 64) + "  \n",
        ("a" * 64) + "  /abs/file.txt\n",
        ("a" * 64) + "  ../file.txt\n",
        ("a" * 64) + "  x/../../file.txt\n",
        ("a" * 64) + "  ok.txt\n\n",  # blank line inside is not allowed
    ],
)
def test_hash_list_malformed_raises(tmp_path, content):
    lst = tmp_path / "l.sha256"
    lst.write_text(content)
    with pytest.raises(GuardError):
        guard.check_hash_list(lst, root=tmp_path)


def test_hash_list_missing_list_raises(tmp_path):
    with pytest.raises(GuardError, match="missing"):
        guard.check_hash_list(tmp_path / "nope.sha256", root=tmp_path)


def test_check_all_unknown_name():
    with pytest.raises(GuardError, match="unknown"):
        guard.check_all_hash_lists(("nope",))


def test_assert_hash_lists_ok_limits_to_ten_and_rejects_empty():
    res = HashListResult("n", "p", False, 20, 0, 0, tuple(f"f{i}" for i in range(15)), (), "d")
    with pytest.raises(GuardError) as e:
        guard.assert_hash_lists_ok({"n": res})
    assert "f9" in str(e.value) and "f10" not in str(e.value) and "5 more" in str(e.value)
    with pytest.raises(GuardError):
        guard.assert_hash_lists_ok({})


def test_is_sealed_path():
    assert guard.is_sealed_path(STUDY2 / "review" / "sheet.csv")
    assert guard.is_sealed_path(STUDY2 / "review_v2" / "key_do_not_open" / "review_key.json")
    assert guard.is_sealed_path(WORKSPACE / "final_week" / "reports" / "_sealed" / "r.md")
    assert guard.is_sealed_path(STUDY2 / "REVIEW" / "x")
    assert not guard.is_sealed_path(STUDY2 / "review_v2" / "sheet.csv")  # not the old review folder
    assert not guard.is_sealed_path(STUDY2 / "mastodon" / "stories.json")
    assert not guard.is_sealed_path(WORKSPACE / "final_week" / "reports" / "other.md")


# ----- privacy -----


def test_private_markers_contents():
    markers = guard.private_markers()
    assert "/Users/" in markers and getpass.getuser() in markers and str(Path.home()) in markers
    assert len({m.casefold() for m in markers}) == len(markers)


def test_assert_no_private_text_detects_and_hides():
    user = getpass.getuser()
    for text in (f"path {Path.home()}/x", f"hello {user.upper()}"):
        with pytest.raises(GuardError) as e:
            guard.assert_no_private_text(text, what="the request")
        assert "the request" in str(e.value)
        assert user not in str(e.value) and str(Path.home()) not in str(e.value)


def test_assert_no_private_text_kinds():
    with pytest.raises(GuardError, match="users path"):
        guard.assert_no_private_text("a /Users/b", markers=["/Users/"])
    with pytest.raises(GuardError, match="home folder path"):
        guard.assert_no_private_text("a /home/zed b", markers=["/home/zed"])
    with pytest.raises(GuardError, match="account name"):
        guard.assert_no_private_text("a zedname b", markers=["zedname"])


def test_assert_no_private_text_clean_and_short_marker():
    guard.assert_no_private_text("nothing here", markers=["abcdef"])
    with pytest.raises(GuardError, match="too short"):
        guard.assert_no_private_text("nothing", markers=["ab"])


def test_import_has_no_side_effect_markers():
    src = Path(guard.__file__).read_text()
    assert getpass.getuser() not in src


def test_users_marker_is_case_sensitive_for_public_urls():
    guard.assert_no_private_text("see mastodon.social/users/Gargron", markers=["/Users/"])
    with pytest.raises(GuardError):
        guard.assert_no_private_text("path /Users/x", markers=["/Users/"])
