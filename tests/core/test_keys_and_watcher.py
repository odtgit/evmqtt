"""Key naming, modifier tracking, rescan diffing, and the no-paho import guarantee."""

from __future__ import annotations

import subprocess
import sys

import pytest
from evdev import ecodes

from evmqtt.core import (
    DEFAULT_IGNORED,
    DEFAULT_MODIFIERS,
    DeviceInfo,
    DeviceWatcher,
    ModifierTracker,
    canonical_name,
    is_keyboard_like,
    key_name,
    key_names,
)
from tests.fakes import FakeEvdevRegistry, keyboard_capabilities, mouse_capabilities


@pytest.mark.parametrize(
    ("code", "canonical"),
    [
        (ecodes.KEY_MUTE, "KEY_MUTE"),
        (ecodes.KEY_HANGEUL, "KEY_HANGEUL"),
        (ecodes.KEY_COFFEE, "KEY_COFFEE"),
        (ecodes.KEY_ROTATE_DISPLAY, "KEY_ROTATE_DISPLAY"),
        (ecodes.KEY_ALL_APPLICATIONS, "KEY_ALL_APPLICATIONS"),
        (ecodes.KEY_BRIGHTNESS_AUTO, "KEY_BRIGHTNESS_AUTO"),
        (ecodes.KEY_WWAN, "KEY_WWAN"),
        (ecodes.KEY_FULL_SCREEN, "KEY_FULL_SCREEN"),
        (ecodes.KEY_ASPECT_RATIO, "KEY_ASPECT_RATIO"),
        (ecodes.KEY_DISPLAYTOGGLE, "KEY_DISPLAYTOGGLE"),
        (ecodes.BTN_0, "BTN_0"),
        (ecodes.BTN_LEFT, "BTN_LEFT"),
        (ecodes.BTN_TRIGGER, "BTN_TRIGGER"),
        (ecodes.BTN_SOUTH, "BTN_SOUTH"),
        (ecodes.BTN_EAST, "BTN_EAST"),
        (ecodes.BTN_NORTH, "BTN_NORTH"),
        (ecodes.BTN_WEST, "BTN_WEST"),
        (ecodes.BTN_TOOL_PEN, "BTN_TOOL_PEN"),
        (ecodes.BTN_GEAR_DOWN, "BTN_GEAR_DOWN"),
        (ecodes.BTN_TRIGGER_HAPPY1, "BTN_TRIGGER_HAPPY1"),
        (ecodes.KEY_A, "KEY_A"),
        (0x2FE, "KEY_766"),
    ],
)
def test_canonical_name_follows_kernel_primary(code: int, canonical: str) -> None:
    assert key_name(code) == canonical
    assert canonical in key_names(code) or code == 0x2FE


def test_every_aliased_code_resolves_to_a_name_it_has() -> None:
    for names in ecodes.keys.values():
        if isinstance(names, str):
            continue
        assert canonical_name(tuple(names)) in names


def test_defaults() -> None:
    assert "KEY_LEFTSHIFT" in DEFAULT_MODIFIERS
    assert "KEY_RIGHTMETA" in DEFAULT_MODIFIERS
    assert len(DEFAULT_MODIFIERS) == 8
    assert DEFAULT_IGNORED == {"KEY_NUMLOCK"}


def test_modifier_tracker() -> None:
    tracker = ModifierTracker()
    tracker.update(("KEY_RIGHTALT",), 1)
    tracker.update(("KEY_LEFTSHIFT",), 2)
    tracker.update(("KEY_A",), 1)
    assert tracker.active == ("KEY_LEFTSHIFT", "KEY_RIGHTALT")
    tracker.update(("KEY_RIGHTALT",), 0)
    assert tracker.active == ("KEY_LEFTSHIFT",)
    assert tracker.is_modifier(("KEY_LEFTSHIFT",))
    assert not tracker.is_modifier(("KEY_A",))
    tracker.clear()
    assert tracker.active == ()


def info(path: str, device_id: str) -> DeviceInfo:
    return DeviceInfo(
        path=path,
        name=device_id,
        id=device_id,
        phys="",
        uniq="",
        bustype=3,
        vendor=0,
        product=0,
        version=0,
        event_types=frozenset(),
        key_codes=frozenset(),
    )


def test_watcher_update_reports_added_removed_and_moved() -> None:
    added: list[str] = []
    removed: list[str] = []
    watcher = DeviceWatcher(
        on_added=lambda d: added.append(f"{d.id}@{d.path}"),
        on_removed=lambda d: removed.append(f"{d.id}@{d.path}"),
    )
    watcher.update([info("/dev/input/event0", "a"), info("/dev/input/event1", "b")])
    assert added == ["a@/dev/input/event0", "b@/dev/input/event1"]
    added.clear()

    new, gone = watcher.update(
        [info("/dev/input/event0", "a"), info("/dev/input/event2", "b")]
    )
    assert removed == ["b@/dev/input/event1"]
    assert added == ["b@/dev/input/event2"]
    assert [d.id for d in new] == ["b"] and [d.id for d in gone] == ["b"]

    removed.clear()
    watcher.update([])
    assert removed == ["a@/dev/input/event0", "b@/dev/input/event2"]
    assert watcher.devices == {}


async def test_watcher_rescan_uses_predicate_and_sees_hotplug(
    fake_evdev: FakeEvdevRegistry,
) -> None:
    fake_evdev.add("/dev/input/event0", "Kbd", keyboard_capabilities(), phys="a")
    fake_evdev.add("/dev/input/event1", "Mouse", mouse_capabilities(), phys="b")
    watcher = DeviceWatcher(predicate=is_keyboard_like)

    added, removed = await watcher.rescan()
    assert [d.name for d in added] == ["Kbd"] and removed == []

    fake_evdev.add("/dev/input/event2", "Remote", keyboard_capabilities(), phys="c")
    added, removed = await watcher.rescan()
    assert [d.name for d in added] == ["Remote"] and removed == []

    fake_evdev.remove("/dev/input/event0")
    added, removed = await watcher.rescan()
    assert added == [] and [d.name for d in removed] == ["Kbd"]


def test_core_imports_without_paho() -> None:
    code = (
        "import sys; sys.modules['paho'] = None; "
        "import evmqtt.core, evmqtt.core.reader; "
        "assert not any(m.startswith('paho') for m in sys.modules if sys.modules[m])"
    )
    subprocess.run([sys.executable, "-c", code], check=True)
