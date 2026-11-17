"""The job manager, with stand-in run functions. No model server is used."""

from __future__ import annotations

import json
import threading
from types import SimpleNamespace

import pytest

pytest.importorskip("nicegui")

from secreqgen_gui import storage  # noqa: E402
from secreqgen_gui.jobs import (  # noqa: E402
    JobManager,
    RequirementsRunSpec,
    RunState,
    ThreatRunSpec,
)


def fake_status(reachable: bool = True, models: tuple[str, ...] = ()):
    infos = tuple(SimpleNamespace(name=name) for name in models)
    return SimpleNamespace(reachable=reachable, models=infos, error=None)

WRITER = "gemma4:26b"
CHECKER = "qwen3.8:27b"


@pytest.fixture
def story_file(tmp_path):
    stories = [
        {"id": f"S-{n}", "title": f"Story {n}", "story": "As a user, I act."}
        for n in (1, 2, 3)
    ]
    path = tmp_path / "stories.json"
    path.write_text(
        json.dumps({"project": "demo", "user_stories": stories}), encoding="utf-8"
    )
    return path


@pytest.fixture
def spec(story_file, tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    return ThreatRunSpec(stories_file=story_file, docs=docs, product="demo")


def good_server():
    return lambda url: fake_status(True, (WRITER, CHECKER))


def make_threat_run(hook=None, fail=None):
    """Return a stand-in for run_threat_lists."""
    from secreqgen.application.threats import ThreatListSummary

    def run(
        options,
        *,
        cfg=None,
        make_client=None,
        on_progress=None,
        on_story_start=None,
        should_stop=None,
    ):
        data = json.loads(options.input_file.read_text(encoding="utf-8"))
        ids = [s["id"] for s in data["user_stories"]]
        result = SimpleNamespace(
            lists=[], failures=[], skipped=[], stopped=False, aborted=None, not_run=[]
        )
        for number, story_id in enumerate(ids, start=1):
            if should_stop is not None and should_stop():
                result.stopped = True
                result.not_run = ids[number - 1 :]
                return result
            on_story_start(story_id, number, len(ids))
            if hook is not None:
                hook(number)
            if fail is not None:
                raise fail
            folder = options.output / story_id
            folder.mkdir(parents=True)
            rows = [{"id": "T1", "quote": "q", "second_model": "refuses"}]
            (folder / "threats.json").write_text(
                json.dumps({"rows": rows}), encoding="utf-8"
            )
            summary = ThreatListSummary(1, 1, 0, 0, 0, 0, 2, 1)
            outcome = SimpleNamespace(
                story_id=story_id, model={"rows": rows}, summary=summary, folder=folder
            )
            on_progress(
                SimpleNamespace(
                    done=number,
                    total=len(ids),
                    story_id=story_id,
                    error=None,
                    outcome=outcome,
                )
            )
        return result

    return run


def test_run_goes_through_checking_running_and_done(spec):
    seen = []
    manager = JobManager(check_server=good_server())
    manager._run_threats = make_threat_run(
        hook=lambda number: seen.append(manager.current().state)
    )
    run_id = manager.start_threats(spec)
    assert manager.current().state in ("checking", "running", "done")
    manager.join(10)
    state = manager.get(run_id)
    assert seen == ["running"] * 3
    assert state.state == "done"
    assert not manager.busy()
    assert [s.state for s in state.stories] == ["done"] * 3
    assert state.stories[0].summary == "1 row, 0 accepted, 1 with a fault"
    assert state.stories[0].seconds is not None
    assert state.finished is not None and state.version > 3

    manifest = storage.read_manifest(run_id)
    assert manifest["status"] == "finished"
    # The manifest keeps the count of rows with a fault for the run page.
    assert manifest["per_story"][0]["faults"] == 1
    assert manifest["kind"] == "threats"
    assert manifest["writer"] == WRITER and manifest["checker"] == CHECKER
    assert manifest["stories_total"] == 3
    assert [e["story_id"] for e in manifest["per_story"]] == ["S-1", "S-2", "S-3"]
    assert storage.story_folder(run_id, "S-2").name == "S-2"
    assert storage.read_threats(run_id, "S-2")["rows"][0]["id"] == "T1"
    assert manifest["prompt_set"]["tested"] is True
    assert (storage.run_dir(run_id) / "stories.json").is_file()


def test_the_manager_checks_the_server_first(spec):
    called = []
    manager = JobManager(
        run_threats=lambda *a, **k: called.append(1),
        check_server=lambda url: fake_status(False),
    )
    run_id = manager.start_threats(spec)
    manager.join(10)
    state = manager.get(run_id)
    assert state.state == "failed"
    assert "does not answer" in state.message and "Check again" in state.message
    assert called == []
    assert storage.read_manifest(run_id)["status"] == "failed"
    assert [s.state for s in state.stories] == ["not run"] * 3


def test_a_missing_model_gives_the_pull_command(spec):
    manager = JobManager(
        run_threats=lambda *a, **k: None,
        check_server=lambda url: fake_status(True, (WRITER,)),
    )
    run_id = manager.start_threats(spec)
    manager.join(10)
    state = manager.get(run_id)
    assert state.state == "failed"
    assert f"The model {CHECKER} is missing. Run: ollama pull {CHECKER}" in (
        state.message
    )


def test_a_stop_request_leaves_the_rest_not_run(spec):
    holder = {}
    manager = JobManager(check_server=good_server())
    manager._run_threats = make_threat_run(
        hook=lambda number: holder["m"].request_stop() if number == 1 else None
    )
    holder["m"] = manager
    run_id = manager.start_threats(spec)
    manager.join(10)
    state = manager.get(run_id)
    assert state.state == "stopped"
    assert state.stop_requested is True
    assert [s.state for s in state.stories] == ["done", "not run", "not run"]
    manifest = storage.read_manifest(run_id)
    assert manifest["status"] == "stopped"
    assert manifest["not_run"] == ["S-2", "S-3"]


def test_an_exception_gives_state_failed_and_frees_the_manager(spec):
    manager = JobManager(check_server=good_server())
    manager._run_threats = make_threat_run(fail=RuntimeError("boom"))
    run_id = manager.start_threats(spec)
    manager.join(10)
    state = manager.get(run_id)
    assert state.state == "failed"
    assert "boom" in state.message
    assert [s.state for s in state.stories] == ["failed", "not run", "not run"]
    assert not manager.busy()
    assert storage.read_manifest(run_id)["error"]["type"] == "RuntimeError"
    manager._run_threats = make_threat_run()
    second = manager.start_threats(spec)
    manager.join(10)
    assert second != run_id
    assert manager.get(second).state == "done"


def test_a_second_start_while_busy_raises(spec):
    release = threading.Event()
    manager = JobManager(check_server=good_server())
    manager._run_threats = make_threat_run(hook=lambda number: release.wait(10))
    manager.start_threats(spec)
    try:
        assert manager.busy()
        with pytest.raises(RuntimeError, match="A run is in work."):
            manager.start_threats(spec)
        with pytest.raises(RuntimeError):
            manager.start_requirements(
                RequirementsRunSpec(stories_file=spec.stories_file, docs=spec.docs)
            )
    finally:
        release.set()
        manager.join(10)
    assert not manager.busy()


def test_a_bad_story_file_fails_with_a_message(spec):
    spec.stories_file.write_text("[1, 2]", encoding="utf-8")
    manager = JobManager(
        run_threats=lambda *a, **k: None, check_server=good_server()
    )
    run_id = manager.start_threats(spec)
    manager.join(10)
    assert manager.get(run_id).state == "failed"
    assert "user_stories" in manager.get(run_id).message


def test_get_gives_none_for_an_older_run(spec):
    manager = JobManager(check_server=good_server())
    manager._run_threats = make_threat_run()
    manager.start_threats(spec)
    manager.join(10)
    assert manager.get("20200101-000000-threats") is None
    assert manager.current() is not manager.current()


def test_server_status_never_raises():
    def broken(url):
        raise OSError("down")

    status = JobManager(check_server=broken).server_status("http://x")
    assert status.reachable is False


def test_a_step_report_changes_only_the_version():
    manager = JobManager()
    manager._state = RunState("r1", "threats", "running", "Running")
    manager._bump()
    manager._bump_steps()
    assert manager._state.version == 2
    assert manager._state.story_version == 1
