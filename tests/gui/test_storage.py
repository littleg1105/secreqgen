"""File work of the GUI: ids, manifests, verdicts and uploads."""

from __future__ import annotations

import json

import pytest

pytest.importorskip("nicegui")

from secreqgen_gui import storage  # noqa: E402


def test_run_ids_and_clash():
    first = storage.new_run_id("threats")
    second = storage.new_run_id("threats")
    assert first != second
    assert first.endswith("-threats")
    assert second.endswith("-2") or second.split("-")[1] != first.split("-")[1]
    assert storage.run_dir(first).is_dir()
    assert storage.new_run_id("requirements").endswith("-requirements")


@pytest.mark.parametrize(
    "bad", ["", "../x", "..", "a/b", "20261117-214500-other", "x-threats"]
)
def test_bad_run_id_is_refused(bad):
    with pytest.raises(ValueError):
        storage.run_dir(bad)
    with pytest.raises(ValueError):
        storage.read_manifest(bad)
    with pytest.raises(ValueError):
        storage.delete_run(bad)


def test_bad_kind_is_refused():
    with pytest.raises(ValueError):
        storage.new_run_id("../threats")


def _make_run(per_story=None):
    run_id = storage.new_run_id("threats")
    storage.write_manifest(
        run_id,
        {
            "run_id": run_id,
            "kind": "threats",
            "status": "finished",
            "created_at": "2026-11-17T14:30:12Z",
            "per_story": per_story or [],
        },
    )
    return run_id


def test_manifest_round_trip_and_list():
    run_id = _make_run()
    manifest = storage.read_manifest(run_id)
    assert manifest["status"] == "finished"
    listed = storage.list_runs()
    assert [item["id"] for item in listed] == [run_id]
    assert listed[0]["kind"] == "threats"
    assert not list(storage.run_dir(run_id).glob("*.tmp"))


def test_list_runs_newest_first_and_damaged():
    old = _make_run()
    (storage.runs_dir() / "20200101-000000-threats").mkdir()
    (storage.runs_dir() / "20200101-000000-threats" / "run.json").write_text("{x")
    listed = storage.list_runs()
    assert listed[0]["id"] == old
    assert listed[-1] == {"id": "20200101-000000-threats", "status": "damaged"}
    assert storage.read_manifest("20200101-000000-threats") is None


def test_interrupted_run_is_marked():
    run_id = storage.new_run_id("threats")
    storage.write_manifest(run_id, {"status": "running"})
    storage.mark_interrupted_runs()
    assert storage.read_manifest(run_id)["status"] == "interrupted"


def test_delete_run():
    run_id = _make_run()
    storage.delete_run(run_id)
    assert storage.list_runs() == []


def _story(run_id, story_id="MM-US-007", folder="MM-US-007"):
    path = storage.run_dir(run_id) / "stories" / folder
    path.mkdir(parents=True)
    (path / "threats.json").write_text(
        json.dumps({"rows": [{"id": "T1", "quote": "the quote"}]}), encoding="utf-8"
    )
    (path / "threats.md").write_text("# page\n", encoding="utf-8")
    return path


def test_story_folder_comes_from_the_manifest():
    run_id = _make_run([{"story_id": "MM-US-007", "folder": "MM-US-007"}])
    folder = _story(run_id)
    assert storage.story_folder(run_id, "MM-US-007") == folder.resolve()
    assert storage.story_folder(run_id, "UNKNOWN") is None
    assert storage.read_threats(run_id, "MM-US-007")["rows"][0]["id"] == "T1"
    assert storage.read_threats_page(run_id, "MM-US-007") == "# page\n"
    assert storage.read_threats(run_id, "UNKNOWN") is None


def test_story_folder_refuses_a_path_in_the_manifest():
    run_id = _make_run([{"story_id": "X", "folder": "../../x"}])
    assert storage.story_folder(run_id, "X") is None


