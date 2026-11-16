"""Guards of the tuning tools: write rules, run lock, other processes, hash lists, privacy.

Every write of the new code goes through the functions of this module. No rule here has a silent
fallback. An unexpected state raises GuardError with a plain message. A message never holds file
content, a secret, or the text of a private marker.
"""

from __future__ import annotations

import getpass
import hashlib
import json
import os
import re
import shlex
import subprocess
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .paths import (
    DATA,
    FINAL_TEST_ROOT,
    HASH_LISTS,
    STUDY2,
    STUDY2B,
    TUNING_ROOT,
    WORKSPACE,
)


class GuardError(Exception):
    """A guard refused. The message says which rule and which path. It never holds a secret."""


# ---------------------------------------------------------------------------
# Write guard
# ---------------------------------------------------------------------------


def _is_inside(path: Path, base: Path) -> bool:
    """True when path is base or a folder below it.

    This Mac ignores case, so STUDY2 and study2 are one folder. The test compares the resolved
    paths, the same paths in one case, and each existing folder with the same-file test.
    (Copy of the logic of is_inside in run_study2b.py. It is not imported.)
    """
    p, b = path.expanduser().resolve(), base.expanduser().resolve()
    if p == b or p.is_relative_to(b):
        return True
    pc, bc = str(p).casefold(), str(b).casefold()
    if pc == bc or pc.startswith(bc.rstrip("/") + "/"):
        return True
    if b.exists():
        for folder in (p, *p.parents):
            try:
                if folder.exists() and os.path.samefile(folder, b):
                    return True
            except OSError:
                continue
    return False


class WriteGuard:
    """Decides which paths the new code may write."""

    def __init__(
        self,
        allowed_roots: Sequence[Path] = (TUNING_ROOT, FINAL_TEST_ROOT),
        forbidden_roots: Sequence[Path] = (STUDY2, STUDY2B, DATA),
        forbidden_names: Sequence[str] = ("study2", "study2b"),
    ) -> None:
        self.allowed_roots = tuple(Path(r) for r in allowed_roots)
        self.forbidden_roots = tuple(Path(r) for r in forbidden_roots)
        self.forbidden_names = tuple(n.casefold() for n in forbidden_names)

    def check(self, path: str | Path) -> Path:
        """Return the resolved path, or raise GuardError."""
        raw = Path(path)
        # Rule 1: absolute. A relative path is never resolved against the current folder.
        if not raw.is_absolute():
            raise GuardError(f"write guard rule 1: the path is not absolute: {path}")
        # Rule 2: follow every existing symbolic link.
        resolved = raw.expanduser().resolve(strict=False)
        # Rule 3: forbidden roots.
        for root in self.forbidden_roots:
            if _is_inside(resolved, root):
                raise GuardError(f"write guard rule 3: {resolved} is inside the forbidden root {root}")
        # Rule 4: forbidden names anywhere in the path.
        for part in resolved.parts:
            if part.casefold() in self.forbidden_names:
                raise GuardError(f"write guard rule 4: {resolved} has the forbidden name {part!r}")
        # Rule 5: allowed roots.
        if not any(_is_inside(resolved, root) for root in self.allowed_roots):
            roots = ", ".join(str(r) for r in self.allowed_roots)
            raise GuardError(f"write guard rule 5: {resolved} is not inside an allowed root ({roots})")
        return resolved


DEFAULT_GUARD: WriteGuard = WriteGuard()


def assert_writable(path: str | Path) -> Path:
    """Check a path with the default guard."""
    return DEFAULT_GUARD.check(path)


def guarded_mkdir(path: str | Path, *, guard: WriteGuard = DEFAULT_GUARD) -> Path:
    """Create a folder (with parents) after the guard accepted it."""
    resolved = guard.check(path)
    resolved.mkdir(parents=True, exist_ok=True)
    return resolved


def guarded_write_text(
    path: str | Path, text: str, *, guard: WriteGuard = DEFAULT_GUARD, encoding: str = "utf-8"
) -> Path:
    """Write a file in one step: temporary file, fsync, then os.replace."""
    target = guard.check(path)
    tmp = guard.check(target.with_name(f"{target.name}.tmp-{os.getpid()}"))
    try:
        with open(tmp, "w", encoding=encoding) as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, target)
    except BaseException:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass
        raise
    return target


