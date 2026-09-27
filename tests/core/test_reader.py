"""Scenario tests for DeviceReader driven through FakeInputDevice pipe fds."""

from __future__ import annotations

import asyncio
import errno

import evdev
import pytest

from evmqtt.core import (
    DeviceReader,
    GrabMode,
    KeyConfig,
    KeyEvent,
    KeyState,
    ReaderStopped,
    StopReason,
    describe,
)
from tests.fakes import (
    FakeInputDevice,
    drained,
    hold,
    key_event,
    keyboard_capabilities,
    msc_event,
    press,
    rel_event,
    release,
    syn_event,
    until,
)


def make_device(path: str = "/dev/input/event0", name: str = "Kbd") -> FakeInputDevice:
    return FakeInputDevice(
        path, name, keyboard_capabilities(), phys="usb-0000:00:14.0-1/input0"
    )


class Sink:
    def __init__(self) -> None:
        self.events: list[KeyEvent] = []
        self.stopped: list[ReaderStopped] = []

    def __call__(self, event: KeyEvent) -> None:
        self.events.append(event)

    def keys(self) -> list[tuple[str, KeyState]]:
        return [(e.key, e.state) for e in self.events]


def make_reader(device: FakeInputDevice, sink: Sink, **kwargs) -> DeviceReader:
    return DeviceReader(device, sink, on_stopped=sink.stopped.append, **kwargs)


async def test_emits_all_states_with_device_identity() -> None:
    device = make_device()
    sink = Sink()
    reader = make_reader(device, sink)
    reader.start()
    try:
        device.push_all([press("KEY_A"), hold("KEY_A"), release("KEY_A")])
        await drained(device)
        assert sink.keys() == [
            ("KEY_A", KeyState.PRESS),
            ("KEY_A", KeyState.REPEAT),
            ("KEY_A", KeyState.RELEASE),
        ]
        event = sink.events[0]
        assert event.device_id == describe(device).id
        assert event.device_name == "Kbd"
        assert event.device_path == "/dev/input/event0"
        assert event.names == ("KEY_A",)
        assert event.modifiers == ()
        assert event.is_modifier is False
        assert event.timestamp > 0
    finally:
        reader.close()


async def test_non_key_events_and_ignored_keys_are_skipped() -> None:
    device = make_device()
    sink = Sink()
    reader = make_reader(device, sink)
    reader.start()
    try:
        device.push_all(
            [
                syn_event(),
                msc_event(),
                rel_event(),
                press("KEY_NUMLOCK"),
                press("KEY_B"),
            ]
        )
        await drained(device)
        assert sink.keys() == [("KEY_B", KeyState.PRESS)]
    finally:
        reader.close()


async def test_ignored_and_modifier_sets_are_configurable() -> None:
    device = make_device()
    sink = Sink()
    config = KeyConfig(modifiers=frozenset({"KEY_CAPSLOCK"}), ignored=frozenset())
    reader = make_reader(device, sink, key_config=config)
    reader.start()
    try:
        device.push_all(
            [
                press("KEY_NUMLOCK"),
                press("KEY_CAPSLOCK"),
                press("KEY_LEFTSHIFT"),
                press("KEY_A"),
            ]
        )
        await drained(device)
        assert [e.key for e in sink.events] == [
            "KEY_NUMLOCK",
            "KEY_CAPSLOCK",
            "KEY_LEFTSHIFT",
            "KEY_A",
        ]
        assert sink.events[-1].modifiers == ("KEY_CAPSLOCK",)
        assert sink.events[1].is_modifier is True
        assert sink.events[2].is_modifier is False
    finally:
        reader.close()


async def test_modifiers_held_through_repeat_and_released() -> None:
    device = make_device()
    sink = Sink()
    reader = make_reader(device, sink)
    reader.start()
    try:
        device.push_all(
            [
                press("KEY_LEFTSHIFT"),
                hold("KEY_LEFTSHIFT"),
                press("KEY_RIGHTCTRL"),
                press("KEY_A"),
                release("KEY_LEFTSHIFT"),
                press("KEY_B"),
                release("KEY_RIGHTCTRL"),
                press("KEY_C"),
            ]
        )
        await drained(device)
        by_key = {e.key: e for e in sink.events if e.state is KeyState.PRESS}
        assert by_key["KEY_A"].modifiers == ("KEY_LEFTSHIFT", "KEY_RIGHTCTRL")
        assert by_key["KEY_B"].modifiers == ("KEY_RIGHTCTRL",)
        assert by_key["KEY_C"].modifiers == ()
        assert sink.events[0].is_modifier is True
        assert sink.events[0].modifiers == ("KEY_LEFTSHIFT",)
    finally:
        reader.close()


