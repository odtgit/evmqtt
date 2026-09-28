"""StateStore persistence and the 2.x -> 3.0 schema migration."""

from __future__ import annotations

import json
from pathlib import Path

from evmqtt.state import VERSION, StateStore


def write(
    path: Path, devices: dict[str, dict[str, object]], version: object = None
) -> None:
    data: dict[str, object] = {"devices": devices}
    if version is not None:
        data["version"] = version
    path.write_text(json.dumps(data))


def test_set_and_enabled_round_trip(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.json")
    store.load()
    assert store.enabled("dev-1") is None
    store.set("dev-1", True, "Kbd", "/dev/input/event0")
    assert store.enabled("dev-1") is True
    assert store.ids() == ["dev-1"]

    reloaded = StateStore(tmp_path / "state.json")
    reloaded.load()
    assert reloaded.enabled("dev-1") is True
    data = json.loads((tmp_path / "state.json").read_text())
    assert data["version"] == VERSION


def test_new_schema_file_is_trusted_as_is(tmp_path: Path, caplog) -> None:
    path = tmp_path / "state.json"
    write(
        path,
        {"dev-1": {"enabled": True, "name": "Kbd A", "path": "/dev/input/event0"}},
        version=VERSION,
    )
    store = StateStore(path)
    store.load(selectors=())
    assert store.enabled("dev-1") is True
    assert "Migrating" not in caplog.text


def test_unversioned_file_disables_unlisted_devices(tmp_path: Path, caplog) -> None:
    path = tmp_path / "state.json"
    write(
        path,
        {
            "kept": {"enabled": True, "name": "Kbd A", "path": "/dev/input/event0"},
            "dropped": {"enabled": True, "name": "Kbd B", "path": "/dev/input/event1"},
            "already-off": {
                "enabled": False,
                "name": "Kbd C",
                "path": "/dev/input/event2",
            },
        },
    )
    store = StateStore(path)
    store.load(selectors=("Kbd A",))

    assert store.enabled("kept") is True
    assert store.enabled("dropped") is False
    assert store.enabled("already-off") is False

    assert "Migrating" in caplog.text
    assert "Kbd B (dropped)" in caplog.text

    data = json.loads(path.read_text())
    assert data["version"] == VERSION
    assert data["devices"]["dropped"]["enabled"] is False
    assert data["devices"]["kept"]["enabled"] is True


def test_version_1_file_is_also_migrated(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    write(
        path,
        {"dev-1": {"enabled": True, "name": "Kbd A", "path": "/dev/input/event0"}},
        version=1,
    )
    store = StateStore(path)
    store.load(selectors=())
    assert store.enabled("dev-1") is False
    assert json.loads(path.read_text())["version"] == VERSION


def test_migration_matches_by_id_path_or_name(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    write(
        path,
        {
            "by-id": {"enabled": True, "name": "A", "path": "/dev/input/event0"},
            "by-path": {"enabled": True, "name": "B", "path": "/dev/input/event1"},
            "by-name": {"enabled": True, "name": "Named Device", "path": "/x"},
            "unmatched": {"enabled": True, "name": "C", "path": "/dev/input/event2"},
        },
    )
    store = StateStore(path)
    store.load(selectors=("by-id", "/dev/input/event1", "Named Device"))
    assert store.enabled("by-id") is True
    assert store.enabled("by-path") is True
    assert store.enabled("by-name") is True
    assert store.enabled("unmatched") is False


def test_migration_with_no_devices_enabled_writes_no_warning(
    tmp_path: Path, caplog
) -> None:
    path = tmp_path / "state.json"
    write(path, {"dev-1": {"enabled": False, "name": "A", "path": "/dev/input/event0"}})
    store = StateStore(path)
    store.load(selectors=())
    assert store.enabled("dev-1") is False
    assert "Migrating" not in caplog.text
    assert json.loads(path.read_text())["version"] == VERSION
