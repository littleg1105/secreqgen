"""The safety rules of tests/unit/conftest.py hold."""

from __future__ import annotations

import socket

import pytest


def test_a_unit_test_cannot_reach_the_model_server(unit_test_safety: dict) -> None:
    port = unit_test_safety["model_server_port"]
    with pytest.raises(ConnectionRefusedError, match="must not reach the model server"):
        socket.create_connection(("127.0.0.1", port), timeout=1)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        assert sock.connect_ex(("127.0.0.1", port)) != 0


def test_the_copy_dependent_tests_are_skipped_in_a_copy(
    request: pytest.FixtureRequest, unit_test_safety: dict
) -> None:
    names = unit_test_safety["copy_dependent_tests"]
    found = {
        item.name: any(mark.name == "skip" for mark in item.iter_markers())
        for item in request.session.items
        if item.path.name == "test_study2b.py" and item.name in names
    }
    if not found:
        pytest.skip("test_study2b.py is not part of this run")
    in_place = unit_test_safety["in_registered_workspace"]
    assert all(skipped != in_place for skipped in found.values()), found