async def test_modifiers_are_per_device() -> None:
    device_a = make_device("/dev/input/event0", "Kbd A")
    device_b = make_device("/dev/input/event1", "Kbd B")
    sink = Sink()
    reader_a = make_reader(device_a, sink)
    reader_b = make_reader(device_b, sink)
    reader_a.start()
    reader_b.start()
    try:
        device_a.push(press("KEY_LEFTSHIFT"))
        await drained(device_a)
        device_b.push(press("KEY_A"))
        device_a.push(press("KEY_Z"))
        await drained(device_a, device_b)
        pressed = {
            (e.device_name, e.key): e.modifiers
            for e in sink.events
            if not e.is_modifier
        }
        assert pressed == {
            ("Kbd B", "KEY_A"): (),
            ("Kbd A", "KEY_Z"): ("KEY_LEFTSHIFT",),
        }
    finally:
        reader_a.close()
        reader_b.close()


@pytest.mark.parametrize(
    ("name", "key", "joined"),
    [
        ("KEY_MUTE", "KEY_MUTE", "KEY_MIN_INTERESTING|KEY_MUTE"),
        ("BTN_LEFT", "BTN_LEFT", "BTN_LEFT|BTN_MOUSE"),
        ("KEY_COFFEE", "KEY_COFFEE", "KEY_COFFEE|KEY_SCREENLOCK"),
        ("BTN_SOUTH", "BTN_SOUTH", "BTN_A|BTN_GAMEPAD|BTN_SOUTH"),
    ],
)
async def test_aliased_codes_have_canonical_key_and_joined_form(
    name: str, key: str, joined: str
) -> None:
    device = make_device()
    sink = Sink()
    reader = make_reader(device, sink)
    reader.start()
    try:
        device.push_all([press(name), press("KEY_A")])
        await drained(device)
        assert [e.key for e in sink.events] == [key, "KEY_A"]
        assert sink.events[0].joined_key == joined
        assert reader.running
    finally:
        reader.close()


async def test_unknown_code_and_value_do_not_break_reader() -> None:
    device = make_device()
    sink = Sink()
    reader = make_reader(device, sink)
    reader.start()
    try:
        device.push(evdev.InputEvent(0, 1, evdev.ecodes.EV_KEY, 0x2FE, 1))
        device.push(key_event("KEY_A", 7))
        device.push(press("KEY_B"))
        await drained(device)
        assert [e.key for e in sink.events] == ["KEY_766", "KEY_B"]
    finally:
        reader.close()


async def test_unplug_ends_reader_with_lifecycle_signal() -> None:
    device = make_device()
    sink = Sink()
    reader = make_reader(device, sink)
    task = asyncio.ensure_future(reader.run())
    assert await until(lambda: reader.running)
    assert device.grabbed
    device.push(press("KEY_A"))
    device.unplug()
    result = await task
    assert result.reason is StopReason.UNPLUGGED
    assert result.error is not None and result.error.errno == errno.ENODEV
    assert result.device.path == "/dev/input/event0"
    assert sink.stopped == [result]
    assert sink.keys() == [("KEY_A", KeyState.PRESS)]
    assert not device.grabbed
    assert device.closed
    assert not reader.running
    assert reader.result is result


async def test_read_error_other_than_enodev_is_error() -> None:
    device = make_device()
    reader = DeviceReader(device, Sink())
    reader.start()
    device._queue.put(OSError(errno.EIO, "I/O error"))
    device._wake()
    result = await reader.wait()
    assert result.reason is StopReason.ERROR
    assert device.closed


async def test_cancel_run_ungrabs_and_closes() -> None:
    device = make_device()
    sink = Sink()
    reader = make_reader(device, sink)
    task = asyncio.ensure_future(reader.run())
    assert await until(lambda: reader.running)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not device.grabbed
    assert device.closed
    assert sink.stopped[0].reason is StopReason.STOPPED
    assert (await reader.wait()).reason is StopReason.STOPPED