def test_verdicts():
    run_id = _make_run([{"story_id": "MM-US-007", "folder": "MM-US-007"}])
    _story(run_id)
    assert storage.read_verdicts(run_id, "MM-US-007") == {}
    storage.write_verdict(run_id, "MM-US-007", "T1", "keep")
    assert storage.read_verdicts(run_id, "MM-US-007") == {"T1": "keep"}
    storage.write_verdict(run_id, "MM-US-007", "T1", "delete")
    assert storage.read_verdicts(run_id, "MM-US-007") == {"T1": "delete"}
    storage.write_verdict(run_id, "MM-US-007", "T1", None)
    assert storage.read_verdicts(run_id, "MM-US-007") == {}


def test_verdict_checks():
    run_id = _make_run([{"story_id": "MM-US-007", "folder": "MM-US-007"}])
    _story(run_id)
    with pytest.raises(ValueError):
        storage.write_verdict(run_id, "MM-US-007", "T1", "maybe")
    with pytest.raises(ValueError):
        storage.write_verdict(run_id, "MM-US-007", "../T1", "keep")
    with pytest.raises(ValueError):
        storage.write_verdict(run_id, "MM-US-007", "T9", "keep")
    with pytest.raises(ValueError):
        storage.write_verdict(run_id, "UNKNOWN", "T1", "keep")


def test_verdict_for_another_quote_is_left_out():
    run_id = _make_run([{"story_id": "MM-US-007", "folder": "MM-US-007"}])
    folder = _story(run_id)
    storage.write_verdict(run_id, "MM-US-007", "T1", "keep")
    (folder / "threats.json").write_text(
        json.dumps({"rows": [{"id": "T1", "quote": "another quote"}]}),
        encoding="utf-8",
    )
    assert storage.read_verdicts(run_id, "MM-US-007") == {}


def test_upload_is_saved_under_uploads():
    path = storage.save_upload("stories.json", b"{}")
    assert path.parent == storage.uploads_dir()
    assert path.read_bytes() == b"{}"
    again = storage.save_upload("stories.json", b"{}")
    assert again != path


@pytest.mark.parametrize("name", ["run.exe", "x.py", "noext", "x.json.sh", "x.JSONX"])
def test_upload_with_a_bad_suffix_is_refused(name):
    with pytest.raises(ValueError):
        storage.save_upload(name, b"{}")
    assert list(storage.uploads_dir().iterdir()) == []


def test_upload_with_too_many_bytes_is_refused():
    with pytest.raises(ValueError):
        storage.save_upload("big.json", b"x" * (storage.MAX_UPLOAD_BYTES + 1))
    storage.save_upload("edge.json", b"x" * storage.MAX_UPLOAD_BYTES)
    with pytest.raises(ValueError):
        storage.save_upload("empty.json", b"")


@pytest.mark.parametrize(
    "name", ["../../etc/passwd.json", "..\\..\\evil.json", "/abs/x.json", "a b/c d.md"]
)
def test_upload_name_with_a_path_gets_a_safe_name(name):
    path = storage.save_upload(name, b"{}")
    assert path.parent == storage.uploads_dir()
    assert ".." not in path.name and "/" not in path.name and "\\" not in path.name
    assert path.resolve().parent == storage.uploads_dir().resolve()


def test_all_paths_stay_inside_the_data_home(gui_home):
    for folder in (
        storage.runs_dir(),
        storage.uploads_dir(),
        storage.prompt_sets_dir(),
    ):
        assert folder.resolve().parent == gui_home.resolve()


def test_the_date_of_a_run_is_shown_in_local_time(monkeypatch):
    import time

    from secreqgen_gui import storage

    monkeypatch.setenv("TZ", "Europe/Athens")
    time.tzset()
    try:
        item = {"created_at": "2026-11-17T20:43:06Z", "id": "x"}
        assert storage.run_date_text(item) == "17 November 2026, 22:43"
        # With no time in the manifest, the run id gives it (also UTC).
        only_id = {"id": "20261117-235900-threats"}
        assert storage.run_date_text(only_id) == "18 November 2026, 01:59"
        assert storage.run_date_text({"id": "no time here"}) == ""
        assert storage.run_date_text({}) == ""
    finally:
        monkeypatch.undo()
        time.tzset()
