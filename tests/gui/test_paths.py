"""Paths in the short form of the home folder."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("nicegui")

from nicegui import ui  # noqa: E402
from secreqgen_gui.settings import expand_path, show_path  # noqa: E402

pytestmark = pytest.mark.anyio


@pytest.fixture
def home(tmp_path, monkeypatch):
    folder = tmp_path / "ann"
    folder.mkdir()
    monkeypatch.setenv("HOME", str(folder))
    return folder


def test_show_path_cases(home):
    assert show_path(home) == "~"
    assert show_path(home / "a" / "b") == "~/a/b"
    assert show_path(str(home) + "2/x") == str(home) + "2/x"
    assert show_path("/opt/data") == "/opt/data"
    assert show_path("") == ""
    assert show_path(None) == ""


def test_expand_path_cases(home):
    assert expand_path("  ~/x.json ") == home / "x.json"
    assert expand_path(Path("~")) == home
    assert expand_path("/opt/a") == Path("/opt/a")


def test_spec_from_dict_expands_home(home):
    from secreqgen_gui import jobs

    spec = jobs.spec_from_dict(
        {
            "kind": "requirements",
            "stories_file": "~/s.json",
            "docs": "~/docs",
            "system_context": "~/c.yaml",
        }
    )
    assert spec.stories_file == home / "s.json"
    assert spec.docs == home / "docs"
    assert spec.system_context == str(home / "c.yaml")


async def test_setup_page_shows_short_paths(user, home):
    docs = home / "docs"
    docs.mkdir()
    (docs / "a.md").write_text("# A\nText")
    item = {"id": "US-1", "title": "T", "story": "As a user, I want x."}
    (home / "x.json").write_text(json.dumps({"user_stories": [item]}))
    await user.open("/threats/new?stories=~/x.json&docs=~/docs&product=Demo")
    await user.should_see("1 story found.")
    await user.should_see("1 Markdown file found.")
    values = {e.value for e in user.find(kind=ui.input).elements}
    assert {"~/x.json", "~/docs", "Demo"} <= values


async def test_product_is_cut_to_80_characters(user, home):
    await user.open("/threats/new?product=" + "a" * 120)
    values = {e.value for e in user.find(kind=ui.input).elements}
    assert "a" * 80 in values
    assert "a" * 81 not in values


async def test_settings_page_shows_short_data_folder(user, home, monkeypatch):
    monkeypatch.setenv("SECREQGEN_GUI_HOME", str(home / "data"))
    await user.open("/settings")
    await user.should_see("~/data")


def test_the_allowed_host_names(monkeypatch):
    from secreqgen_gui import settings

    monkeypatch.delenv(settings.HOST_ENV, raising=False)
    monkeypatch.delenv(settings.ALLOWED_HOSTS_ENV, raising=False)
    assert settings.allowed_hosts() == ["127.0.0.1", "localhost"]
    assert settings.listens_on_this_computer_only()
    # An address for every network card is not a name of a request.
    monkeypatch.setenv(settings.HOST_ENV, "0.0.0.0")
    monkeypatch.setenv(settings.ALLOWED_HOSTS_ENV, " lab-pc , localhost,")
    assert settings.allowed_hosts() == ["127.0.0.1", "localhost", "lab-pc"]
    assert not settings.listens_on_this_computer_only()
    monkeypatch.setenv(settings.HOST_ENV, "192.168.1.20")
    assert "192.168.1.20" in settings.allowed_hosts()
