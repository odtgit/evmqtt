"""Integration scenario tests against a real kernel input device via uinput.

These exercise InputMonitor's select()-based read loop against an actual
evdev character device (created with evdev.UInput), rather than the
FakeInputDevice pipe model used by the fast tier. This is what caught the
original stop()-hang bug: closing an fd does not wake a thread blocked in
select() on it on Linux, so a fake that models close() as "wakes the
reader" can pass while the real thing hangs.

Skipped unless the runner can both write to /dev/uinput and actually
expose the resulting /dev/input/eventN node (some hosted CI containers
allow the former without the latter - no udev/devtmpfs enumeration of
input devices - in which case evdev.UInput.device is None). Run
explicitly with `pytest -m uinput`.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Iterator

import pytest
from evdev import UInput, ecodes

from evmqtt.config import Config
from evmqtt.input_monitor import InputMonitor
from evmqtt.key_handler import KeyHandler
from evmqtt.mqtt_client import MQTTClientWrapper
from tests.fakes import published, wait_for

pytestmark = pytest.mark.uinput

# Cheap check only; this deliberately does not open /dev/uinput so importing
# this module (e.g. during collection of the fast tier, where these tests
# are deselected) never touches real hardware.
UINPUT_NODE_WRITABLE = os.path.exists("/dev/uinput") and os.access(
    "/dev/uinput", os.W_OK
)

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


def stop_and_join(monitor: InputMonitor) -> None:
    monitor.stop()
    monitor.join(timeout=2)


@pytest.fixture
def virtual_keyboard() -> Iterator[UInput]:
    if not UINPUT_NODE_WRITABLE:
        pytest.skip("/dev/uinput not writable")
    capabilities = {
        ecodes.EV_KEY: sorted(
            {
                ecodes.KEY_A,
                ecodes.KEY_B,
                ecodes.KEY_MUTE,
                ecodes.KEY_LEFTSHIFT,
            }
        )
    }
    ui = UInput(capabilities, name="evmqtt-test-keyboard")
    if ui.device is None:
        # /dev/uinput is writable but the runner never exposed a matching
        # /dev/input/eventN node (no udev/devtmpfs enumeration of input
        # devices in some hosted CI containers).
        ui.close()
        pytest.skip("uinput device created but no /dev/input/eventN node is visible")
    try:
        # Give the kernel a moment to register the new /dev/input/eventN node.
        time.sleep(0.1)
        yield ui
    finally:
        ui.close()


def test_press_publishes_key_event(virtual_keyboard, fake_mqtt) -> None:
    wrapper = make_wrapper(fake_mqtt)
    monitor = make_monitor(wrapper, virtual_keyboard.device.path)
    monitor.start()
    try:
        assert wait_for(lambda: monitor.is_alive())
        press(virtual_keyboard, ecodes.KEY_A)
        release(virtual_keyboard, ecodes.KEY_A)
        assert wait_for(lambda: published(wrapper.client, monitor.state_topic))
        payload = last_payload(wrapper, monitor.state_topic)
        assert payload["key"] == "KEY_A"
    finally:
        stop_and_join(monitor)


def test_aliased_keycode_publishes_and_keeps_running(
    virtual_keyboard, fake_mqtt
) -> None:
    """KEY_MUTE -> ('KEY_MIN_INTERESTING', 'KEY_MUTE') on a real device."""
    wrapper = make_wrapper(fake_mqtt)
    monitor = make_monitor(wrapper, virtual_keyboard.device.path)
    monitor.start()
    try:
        assert wait_for(lambda: monitor.is_alive())
        press(virtual_keyboard, ecodes.KEY_MUTE)
        release(virtual_keyboard, ecodes.KEY_MUTE)
        press(virtual_keyboard, ecodes.KEY_A)
        release(virtual_keyboard, ecodes.KEY_A)
        assert wait_for(
            lambda: len(published(wrapper.client, monitor.state_topic)) >= 2
        )
        payloads = [
            json.loads(r.payload)
            for r in published(wrapper.client, monitor.state_topic)
        ]
        assert any("KEY_MUTE" in p["key"] for p in payloads)
        assert monitor.is_alive()
    finally:
        stop_and_join(monitor)


def test_modifier_suffix_survives_autorepeat_hold(virtual_keyboard, fake_mqtt) -> None:
    wrapper = make_wrapper(fake_mqtt)
    monitor = make_monitor(wrapper, virtual_keyboard.device.path)
    monitor.start()
    try:
        assert wait_for(lambda: monitor.is_alive())
        press(virtual_keyboard, ecodes.KEY_LEFTSHIFT)
        hold(virtual_keyboard, ecodes.KEY_LEFTSHIFT)
        press(virtual_keyboard, ecodes.KEY_A)
        assert wait_for(lambda: published(wrapper.client, monitor.state_topic))
        payload = last_payload(wrapper, monitor.state_topic)
        assert payload["key"] == "KEY_A_KEY_LEFTSHIFT"
    finally:
        release(virtual_keyboard, ecodes.KEY_A)
        release(virtual_keyboard, ecodes.KEY_LEFTSHIFT)
        stop_and_join(monitor)


def test_stop_returns_quickly_with_idle_device(virtual_keyboard, fake_mqtt) -> None:
    """The bug this whole tier exists for: stop() must not hang against a
    real device fd blocked in select() with no events pending."""
    wrapper = make_wrapper(fake_mqtt)
    monitor = make_monitor(wrapper, virtual_keyboard.device.path)
    monitor.start()
    assert wait_for(lambda: monitor.is_alive())
    time.sleep(0.05)  # let run() get past grab() and into select()

    start = time.monotonic()
    monitor.stop()
    monitor.join(timeout=1.0)
    elapsed = time.monotonic() - start

    assert not monitor.is_alive()
    assert elapsed < 1.0


def test_unplug_ends_monitor_cleanly(virtual_keyboard, fake_mqtt) -> None:
    wrapper = make_wrapper(fake_mqtt)
    monitor = make_monitor(wrapper, virtual_keyboard.device.path)
    monitor.start()
    assert wait_for(lambda: monitor.is_alive())
    time.sleep(0.05)

    virtual_keyboard.close()  # destroys the /dev/input/eventN node
    assert wait_for(lambda: not monitor.is_alive(), timeout=2)
