"""Scenario tests driving InputMonitor.run() through a fake device and fake broker."""

from __future__ import annotations

import asyncio
import json

from evmqtt.config import Config
from evmqtt.core import StopReason
from evmqtt.input_monitor import InputMonitor
from evmqtt.key_handler import KeyHandler
from evmqtt.mqtt_client import MQTTClientWrapper
from tests.fakes import (
    FakeEvdevRegistry,
    FakeInputDevice,
    drained,
    hold,
    keyboard_capabilities,
    msc_event,
    press,
    published,
    rel_event,
    release,
    syn_event,
    until,
)

BASE_TOPIC = "homeassistant/sensor/evmqtt"


def make_config(extra_entries: dict) -> Config:
    return Config.from_dict(
        {
            "serverip": "broker.local",
            "name": "Gateway",
            "topic": BASE_TOPIC,
            "devices": ["/dev/input/event0"],
        }
        | extra_entries
    )


def make_wrapper(fake_mqtt) -> MQTTClientWrapper:
    config = make_config({})
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
        key_handler=KeyHandler(publish_states=wrapper._config.keystates),
        device_slug=slug,
    )
    return device, monitor


async def start(monitor: InputMonitor) -> asyncio.Task:
    task = asyncio.ensure_future(monitor.run())
    assert await until(lambda: monitor.running)
    return task


async def stop_and_join(monitor: InputMonitor, task: asyncio.Task) -> None:
    monitor.stop()
    await task


def last_payload(wrapper: MQTTClientWrapper, topic: str) -> dict:
    return json.loads(published(wrapper.client, topic)[-1].payload)


async def test_press_publishes_key_event(fake_evdev, fake_mqtt) -> None:
    wrapper = make_wrapper(fake_mqtt)
    device, monitor = make_monitor(fake_evdev, wrapper)
    task = await start(monitor)
    try:
        device.push(press("KEY_A"))
        assert await until(lambda: published(wrapper.client, monitor.state_topic))
        payload = last_payload(wrapper, monitor.state_topic)
        assert payload["key"] == "KEY_A"
        assert payload["devicePath"] == "/dev/input/event0"
        assert payload["deviceName"] == "Test Keyboard"
    finally:
        await stop_and_join(monitor, task)


async def test_release_and_repeat_are_dropped(fake_evdev, fake_mqtt) -> None:
    wrapper = make_wrapper(fake_mqtt)
    device, monitor = make_monitor(fake_evdev, wrapper)
    task = await start(monitor)
    try:
        device.push(press("KEY_A"))
        assert await until(lambda: published(wrapper.client, monitor.state_topic))
        count_after_press = len(published(wrapper.client, monitor.state_topic))

        device.push(release("KEY_A"))
        device.push(hold("KEY_A"))
        await drained(device)
        assert len(published(wrapper.client, monitor.state_topic)) == count_after_press
    finally:
        await stop_and_join(monitor, task)


async def test_modifier_suffix_and_multiple_sorted(fake_evdev, fake_mqtt) -> None:
    wrapper = make_wrapper(fake_mqtt)
    device, monitor = make_monitor(fake_evdev, wrapper)
    task = await start(monitor)
    try:
        device.push(press("KEY_LEFTSHIFT"))
        device.push(press("KEY_LEFTCTRL"))
        device.push(press("KEY_A"))
        assert await until(lambda: published(wrapper.client, monitor.state_topic))
        payload = last_payload(wrapper, monitor.state_topic)
        assert payload["key"] == "KEY_A_KEY_LEFTCTRL_KEY_LEFTSHIFT"

        device.push(release("KEY_LEFTSHIFT"))
        device.push(release("KEY_LEFTCTRL"))
        device.push(press("KEY_B"))
        assert await until(
            lambda: len(published(wrapper.client, monitor.state_topic)) >= 2
        )
        payload = last_payload(wrapper, monitor.state_topic)
        assert payload["key"] == "KEY_B"
    finally:
        await stop_and_join(monitor, task)


async def test_modifier_only_and_numlock_dropped(fake_evdev, fake_mqtt) -> None:
    wrapper = make_wrapper(fake_mqtt)
    device, monitor = make_monitor(fake_evdev, wrapper)
    task = await start(monitor)
    try:
        device.push(press("KEY_LEFTSHIFT"))
        device.push(press("KEY_NUMLOCK"))
        device.push(press("KEY_A"))
        assert await until(lambda: published(wrapper.client, monitor.state_topic))
        # only the KEY_A press should have published
        assert len(published(wrapper.client, monitor.state_topic)) == 1
    finally:
        await stop_and_join(monitor, task)


