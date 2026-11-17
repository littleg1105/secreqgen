"""Fixtures of the GUI tests. They run in the GUI environment only."""

from __future__ import annotations

from types import SimpleNamespace

import pytest


@pytest.fixture(autouse=True)
def gui_home(tmp_path, monkeypatch):
    """Give every test its own data folder."""
    home = tmp_path / "gui_home"
    monkeypatch.setenv("SECREQGEN_GUI_HOME", str(home))
    monkeypatch.setenv("NICEGUI_STORAGE_PATH", str(tmp_path / "nicegui"))
    return home


def fake_status():
    """Return a stand-in for the answer of the model server check."""
    return SimpleNamespace(reachable=True, models=(), error=None)


@pytest.fixture(autouse=True)
def quiet_manager(monkeypatch):
    """Reset the shared job manager. No test reaches a model server."""
    pytest.importorskip("nicegui")
    from secreqgen_gui import jobs

    monkeypatch.setattr(jobs.manager, "_state", None)
    monkeypatch.setattr(jobs.manager, "_thread", None)
    monkeypatch.setattr(jobs.manager, "_check_server", lambda url: fake_status())
    return jobs.manager


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def user():
    pytest.importorskip("nicegui")
    from nicegui.testing.user_simulation import user_simulation
    from secreqgen_gui.app import root

    async with user_simulation(root=root) as simulated:
        yield simulated
