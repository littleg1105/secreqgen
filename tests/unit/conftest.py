"""Safety of the unit tests, wherever the repository is.

1. No unit test reaches a model server. A connection to the Ollama port is
   refused, so a test that needs a model fails at once. Tests with a real
   model are integration tests (tests/integration).
2. Four tests of the Study 2b runner hold only in the registered workspace.
   The lab script scripts/run_study2.py names that workspace as a fixed path,
   and the guards of the runner protect that path. In a copy of the
   repository at another place the guards do not refuse the folders of the
   copy, and one of the four tests then starts real generations (seen on
   17 November 2026: 10 model calls in a scratch copy). In a copy the four
   tests are skipped.
"""

from __future__ import annotations

import os
import re
import socket
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
MODEL_SERVER_PORT = 11434
COPY_DEPENDENT_TESTS = {
    "test_guard_output_folder",
    "test_main_refuses_output_inside_study2_and_writes_nothing",
    "test_analyzer_refuses_before_the_labels_are_locked",
    "test_analyzer_reproduces_the_registered_arms_a_and_b",
}

_real_connect = socket.socket.connect
_real_connect_ex = socket.socket.connect_ex


def _is_model_server(address: object) -> bool:
    if not isinstance(address, tuple) or len(address) < 2:
        return False
    return address[1] == MODEL_SERVER_PORT


def _connect(self: socket.socket, address: object) -> None:
    if _is_model_server(address):
        raise ConnectionRefusedError("a unit test must not reach the model server")
    return _real_connect(self, address)


def _connect_ex(self: socket.socket, address: object) -> int:
    if _is_model_server(address):
        return 61  # "connection refused"
    return _real_connect_ex(self, address)


@pytest.fixture(autouse=True, scope="session")
def no_model_server_in_unit_tests():
    socket.socket.connect = _connect  # type: ignore[method-assign]
    socket.socket.connect_ex = _connect_ex  # type: ignore[method-assign]
    yield
    socket.socket.connect = _real_connect  # type: ignore[method-assign]
    socket.socket.connect_ex = _real_connect_ex  # type: ignore[method-assign]


@pytest.fixture(scope="session")
def unit_test_safety() -> dict:
    """The safety facts of this run, for the tests of the safety itself."""
    return {
        "model_server_port": MODEL_SERVER_PORT,
        "copy_dependent_tests": set(COPY_DEPENDENT_TESTS),
        "in_registered_workspace": in_registered_workspace(),
    }


def registered_coded_project() -> Path | None:
    """The folder that scripts/run_study2.py names for the registered data."""
    try:
        text = (REPO / "scripts" / "run_study2.py").read_text(encoding="utf-8")
    except OSError:
        return None
    pattern = r'^THESIS_ROOT\s*=\s*Path\(\s*"([^"]+)"\s*\)'
    match = re.search(pattern, text, re.MULTILINE)
    return Path(match.group(1)) if match else None


def in_registered_workspace() -> bool:
    """True when this repository is the one inside the registered workspace."""
    registered = registered_coded_project()
    if registered is None:
        return False
    try:
        return os.path.samefile(registered, REPO.parent)
    except OSError:
        return False


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    if in_registered_workspace():
        return
    skip = pytest.mark.skip(
        reason="holds only in the registered workspace; a copy can start model calls"
    )
    for item in items:
        if item.path.name == "test_study2b.py" and item.name in COPY_DEPENDENT_TESTS:
            item.add_marker(skip)