async def test_non_key_events_ignored(fake_evdev, fake_mqtt) -> None:
    wrapper = make_wrapper(fake_mqtt)
    device, monitor = make_monitor(fake_evdev, wrapper)
    task = await start(monitor)
    try:
        device.push(syn_event())
        device.push(msc_event())
        device.push(rel_event())
        device.push(press("KEY_A"))
        assert await until(lambda: published(wrapper.client, monitor.state_topic))
        assert len(published(wrapper.client, monitor.state_topic)) == 1
    finally:
        await stop_and_join(monitor, task)


async def test_grab_failure_exits_quietly(fake_evdev, fake_mqtt) -> None:
    wrapper = make_wrapper(fake_mqtt)
    device, monitor = make_monitor(fake_evdev, wrapper)
    device.fail_grab()
    assert await monitor.run() is None
    assert not monitor.running
    assert not device.grabbed


async def test_unplug_mid_stream_exits_and_ungrabs(fake_evdev, fake_mqtt) -> None:
    wrapper = make_wrapper(fake_mqtt)
    device, monitor = make_monitor(fake_evdev, wrapper)
    task = await start(monitor)
    assert device.grabbed
    device.push(press("KEY_A"))
    assert await until(lambda: published(wrapper.client, monitor.state_topic))
    device.unplug()
    result = await task
    assert result.reason is StopReason.UNPLUGGED
    assert not monitor.running
    assert not device.grabbed


async def test_stop_takes_effect_on_next_queued_event(fake_evdev, fake_mqtt) -> None:
    wrapper = make_wrapper(fake_mqtt)
    device, monitor = make_monitor(fake_evdev, wrapper)
    task = await start(monitor)
    try:
        device.push(press("KEY_A"))
        assert await until(lambda: published(wrapper.client, monitor.state_topic))
        device.push(press("KEY_B"))
        monitor.stop()
        await task
        payloads = [
            json.loads(r.payload)
            for r in published(wrapper.client, monitor.state_topic)
        ]
        assert all(p["key"] != "KEY_B" for p in payloads)
    finally:
        monitor.stop()


async def test_multi_name_keycode_publishes_and_keeps_running(
    fake_evdev, fake_mqtt
) -> None:
    wrapper = make_wrapper(fake_mqtt)
    device, monitor = make_monitor(fake_evdev, wrapper)
    task = await start(monitor)
    try:
        device.push(press("KEY_MUTE"))
        device.push(press("KEY_A"))
        assert await until(
            lambda: len(published(wrapper.client, monitor.state_topic)) >= 2
        )
        payloads = [
            json.loads(r.payload)
            for r in published(wrapper.client, monitor.state_topic)
        ]
        assert any("KEY_MUTE" in p["key"] for p in payloads)
        assert monitor.running
    finally:
        await stop_and_join(monitor, task)


async def test_aliased_button_keycode_publishes(fake_evdev, fake_mqtt) -> None:
    """BTN_LEFT -> ('BTN_LEFT', 'BTN_MOUSE') is a second real-world tuple alias."""
    wrapper = make_wrapper(fake_mqtt)
    device, monitor = make_monitor(fake_evdev, wrapper)
    task = await start(monitor)
    try:
        device.push(press("BTN_LEFT"))
        assert await until(lambda: published(wrapper.client, monitor.state_topic))
        payload = last_payload(wrapper, monitor.state_topic)
        assert "BTN_LEFT" in payload["key"]
        assert monitor.running
    finally:
        await stop_and_join(monitor, task)


async def test_unexpected_exception_in_event_handling_logs_and_continues(
    fake_evdev, fake_mqtt
) -> None:
    """A bug in per-event handling must not kill the monitor."""

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
    task = await start(monitor)
    try:
        device.push(press("KEY_A"))
        assert await until(lambda: published(wrapper.client, monitor.state_topic))
        device.push(press("KEY_B"))
        device.push(press("KEY_C"))
        assert await until(
            lambda: len(published(wrapper.client, monitor.state_topic)) >= 2
        )
        assert monitor.running
        payloads = [
            json.loads(r.payload)
            for r in published(wrapper.client, monitor.state_topic)
        ]
        assert all(p["key"] != "KEY_B" for p in payloads)
    finally:
        await stop_and_join(monitor, task)


