"""Integration scenario tests against a real kernel input device via uinput.

These exercise the daemon's InputMonitor on the core asyncio reader against
an actual evdev character device (created with evdev.UInput), rather than
the FakeInputDevice pipe model used by the fast tier. This tier caught the
original 1.x stop()-hang bug, where closing an fd did not wake a thread
blocked in select() on it; the fake cannot model that class of bug.

Skipped unless the runner can both write to /dev/uinput and actually
expose the resulting /dev/input/eventN node (some CI setups can open
/dev/uinput but the eventN node comes up root:input 0660, unreadable by
the runner user, so evdev.UInput.device is None). Run explicitly with
`pytest -m uinput`.

Set EVMQTT_REQUIRE_UINPUT=1 to turn that skip into a hard failure. CI
sets this so a runner that is supposed to support uinput (module loaded,
udev rules in place) fails loudly instead of silently skipping if it
regresses; local runs without uinput are left alone and skip as usual.
"""

from __future__ import annotations

import asyncio
import json
import time

import pytest
from evdev import UInput, ecodes

from evmqtt.config import Config
from evmqtt.core import StopReason
from evmqtt.input_monitor import InputMonitor
from evmqtt.key_handler import KeyHandler
from evmqtt.mqtt_client import MQTTClientWrapper
from tests.fakes import published, until
from tests.integration.conftest import UInputFactory

pytestmark = pytest.mark.uinput

BASE_TOPIC = "homeassistant/sensor/evmqtt"


def make_wrapper(fake_mqtt) -> MQTTClientWrapper:
    config = Config.from_dict(
        {
            "serverip": "broker.local",
            "name": "Gateway",
            "topic": BASE_TOPIC,
            "devices": ["/dev/input/event0"],
        }
    )
    return MQTTClientWrapper("uinput-test", config)


def make_monitor(
    wrapper: MQTTClientWrapper, path: str, slug: str = "uinput-kb"
) -> InputMonitor:
    return InputMonitor(
        mqtt_client=wrapper,
        device_path=path,
        base_topic=BASE_TOPIC,
        gateway_name="Gateway",
        key_handler=KeyHandler(),
        device_slug=slug,
    )


def last_payload(wrapper: MQTTClientWrapper, topic: str) -> dict:
    return json.loads(published(wrapper.client, topic)[-1].payload)


def press(ui: UInput, code: int) -> None:
    ui.write(ecodes.EV_KEY, code, 1)
    ui.syn()


def release(ui: UInput, code: int) -> None:
    ui.write(ecodes.EV_KEY, code, 0)
    ui.syn()


def hold(ui: UInput, code: int) -> None:
    ui.write(ecodes.EV_KEY, code, 2)
    ui.syn()


async def start(monitor: InputMonitor) -> asyncio.Task:
    task = asyncio.ensure_future(monitor.run())
    assert await until(lambda: monitor.running)
    return task


async def stop_and_join(monitor: InputMonitor, task: asyncio.Task) -> None:
    monitor.stop()
    await asyncio.wait_for(task, 2)


@pytest.fixture
def virtual_keyboard(make_uinput: UInputFactory) -> UInput:
    return make_uinput(name="evmqtt-test-keyboard")


async def test_press_publishes_key_event(virtual_keyboard, fake_mqtt) -> None:
    wrapper = make_wrapper(fake_mqtt)
    monitor = make_monitor(wrapper, virtual_keyboard.device.path)
    task = await start(monitor)
    try:
        press(virtual_keyboard, ecodes.KEY_A)
        release(virtual_keyboard, ecodes.KEY_A)
        assert await until(lambda: published(wrapper.client, monitor.state_topic))
        payload = last_payload(wrapper, monitor.state_topic)
        assert payload["key"] == "KEY_A"
    finally:
        await stop_and_join(monitor, task)


async def test_aliased_keycode_publishes_and_keeps_running(
    virtual_keyboard, fake_mqtt
) -> None:
    """KEY_MUTE -> ('KEY_MIN_INTERESTING', 'KEY_MUTE') on a real device."""
    wrapper = make_wrapper(fake_mqtt)
    monitor = make_monitor(wrapper, virtual_keyboard.device.path)
    task = await start(monitor)
    try:
        press(virtual_keyboard, ecodes.KEY_MUTE)
        release(virtual_keyboard, ecodes.KEY_MUTE)
        press(virtual_keyboard, ecodes.KEY_A)
        release(virtual_keyboard, ecodes.KEY_A)
        assert await until(
            lambda: len(published(wrapper.client, monitor.state_topic)) >= 2
        )
        payloads = [
            json.loads(r.payload)
            for r in published(wrapper.client, monitor.state_topic)
        ]
        assert payloads[0]["key"] == "KEY_MIN_INTERESTING|KEY_MUTE"
        assert monitor.running
    finally:
        await stop_and_join(monitor, task)


async def test_modifier_suffix_survives_autorepeat_hold(
    virtual_keyboard, fake_mqtt
) -> None:
    wrapper = make_wrapper(fake_mqtt)
    monitor = make_monitor(wrapper, virtual_keyboard.device.path)
    task = await start(monitor)
    try:
        press(virtual_keyboard, ecodes.KEY_LEFTSHIFT)
        hold(virtual_keyboard, ecodes.KEY_LEFTSHIFT)
        press(virtual_keyboard, ecodes.KEY_A)
        assert await until(lambda: published(wrapper.client, monitor.state_topic))
        payload = last_payload(wrapper, monitor.state_topic)
        assert payload["key"] == "KEY_A_KEY_LEFTSHIFT"
    finally:
        release(virtual_keyboard, ecodes.KEY_A)
        release(virtual_keyboard, ecodes.KEY_LEFTSHIFT)
        await stop_and_join(monitor, task)


async def test_stop_returns_quickly_with_idle_device(
    virtual_keyboard, fake_mqtt
) -> None:
    """The bug this whole tier exists for: stop() must not hang on a real,
    idle device fd."""
    wrapper = make_wrapper(fake_mqtt)
    monitor = make_monitor(wrapper, virtual_keyboard.device.path)
    task = await start(monitor)

    started = time.monotonic()
    monitor.stop()
    await asyncio.wait_for(task, 1.0)
    elapsed = time.monotonic() - started

    assert not monitor.running
    assert elapsed < 1.0


async def test_unplug_ends_monitor_cleanly(virtual_keyboard, fake_mqtt) -> None:
    wrapper = make_wrapper(fake_mqtt)
    monitor = make_monitor(wrapper, virtual_keyboard.device.path)
    task = await start(monitor)

    virtual_keyboard.close()  # destroys the /dev/input/eventN node
    result = await asyncio.wait_for(task, 2)
    assert result.reason is StopReason.UNPLUGGED
    assert not monitor.running