async def test_close_drops_rest_of_batch_and_is_idempotent() -> None:
    device = make_device()
    sink = Sink()
    reader: DeviceReader

    def on_event(event: KeyEvent) -> None:
        sink(event)
        reader.close()

    reader = DeviceReader(device, on_event, on_stopped=sink.stopped.append)
    reader.start()
    device.push_all([press("KEY_A"), press("KEY_B")])
    result = await reader.wait()
    reader.close()
    assert result.reason is StopReason.STOPPED
    assert sink.keys() == [("KEY_A", KeyState.PRESS)]
    assert len(sink.stopped) == 1


async def test_callback_exception_is_logged_and_reader_continues(caplog) -> None:
    device = make_device()
    seen: list[str] = []

    def on_event(event: KeyEvent) -> None:
        if event.key == "KEY_B":
            raise RuntimeError("boom")
        seen.append(event.key)

    reader = DeviceReader(device, on_event)
    reader.start()
    try:
        device.push_all([press("KEY_A"), press("KEY_B"), press("KEY_C")])
        await drained(device)
        assert seen == ["KEY_A", "KEY_C"]
        assert "boom" in caplog.text
    finally:
        reader.close()


async def test_enable_disable_toggles_grab_and_emission() -> None:
    device = make_device()
    sink = Sink()
    reader = make_reader(device, sink, enabled=False)
    reader.start()
    try:
        assert not device.grabbed
        device.push_all([press("KEY_LEFTSHIFT"), press("KEY_A")])
        await drained(device)
        assert sink.events == []

        reader.set_enabled(True)
        assert device.grabbed and reader.grabbed and reader.enabled
        device.push(press("KEY_B"))
        await drained(device)
        assert [(e.key, e.modifiers) for e in sink.events] == [
            ("KEY_B", ("KEY_LEFTSHIFT",))
        ]

        reader.set_enabled(False)
        assert not device.grabbed
        device.push(press("KEY_C"))
        await drained(device)
        assert len(sink.events) == 1
        reader.set_enabled(False)
        assert device.grab_calls == 1
    finally:
        reader.close()


async def test_grab_always_keeps_grab_while_disabled() -> None:
    device = make_device()
    reader = DeviceReader(device, Sink(), grab=GrabMode.ALWAYS, enabled=False)
    reader.start()
    try:
        assert device.grabbed
        reader.set_enabled(True)
        reader.set_enabled(False)
        assert device.grabbed
        assert device.grab_calls == 1
    finally:
        reader.close()
    assert not device.grabbed


async def test_grab_never() -> None:
    device = make_device()
    sink = Sink()
    reader = make_reader(device, sink, grab=GrabMode.NEVER)
    reader.start()
    try:
        reader.set_enabled(False)
        reader.set_enabled(True)
        device.push(press("KEY_A"))
        await drained(device)
        assert device.grab_calls == 0
        assert len(sink.events) == 1
    finally:
        reader.close()


async def test_grab_failure_on_start_raises_and_reports_error() -> None:
    device = make_device()
    device.fail_grab()
    sink = Sink()
    reader = make_reader(device, sink)
    with pytest.raises(OSError):
        reader.start()
    assert device.closed
    result = await reader.wait()
    assert result.reason is StopReason.ERROR
    assert sink.stopped == [result]


async def test_grab_failure_on_enable_raises_and_stays_disabled() -> None:
    device = make_device()
    reader = DeviceReader(device, Sink(), enabled=False)
    reader.start()
    try:
        device.fail_grab(OSError(errno.EBUSY, "busy"))
        with pytest.raises(OSError):
            reader.set_enabled(True)
        assert not reader.enabled
        assert reader.running
    finally:
        reader.close()


async def test_start_twice_and_wait_before_start_raise() -> None:
    device = make_device()
    reader = DeviceReader(device, Sink())
    with pytest.raises(RuntimeError):
        await reader.wait()
    reader.start()
    with pytest.raises(RuntimeError):
        reader.start()
    reader.close()


async def test_on_stopped_exception_is_contained(caplog) -> None:
    device = make_device()

    def boom(_: ReaderStopped) -> None:
        raise RuntimeError("stopped boom")

    reader = DeviceReader(device, Sink(), on_stopped=boom)
    reader.start()
    device.unplug()
    result = await reader.wait()
    assert result.reason is StopReason.UNPLUGGED
    assert "stopped boom" in caplog.text