async def test_stop_returns_quickly_with_idle_device(fake_evdev, fake_mqtt) -> None:
    wrapper = make_wrapper(fake_mqtt)
    device, monitor = make_monitor(fake_evdev, wrapper)
    task = await start(monitor)
    assert device.grabbed
    monitor.stop()
    await asyncio.wait_for(task, 0.15)
    assert not device.grabbed


async def test_modifier_suffix_survives_autorepeat_hold(fake_evdev, fake_mqtt) -> None:
    wrapper = make_wrapper(fake_mqtt)
    device, monitor = make_monitor(fake_evdev, wrapper)
    task = await start(monitor)
    try:
        device.push(press("KEY_LEFTSHIFT"))
        device.push(hold("KEY_LEFTSHIFT"))
        device.push(press("KEY_A"))
        assert await until(lambda: published(wrapper.client, monitor.state_topic))
        payload = last_payload(wrapper, monitor.state_topic)
        assert payload["key"] == "KEY_A_KEY_LEFTSHIFT"
    finally:
        await stop_and_join(monitor, task)


async def test_keystate_default_is_press_only(fake_evdev, fake_mqtt) -> None:
    wrapper = make_wrapper(fake_mqtt)
    device, monitor = make_monitor(fake_evdev, wrapper)
    task = await start(monitor)
    try:
        device.push(press("KEY_A"))
        device.push(hold("KEY_A"))
        device.push(release("KEY_A"))
        assert await until(lambda: published(wrapper.client, monitor.state_topic))
        payload = last_payload(wrapper, monitor.state_topic)
        assert payload["state"] == "PRESS"
    finally:
        await stop_and_join(monitor, task)


async def test_keystate_is_limited_to_release_when_configured_to_release(
    fake_evdev, fake_mqtt
) -> None:
    config = make_config({"keystates": ["RELEASE"]})
    wrapper = MQTTClientWrapper("test-client", config)
    device, monitor = make_monitor(fake_evdev, wrapper)
    task = await start(monitor)
    try:
        device.push(press("KEY_A"))
        device.push(hold("KEY_A"))
        device.push(release("KEY_A"))
        assert await until(lambda: published(wrapper.client, monitor.state_topic))
        payload = last_payload(wrapper, monitor.state_topic)
        assert payload["state"] == "RELEASE"
    finally:
        await stop_and_join(monitor, task)


async def test_keystates_are_reported_as_configured(fake_evdev, fake_mqtt) -> None:
    config = make_config({"keystates": ["RELEASE", "PRESS", "REPEAT"]})
    wrapper = MQTTClientWrapper("test-client", config)
    device, monitor = make_monitor(fake_evdev, wrapper)
    task = await start(monitor)
    try:
        device.push(press("KEY_A"))
        device.push(hold("KEY_A"))
        device.push(release("KEY_A"))
        assert await until(
            lambda: len(published(wrapper.client, monitor.state_topic)) >= 3
        )
        assert monitor.running

        expected = ["PRESS", "REPEAT", "RELEASE"]
        payloads = [
            json.loads(r.payload)
            for r in published(wrapper.client, monitor.state_topic)
        ]
        assert [p["state"] for p in payloads] == expected
    finally:
        await stop_and_join(monitor, task)


async def test_modifier_press_is_dropped_even_when_release_configured(
    fake_evdev, fake_mqtt
) -> None:
    """A modifier key's own release must stay filtered, all keystates on."""
    config = make_config({"keystates": ["PRESS", "RELEASE", "REPEAT"]})
    wrapper = MQTTClientWrapper("test-client", config)
    device, monitor = make_monitor(fake_evdev, wrapper)
    task = await start(monitor)
    try:
        device.push(press("KEY_LEFTSHIFT"))
        device.push(release("KEY_LEFTSHIFT"))
        device.push(press("KEY_A"))
        device.push(release("KEY_A"))
        assert await until(
            lambda: len(published(wrapper.client, monitor.state_topic)) >= 2
        )
        payloads = [
            json.loads(r.payload)
            for r in published(wrapper.client, monitor.state_topic)
        ]
        assert [p["key"] for p in payloads] == ["KEY_A", "KEY_A"]
        assert [p["state"] for p in payloads] == ["PRESS", "RELEASE"]
    finally:
        await stop_and_join(monitor, task)
