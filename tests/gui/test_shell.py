"""The shell shows the six entries, and each route opens its page."""

from __future__ import annotations

import pytest

pytest.importorskip("nicegui")

pytestmark = pytest.mark.anyio

ENTRIES = ["Home", "Threat list", "Requirements", "History", "Prompts", "Settings"]
RUN = "20261117-214500-threats"

ROUTES = [
    ("/", "Home"),
    ("/threats/new", "This page is not built yet."),
    ("/requirements/new", "This page is not built yet."),
    (f"/runs/{RUN}", f"Run {RUN}"),
    (f"/runs/{RUN}/stories/MM-US-007", "Threat list of MM-US-007"),
    (f"/runs/{RUN}/requirements", "Requirements of the run"),
    ("/history", "This page is not built yet."),
    ("/prompts", "This page is not built yet."),
    ("/prompts/threats/my-set", "Prompt set my-set"),
    ("/settings", "This page is not built yet."),
]


async def test_rail_shows_six_entries(user):
    await user.open("/")
    await user.should_see("SecReqGen")
    for entry in ENTRIES:
        await user.should_see(entry)


@pytest.mark.parametrize("path,expected", ROUTES)
async def test_each_route_opens_its_page(user, path, expected):
    await user.open(path)
    await user.should_see(expected)


def test_route_table_matches_the_design():
    from secreqgen_gui.app import ROUTES

    assert set(ROUTES) == {
        "/",
        "/threats/new",
        "/requirements/new",
        "/runs/{run_id}",
        "/runs/{run_id}/stories/{story_id}",
        "/runs/{run_id}/requirements",
        "/history",
        "/prompts",
        "/prompts/{kind}/{name}",
        "/settings",
    }


def test_server_listens_on_this_computer_by_default(monkeypatch):
    from secreqgen_gui import settings

    monkeypatch.delenv("SECREQGEN_GUI_HOST", raising=False)
    monkeypatch.delenv("SECREQGEN_GUI_PORT", raising=False)
    assert settings.server_host() == "127.0.0.1"
    assert settings.server_port() == 8080
    monkeypatch.setenv("SECREQGEN_GUI_PORT", "not a number")
    assert settings.server_port() == 8080


def test_settings_round_trip_and_broken_file(gui_home):
    from secreqgen_gui import settings

    assert settings.load_settings() == settings.GuiSettings()
    changed = settings.GuiSettings(base_url="http://example.test:1", writer_model="a:1")
    settings.save_settings(changed)
    assert settings.load_settings() == changed
    (gui_home / "settings.json").write_text("{broken", encoding="utf-8")
    assert settings.load_settings() == settings.GuiSettings()


def test_start_file_makes_a_spec(tmp_path):
    from secreqgen_gui.jobs import ThreatRunSpec, spec_from_dict

    spec = spec_from_dict(
        {"kind": "threats", "stories_file": "s.json", "docs": "d", "story_ids": ["A"]}
    )
    assert isinstance(spec, ThreatRunSpec)
    assert spec.story_ids == ("A",)
    with pytest.raises(ValueError):
        spec_from_dict({"kind": "threats", "stories_file": "s", "docs": "d", "x": 1})


def test_start_file_starts_a_run(tmp_path, monkeypatch):
    from secreqgen_gui import app

    started = []
    monkeypatch.setattr(
        app.manager, "start_threats", lambda s: started.append(s) or "r1"
    )
    path = tmp_path / "start.json"
    path.write_text(
        '{"kind": "threats", "stories_file": "s.json", "docs": "d"}', encoding="utf-8"
    )
    assert app.start_run_from_file(path) == "r1"
    assert started[0].stories_file.name == "s.json"
    path.write_text("{broken", encoding="utf-8")
    assert app.start_run_from_file(path) is None
    assert app.start_run_from_file(tmp_path / "missing.json") is None


async def test_rail_shows_the_model_server_state(user):
    await user.open("/")
    await user.should_see("Model server answers")