def guarded_append_line(
    path: str | Path, line: str, *, guard: WriteGuard = DEFAULT_GUARD, fsync: bool = True
) -> Path:
    """Append one line (a newline is added when missing). One record is one line."""
    target = guard.check(path)
    body = line[:-1] if line.endswith("\n") else line
    if "\n" in body or "\r" in body:
        raise GuardError("append guard: the line holds a line break inside, so it is not one line")
    with open(target, "a", encoding="utf-8") as fh:
        fh.write(body + "\n")
        fh.flush()
        if fsync:
            os.fsync(fh.fileno())
    return target


# ---------------------------------------------------------------------------
# Run lock
# ---------------------------------------------------------------------------

LOCK_NAME = "run_tuning.lock"


def _pid_alive(pid: int) -> bool:
    """True when the process exists. PermissionError means it exists."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class RunLock:
    """A lock file in a run folder. It never removes a lock that it does not own."""

    def __init__(
        self,
        run_dir: Path,
        *,
        guard: WriteGuard = DEFAULT_GUARD,
        pid_alive: Callable[[int], bool] = _pid_alive,
    ) -> None:
        self.run_dir = Path(run_dir)
        self.guard = guard
        self.pid_alive = pid_alive
        self.token = uuid.uuid4().hex
        self._path: Path | None = None

    def acquire(self) -> None:
        """Create the lock file, or raise GuardError when it exists."""
        folder = guarded_mkdir(self.run_dir, guard=self.guard)
        lock_path = self.guard.check(folder / LOCK_NAME)
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except FileExistsError:
            raise GuardError(self._describe_existing(lock_path)) from None
        payload = {
            "pid": os.getpid(),
            "started_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "token": self.token,
        }
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(json.dumps(payload))
                fh.flush()
                os.fsync(fh.fileno())
        except BaseException:
            lock_path.unlink(missing_ok=True)
            raise
        self._path = lock_path

    def _describe_existing(self, lock_path: Path) -> str:
        try:
            pid = int(json.loads(lock_path.read_text(encoding="utf-8"))["pid"])
        except (OSError, ValueError, KeyError, TypeError):
            return f"the lock file {lock_path} exists and cannot be read; check it by hand and delete it if no run is active"
        if self.pid_alive(pid):
            return f"the lock file {lock_path} exists: the process {pid} is still running"
        return f"the lock file {lock_path} exists: the process {pid} is gone; delete this file by hand and start again"

    def release(self) -> None:
        """Delete the lock file when its token is the token of this object."""
        if self._path is None:
            raise GuardError("release of a lock that this object did not acquire")
        lock_path = self.guard.check(self._path)
        try:
            token = json.loads(lock_path.read_text(encoding="utf-8")).get("token")
        except (OSError, ValueError, AttributeError) as exc:
            raise GuardError(f"the lock file {lock_path} cannot be read at release ({type(exc).__name__})") from None
        if token != self.token:
            raise GuardError(f"the lock file {lock_path} belongs to another run; it was not deleted")
        lock_path.unlink()
        self._path = None

    def __enter__(self) -> "RunLock":
        self.acquire()
        return self

    def __exit__(self, *exc: object) -> None:
        self.release()


# ---------------------------------------------------------------------------
# Other experiment processes
# ---------------------------------------------------------------------------

OTHER_SCRIPT_NAMES: tuple[str, ...] = (
    "run_study2.py",
    "run_study2b.py",
    "run_tuning.py",
    "run_final_test.py",
    "run_cross_family_winning_config.py",
    "run_study2_ai_raters.py",
)

_PYTHON_RE = re.compile(r"^python[0-9.]*$", re.IGNORECASE)


@dataclass(frozen=True)
class ProcessRow:
    pid: int
    ppid: int
    command: str


def list_processes() -> list[ProcessRow]:
    """List all processes with /bin/ps. Raises GuardError when ps fails or gives nothing."""
    try:
        out = subprocess.run(
            ["/bin/ps", "-axo", "pid=,ppid=,command="],
            capture_output=True,
            text=True,
            timeout=20,
            check=True,
        ).stdout
    except (OSError, subprocess.SubprocessError) as exc:
        raise GuardError(f"the process list cannot be read ({type(exc).__name__})") from None
    rows: list[ProcessRow] = []
    for line in out.splitlines():
        parts = line.strip().split(None, 2)
        if len(parts) < 3 or not parts[0].isdigit() or not parts[1].isdigit():
            continue
        rows.append(ProcessRow(int(parts[0]), int(parts[1]), parts[2]))
    if not rows:
        raise GuardError("the process list is empty, so no check is possible")
    return rows


def _script_of(command: str) -> str | None:
    """Base name of the script that a command runs, or None when it is not a script run."""
    try:
        tokens = shlex.split(command)
    except ValueError:
        tokens = command.split()
    head = tokens[:6]
    for i, tok in enumerate(head):
        if _PYTHON_RE.match(os.path.basename(tok)):
            for nxt in tokens[i + 1 :]:
                if nxt.startswith("-"):
                    if nxt in ("-m", "-c"):
                        return None
                    continue
                return os.path.basename(nxt)
            return None
    if head:
        return os.path.basename(head[0])  # a script started through its shebang line
    return None


def find_other_experiment_processes(
    rows: Sequence[ProcessRow],
    *,
    own_pid: int | None = None,
    names: Sequence[str] = OTHER_SCRIPT_NAMES,
) -> list[ProcessRow]:
    """Return the rows that run an experiment script. This process and its ancestors are left out."""
    own = os.getpid() if own_pid is None else own_pid
    by_pid = {r.pid: r for r in rows}
    skip: set[int] = set()
    cur = own
    while cur > 1 and cur not in skip:
        skip.add(cur)
        row = by_pid.get(cur)
        if row is None:
            break
        cur = row.ppid
    wanted = set(names)
    return [r for r in rows if r.pid not in skip and _script_of(r.command) in wanted]


def assert_no_other_experiment(rows: Sequence[ProcessRow] | None = None) -> None:
    """Raise GuardError when another experiment script runs. Two scripts at once wipe one index."""
    rows = list_processes() if rows is None else rows
    found = find_other_experiment_processes(rows)
    if found:
        shown = "; ".join(f"pid {r.pid}: {r.command[:120]}" for r in found[:3])
        more = f" (and {len(found) - 3} more)" if len(found) > 3 else ""
        raise GuardError(f"another experiment process runs: {shown}{more}")


# ---------------------------------------------------------------------------
# Hash lists
# ---------------------------------------------------------------------------

_LINE_RE = re.compile(r"^([0-9a-f]{64})  (\S.*)$")


@dataclass(frozen=True)
class HashListResult:
    name: str
    path: str
    ok: bool
    n_listed: int
    n_hashed: int
    n_existence_only: int
    changed: tuple[str, ...]
    missing: tuple[str, ...]
    detail: str


def is_sealed_path(path: Path) -> bool:
    """True for files that the project never opens: the old review folder, key folders, sealed reports."""
    p = Path(path)
    if any(part.casefold() == "key_do_not_open" for part in p.parts):
        return True
    text = str(p).casefold()
    for base in (STUDY2 / "review", WORKSPACE / "final_week" / "reports" / "_sealed"):
        b = str(base).casefold().rstrip("/")
        if text == b or text.startswith(b + "/"):
            return True
    return False


def _sha256_file(path: Path, chunk_size: int) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while chunk := fh.read(chunk_size):
            h.update(chunk)
    return h.hexdigest()


def _list_label(path: Path) -> str:
    try:
        return str(path.relative_to(WORKSPACE))
    except ValueError:
        return path.name  # never an absolute path


def check_hash_list(
    path: Path,
    *,
    root: Path = WORKSPACE,
    existence_only: Callable[[Path], bool] = is_sealed_path,
    chunk_size: int = 1 << 20,
) -> HashListResult:
    """Check every file of a hash list. Reads only. Sealed files get is_file() and nothing else."""
    path = Path(path)
    if not path.is_file():
        raise GuardError(f"the hash list is missing: {_list_label(path)}")
    entries: list[tuple[str, str]] = []
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        m = _LINE_RE.match(line)
        if m is None:
            raise GuardError(f"hash list {_list_label(path)}: line {n} is not '<64 hex digits><two spaces><path>'")
        digest, rel = m.group(1), m.group(2)
        rp = Path(rel)
        if rp.is_absolute() or ".." in rp.parts:
            raise GuardError(f"hash list {_list_label(path)}: line {n} has an absolute path or a '..' part")
        entries.append((digest, rel))
    if not entries:
        raise GuardError(f"hash list {_list_label(path)} is empty")

    changed: list[str] = []
    missing: list[str] = []
    n_hashed = n_exist = 0
    for digest, rel in entries:
        target = Path(root) / rel
        if existence_only(target):
            n_exist += 1
            if not target.is_file():
                missing.append(rel)
            continue
        if not target.is_file():
            missing.append(rel)
            continue
        try:
            actual = _sha256_file(target, chunk_size)
        except OSError as exc:
            raise GuardError(f"cannot read {rel} ({type(exc).__name__})") from None
        n_hashed += 1
        if actual != digest:
            changed.append(rel)
    ok = not changed and not missing
    detail = (
        f"{len(entries)} listed, {n_hashed} hashed, {n_exist} existence only, "
        f"{len(changed)} changed, {len(missing)} missing"
    )
    return HashListResult(
        name=path.stem,
        path=_list_label(path),
        ok=ok,
        n_listed=len(entries),
        n_hashed=n_hashed,
        n_existence_only=n_exist,
        changed=tuple(changed),
        missing=tuple(missing),
        detail=detail,
    )


def check_all_hash_lists(names: Sequence[str] = ("study2", "study2b", "vectordb")) -> dict[str, HashListResult]:
    """Check the named registered hash lists."""
    results: dict[str, HashListResult] = {}
    for name in names:
        if name not in HASH_LISTS:
            raise GuardError(f"unknown hash list name: {name!r}")
        res = check_hash_list(HASH_LISTS[name])
        results[name] = HashListResult(**{**res.__dict__, "name": name})
    return results


def assert_hash_lists_ok(results: Mapping[str, HashListResult]) -> None:
    """Raise GuardError when any list has a changed or missing file (at most 10 paths shown)."""
    if not results:
        raise GuardError("no hash list result to check")
    bad: list[str] = []
    for name, res in results.items():
        if not res.ok:
            bad.extend(f"{name}: changed {p}" for p in res.changed)
            bad.extend(f"{name}: missing {p}" for p in res.missing)
            if not res.changed and not res.missing:
                bad.append(f"{name}: not ok ({res.detail})")
    if bad:
        shown = "; ".join(bad[:10])
        more = f" (and {len(bad) - 10} more)" if len(bad) > 10 else ""
        raise GuardError(f"registered files changed or missing: {shown}{more}")


# ---------------------------------------------------------------------------
# Privacy guard
# ---------------------------------------------------------------------------


def private_markers() -> tuple[str, ...]:
    """Strings that must not appear in a request, record or manifest. Computed at call time."""
    home = Path.home()
    candidates = ["/Users/", str(home), home.name, getpass.getuser(), os.environ.get("USER")]
    out: list[str] = []
    seen: set[str] = set()
    for c in candidates:
        if not c or c.casefold() in seen:
            continue
        seen.add(c.casefold())
        out.append(c)
    return tuple(out)


def _marker_kind(marker: str) -> str:
    if marker.casefold() == "/users/":
        return "users path"
    if marker.startswith("/"):
        return "home folder path"
    return "account name"


def assert_no_private_text(text: str, *, what: str = "text", markers: Sequence[str] | None = None) -> None:
    """Raise GuardError when text holds a private marker. The message never shows the marker."""
    use = private_markers() if markers is None else tuple(markers)
    folded = text.casefold()
    for marker in use:
        if len(marker) < 3:
            raise GuardError(f"privacy guard: a marker for {what} is too short to check safely")
        # "/Users/" is matched with exact case: public URLs such as mastodon.social/users/name are allowed.
        hit = (marker in text) if marker == "/Users/" else (marker.casefold() in folded)
        if hit:
            raise GuardError(f"privacy guard: {what} holds a {_marker_kind(marker)}")
