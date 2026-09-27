"""evmqtt.core against real kernel input devices created through uinput."""

from __future__ import annotations

import asyncio
import time

import evdev
import pytest
from evdev import UInput, ecodes

from evmqtt.core import (
    DeviceReader,
    KeyEvent,
    KeyState,
    StopReason,
    describe,
    is_keyboard_like,
    list_devices,
    open_device,
)
from tests.fakes import until
from tests.integration.conftest import UInputFactory

pytestmark = pytest.mark.uinput


def tap(ui: UInput, code: int, *states: int) -> None:
    for state in states:
        ui.write(ecodes.EV_KEY, code, state)
        ui.syn()


def start_reader(ui: UInput, events: list[KeyEvent], **kwargs) -> DeviceReader:
    reader = DeviceReader(open_device(ui.device.path), events.append, **kwargs)
    reader.start()
    return reader


async def test_reader_emits_states_modifiers_and_aliases(
    make_uinput: UInputFactory,
) -> None:
    ui = make_uinput(name="evmqtt-test-core-kbd")
    events: list[KeyEvent] = []
    reader = start_reader(ui, events)
    try:
        tap(ui, ecodes.KEY_LEFTSHIFT, 1, 2)
        tap(ui, ecodes.KEY_A, 1, 2, 0)
        tap(ui, ecodes.KEY_LEFTSHIFT, 0)
        tap(ui, ecodes.KEY_MUTE, 1)
        assert await until(lambda: any(e.key == "KEY_MUTE" for e in events))
        plain = [(e.key, e.state, e.modifiers) for e in events if not e.is_modifier]
        assert plain == [
            ("KEY_A", KeyState.PRESS, ("KEY_LEFTSHIFT",)),
            ("KEY_A", KeyState.REPEAT, ("KEY_LEFTSHIFT",)),
            ("KEY_A", KeyState.RELEASE, ("KEY_LEFTSHIFT",)),
            ("KEY_MUTE", KeyState.PRESS, ()),
        ]
        assert events[-1].joined_key == "KEY_MIN_INTERESTING|KEY_MUTE"
        assert events[-1].device_path == ui.device.path
    finally:
        reader.close()


async def test_cancel_is_clean_and_fast_on_idle_device(
    make_uinput: UInputFactory,
) -> None:
    ui = make_uinput(name="evmqtt-test-core-idle")
    reader = DeviceReader(open_device(ui.device.path), lambda e: None)
    task = asyncio.ensure_future(reader.run())
    assert await until(lambda: reader.running)

    started = time.monotonic()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    elapsed = time.monotonic() - started

    assert elapsed < 1.0
    assert reader.result is not None and reader.result.reason is StopReason.STOPPED
    assert reader.device.fd == -1
    other = evdev.InputDevice(ui.device.path)
    try:
        other.grab()
        other.ungrab()
    finally:
        other.close()


async def test_grab_excludes_other_readers_only_while_enabled(
    make_uinput: UInputFactory,
) -> None:
    ui = make_uinput(name="evmqtt-test-core-grab")
    events: list[KeyEvent] = []
    reader = start_reader(ui, events)
    bystander = evdev.InputDevice(ui.device.path)
    try:
        assert reader.grabbed
        tap(ui, ecodes.KEY_A, 1, 0)
        assert await until(lambda: len(events) == 2)
        assert bystander.read_one() is None

        reader.set_enabled(False)
        tap(ui, ecodes.KEY_B, 1, 0)
        seen = [e for e in iter(bystander.read_one, None) if e.type == ecodes.EV_KEY]
        assert [(e.code, e.value) for e in seen] == [
            (ecodes.KEY_B, 1),
            (ecodes.KEY_B, 0),
        ]
        await asyncio.sleep(0)
        assert len(events) == 2

        reader.set_enabled(True)
        with pytest.raises(OSError):
            bystander.grab()
    finally:
        reader.close()
        bystander.close()


async def test_unplug_ends_reader_with_enodev(make_uinput: UInputFactory) -> None:
    ui = make_uinput(name="evmqtt-test-core-unplug")
    stopped = []
    reader = DeviceReader(
        open_device(ui.device.path), lambda e: None, on_stopped=stopped.append
    )
    task = asyncio.ensure_future(reader.run())
    assert await until(lambda: reader.running)
    ui.close()
    result = await asyncio.wait_for(task, 2.0)
    assert result.reason is StopReason.UNPLUGGED
    assert stopped == [result]


def ids_for(phys: str) -> list[str]:
    return [d.id for d in list_devices() if d.phys == phys]


def test_device_id_survives_recreate_and_splits_on_phys(
    make_uinput: UInputFactory,
) -> None:
    kwargs = {"name": "evmqtt-test-id"}
    first = make_uinput(phys="evmqtt-test/usb-1/input0", **kwargs)
    info = describe(first.device)
    assert (info.vendor, info.product) == (0x7E57, 0x0001)
    assert info.phys == "evmqtt-test/usb-1/input0"
    original_id = info.id
    first.close()

    make_uinput(phys="evmqtt-test/usb-2/input0", **kwargs)
    make_uinput(phys="evmqtt-test/usb-1/input0", **kwargs)
    assert ids_for("evmqtt-test/usb-1/input0") == [original_id]
    assert ids_for("evmqtt-test/usb-2/input0") != [original_id]


def test_identical_virtual_devices_get_suffixed_ids(
    make_uinput: UInputFactory,
) -> None:
    kwargs = {"name": "evmqtt-test-dup", "phys": "evmqtt-test/dup"}
    make_uinput(**kwargs)
    make_uinput(**kwargs)
    ids = ids_for("evmqtt-test/dup")
    assert len(ids) == 2
    assert ids[1] == f"{ids[0]}-2"


def test_keyboard_filter_on_real_nodes(make_uinput: UInputFactory) -> None:
    kbd = make_uinput(name="evmqtt-test-filter-kbd", phys="evmqtt-test/filter")
    mouse = make_uinput(
        {
            ecodes.EV_KEY: [ecodes.BTN_LEFT, ecodes.BTN_RIGHT],
            ecodes.EV_REL: [ecodes.REL_X, ecodes.REL_Y],
        },
        name="evmqtt-test-filter-mouse",
        phys="evmqtt-test/filter",
    )
    power = make_uinput(
        {ecodes.EV_KEY: [ecodes.KEY_POWER]},
        name="evmqtt-test-filter-power",
        phys="evmqtt-test/filter",
    )
    found = {
        d.path for d in list_devices(is_keyboard_like) if d.phys == "evmqtt-test/filter"
    }
    assert found == {kbd.device.path}
    assert mouse.device.path not in found and power.device.path not in found
