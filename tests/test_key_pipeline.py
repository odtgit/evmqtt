"""Scenario tests driving InputMonitor.run() through a fake device and fake broker."""

from __future__ import annotations

import json

from evmqtt.config import Config
from evmqtt.input_monitor import InputMonitor
from evmqtt.key_handler import KeyHandler
from evmqtt.mqtt_client import MQTTClientWrapper
from tests.fakes import (
    FakeEvdevRegistry,
    FakeInputDevice,
    hold,
    keyboard_capabilities,
    msc_event,
    press,
    published,
    rel_event,
    release,
    syn_event,
    wait_for,
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
    return MQTTClientWrapper("test-client", config)


def make_monitor(
    fake_evdev: FakeEvdevRegistry,
    wrapper: MQTTClientWrapper,
    path: str = "/dev/input/event0",
    slug: str = "test-kb",
    name: str = "Test Keyboard",
) -> tuple[FakeInputDevice, InputMonitor]:
    device = fake_evdev.add(path, name=name, capabilities=keyboard_capabilities())
    monitor = InputMonitor(
        mqtt_client=wrapper,
        device_path=path,
        base_topic=BASE_TOPIC,
        gateway_name="Gateway",
        key_handler=KeyHandler(),
        device_slug=slug,
    )
    return device, monitor


def stop_and_join(device: FakeInputDevice, monitor: InputMonitor) -> None:
    monitor.stop()
    device.unplug()
    monitor.join(timeout=2)


def last_payload(wrapper: MQTTClientWrapper, topic: str) -> dict:
    return json.loads(published(wrapper.client, topic)[-1].payload)


def test_press_publishes_key_event(fake_evdev, fake_mqtt) -> None:
    wrapper = make_wrapper(fake_mqtt)
    device, monitor = make_monitor(fake_evdev, wrapper)
    monitor.start()
    try:
        device.push(press("KEY_A"))
        assert wait_for(lambda: published(wrapper.client, monitor.state_topic))
        payload = last_payload(wrapper, monitor.state_topic)
        assert payload["key"] == "KEY_A"
        assert payload["devicePath"] == "/dev/input/event0"
        assert payload["deviceName"] == "Test Keyboard"
    finally:
        stop_and_join(device, monitor)


def test_release_and_repeat_are_dropped(fake_evdev, fake_mqtt) -> None:
    wrapper = make_wrapper(fake_mqtt)
    device, monitor = make_monitor(fake_evdev, wrapper)
    monitor.start()
    try:
        device.push(press("KEY_A"))
        assert wait_for(lambda: published(wrapper.client, monitor.state_topic))
        count_after_press = len(published(wrapper.client, monitor.state_topic))

        device.push(release("KEY_A"))
        device.push(hold("KEY_A"))
        grew = wait_for(
            lambda: (
                len(published(wrapper.client, monitor.state_topic)) > count_after_press
            ),
            timeout=0.15,
        )
        assert not grew
    finally:
        stop_and_join(device, monitor)


def test_modifier_suffix_and_multiple_sorted(fake_evdev, fake_mqtt) -> None:
    wrapper = make_wrapper(fake_mqtt)
    device, monitor = make_monitor(fake_evdev, wrapper)
    monitor.start()
    try:
        device.push(press("KEY_LEFTSHIFT"))
        device.push(press("KEY_LEFTCTRL"))
        device.push(press("KEY_A"))
        assert wait_for(lambda: published(wrapper.client, monitor.state_topic))
        payload = last_payload(wrapper, monitor.state_topic)
        assert payload["key"] == "KEY_A_KEY_LEFTCTRL_KEY_LEFTSHIFT"

        device.push(release("KEY_LEFTSHIFT"))
        device.push(release("KEY_LEFTCTRL"))
        device.push(press("KEY_B"))
        assert wait_for(
            lambda: len(published(wrapper.client, monitor.state_topic)) >= 2
        )
        payload = last_payload(wrapper, monitor.state_topic)
        assert payload["key"] == "KEY_B"
    finally:
        stop_and_join(device, monitor)


def test_modifier_only_and_numlock_dropped(fake_evdev, fake_mqtt) -> None:
    wrapper = make_wrapper(fake_mqtt)
    device, monitor = make_monitor(fake_evdev, wrapper)
    monitor.start()
    try:
        device.push(press("KEY_LEFTSHIFT"))
        device.push(press("KEY_NUMLOCK"))
        device.push(press("KEY_A"))
        assert wait_for(lambda: published(wrapper.client, monitor.state_topic))
        # only the KEY_A press should have published
        assert len(published(wrapper.client, monitor.state_topic)) == 1
    finally:
        stop_and_join(device, monitor)


def test_non_key_events_ignored(fake_evdev, fake_mqtt) -> None:
    wrapper = make_wrapper(fake_mqtt)
    device, monitor = make_monitor(fake_evdev, wrapper)
    monitor.start()
    try:
        device.push(syn_event())
        device.push(msc_event())
        device.push(rel_event())
        device.push(press("KEY_A"))
        assert wait_for(lambda: published(wrapper.client, monitor.state_topic))
        assert len(published(wrapper.client, monitor.state_topic)) == 1
    finally:
        stop_and_join(device, monitor)


def test_grab_failure_exits_quietly(fake_evdev, fake_mqtt) -> None:
    wrapper = make_wrapper(fake_mqtt)
    device, monitor = make_monitor(fake_evdev, wrapper)
    device.fail_grab()
    monitor.start()
    monitor.join(timeout=2)
    assert not monitor.is_alive()
    assert not device.grabbed


def test_unplug_mid_stream_exits_and_ungrabs(fake_evdev, fake_mqtt) -> None:
    wrapper = make_wrapper(fake_mqtt)
    device, monitor = make_monitor(fake_evdev, wrapper)
    monitor.start()
    assert wait_for(lambda: device.grabbed)
    device.push(press("KEY_A"))
    assert wait_for(lambda: published(wrapper.client, monitor.state_topic))
    device.unplug()
    assert wait_for(lambda: not monitor.is_alive(), timeout=2)
    assert not device.grabbed


def test_stop_takes_effect_on_next_queued_event(fake_evdev, fake_mqtt) -> None:
    wrapper = make_wrapper(fake_mqtt)
    device, monitor = make_monitor(fake_evdev, wrapper)
    monitor.start()
    try:
        device.push(press("KEY_A"))
        assert wait_for(lambda: published(wrapper.client, monitor.state_topic))
        monitor.stop()
        device.push(press("KEY_B"))
        assert wait_for(lambda: not monitor.is_alive(), timeout=2)
        payloads = [
            json.loads(r.payload)
            for r in published(wrapper.client, monitor.state_topic)
        ]
        assert all(p["key"] != "KEY_B" for p in payloads)
    finally:
        device.unplug()


def test_multi_name_keycode_publishes_and_keeps_running(fake_evdev, fake_mqtt) -> None:
    wrapper = make_wrapper(fake_mqtt)
    device, monitor = make_monitor(fake_evdev, wrapper)
    monitor.start()
    try:
        device.push(press("KEY_MUTE"))
        device.push(press("KEY_A"))
        assert wait_for(
            lambda: len(published(wrapper.client, monitor.state_topic)) >= 2,
            timeout=0.15,
        )
        payloads = [
            json.loads(r.payload)
            for r in published(wrapper.client, monitor.state_topic)
        ]
        assert any("KEY_MUTE" in p["key"] for p in payloads)
        assert monitor.is_alive()
    finally:
        stop_and_join(device, monitor)


def test_aliased_button_keycode_publishes(fake_evdev, fake_mqtt) -> None:
    """BTN_LEFT -> ('BTN_LEFT', 'BTN_MOUSE') is a second real-world tuple alias."""
    wrapper = make_wrapper(fake_mqtt)
    device, monitor = make_monitor(fake_evdev, wrapper)
    monitor.start()
    try:
        device.push(press("BTN_LEFT"))
        assert wait_for(lambda: published(wrapper.client, monitor.state_topic))
        payload = last_payload(wrapper, monitor.state_topic)
        assert "BTN_LEFT" in payload["key"]
        assert monitor.is_alive()
    finally:
        stop_and_join(device, monitor)


def test_unexpected_exception_in_event_handling_logs_and_continues(
    fake_evdev, fake_mqtt
) -> None:
    """A bug in per-event handling must not kill the monitor thread."""

    class BoomOnB(KeyHandler):
        def should_publish(self, keycode, keystate):
            primary = keycode[0] if isinstance(keycode, (list, tuple)) else keycode
            if primary == "KEY_B":
                raise RuntimeError("boom")
            return super().should_publish(keycode, keystate)

    wrapper = make_wrapper(fake_mqtt)
    device = fake_evdev.add(
        "/dev/input/event0", name="Test Keyboard", capabilities=keyboard_capabilities()
    )
    monitor = InputMonitor(
        mqtt_client=wrapper,
        device_path="/dev/input/event0",
        base_topic=BASE_TOPIC,
        gateway_name="Gateway",
        key_handler=BoomOnB(),
        device_slug="test-kb",
    )
    monitor.start()
    try:
        device.push(press("KEY_A"))
        assert wait_for(lambda: published(wrapper.client, monitor.state_topic))
        device.push(press("KEY_B"))
        device.push(press("KEY_C"))
        assert wait_for(
            lambda: len(published(wrapper.client, monitor.state_topic)) >= 2
        )
        assert monitor.is_alive()
        payloads = [
            json.loads(r.payload)
            for r in published(wrapper.client, monitor.state_topic)
        ]
        assert all(p["key"] != "KEY_B" for p in payloads)
    finally:
        stop_and_join(device, monitor)


def test_stop_returns_quickly_with_idle_device(fake_evdev, fake_mqtt) -> None:
    wrapper = make_wrapper(fake_mqtt)
    device, monitor = make_monitor(fake_evdev, wrapper)
    monitor.start()
    assert wait_for(lambda: device.grabbed)
    monitor.stop()
    assert wait_for(lambda: not monitor.is_alive(), timeout=0.15)
    assert not device.grabbed


def test_modifier_suffix_survives_autorepeat_hold(fake_evdev, fake_mqtt) -> None:
    wrapper = make_wrapper(fake_mqtt)
    device, monitor = make_monitor(fake_evdev, wrapper)
    monitor.start()
    try:
        device.push(press("KEY_LEFTSHIFT"))
        device.push(hold("KEY_LEFTSHIFT"))
        device.push(press("KEY_A"))
        assert wait_for(lambda: published(wrapper.client, monitor.state_topic))
        payload = last_payload(wrapper, monitor.state_topic)
        assert payload["key"] == "KEY_A_KEY_LEFTSHIFT"
    finally:
        stop_and_join(device, monitor)
