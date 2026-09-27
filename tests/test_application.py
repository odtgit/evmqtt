"""End-to-end scenario tests for Application through fake evdev + fake broker."""

from __future__ import annotations

import json

import pytest

from evmqtt.__main__ import Application
from evmqtt.config import Config
from evmqtt.device_discovery import slugify
from tests.fakes import (
    keyboard_capabilities,
    mouse_capabilities,
    press,
    published,
    wait_for,
)

BASE_TOPIC = "homeassistant/sensor/evmqtt"


def make_config(**overrides) -> Config:
    data: dict[str, object] = {
        "serverip": "broker.local",
        "name": "Gateway",
        "topic": BASE_TOPIC,
        "devices": [],
        "auto_discover": True,
        "filter_keys_only": True,
        "enabled_devices": [],
    }
    data.update(overrides)
    return Config.from_dict(data)


def sensor_config_topic(slug: str) -> str:
    return f"{BASE_TOPIC}/{slug}/config"


def switch_state_topic(slug: str) -> str:
    return f"{BASE_TOPIC}/{slug}/switch/state"


def switch_command_topic(slug: str) -> str:
    return f"{BASE_TOPIC}/{slug}/switch/set"


def state_topic(slug: str) -> str:
    return f"{BASE_TOPIC}/{slug}/state"


def test_auto_discover_publishes_ha_config_subscribes_and_skips_non_key(
    fake_evdev, fake_mqtt
) -> None:
    fake_evdev.add(
        "/dev/input/event0", name="Kbd A", capabilities=keyboard_capabilities()
    )
    fake_evdev.add(
        "/dev/input/event1", name="Mouse A", capabilities=mouse_capabilities()
    )
    config = make_config()
    app = Application(config, connect_timeout=1.0)
    app.start()
    try:
        client = fake_mqtt.last()
        kbd_slug = slugify("Kbd A")

        assert wait_for(lambda: published(client, sensor_config_topic(kbd_slug)))
        sensor_record = published(client, sensor_config_topic(kbd_slug))[-1]
        assert sensor_record.retain is True
        sensor_config = json.loads(sensor_record.payload)
        assert sensor_config["device"]["name"] == "Kbd A"

        switch_state_records = published(client, switch_state_topic(kbd_slug))
        assert switch_state_records[-1].payload == "ON"
        assert switch_state_records[-1].retain is True

        assert switch_command_topic(kbd_slug) in client.subscriptions

        mouse_slug = slugify("Mouse A")
        assert published(client, sensor_config_topic(mouse_slug)) == []
    finally:
        app.stop()


def test_filter_keys_only_false_includes_non_key_device(fake_evdev, fake_mqtt) -> None:
    fake_evdev.add(
        "/dev/input/event0", name="Mouse A", capabilities=mouse_capabilities()
    )
    config = make_config(filter_keys_only=False)
    app = Application(config, connect_timeout=1.0)
    app.start()
    try:
        client = fake_mqtt.last()
        slug = slugify("Mouse A")
        assert wait_for(lambda: published(client, sensor_config_topic(slug)))
    finally:
        app.stop()


def test_duplicate_names_get_unique_topics(fake_evdev, fake_mqtt) -> None:
    fake_evdev.add(
        "/dev/input/event0", name="Kbd", capabilities=keyboard_capabilities()
    )
    fake_evdev.add(
        "/dev/input/event1", name="Kbd", capabilities=keyboard_capabilities()
    )
    config = make_config()
    app = Application(config, connect_timeout=1.0)
    app.start()
    try:
        client = fake_mqtt.last()
        assert wait_for(
            lambda: (
                len(
                    [
                        r
                        for r in client.published
                        if r.topic.startswith(BASE_TOPIC)
                        and r.topic.endswith("/config")
                    ]
                )
                >= 2
            )
        )
        config_topics = {
            r.topic
            for r in client.published
            if r.topic.startswith(BASE_TOPIC) and r.topic.endswith("/config")
        }
        assert len(config_topics) == 2
    finally:
        app.stop()


def test_enabled_devices_subset_others_off_and_dropped(fake_evdev, fake_mqtt) -> None:
    device_a = fake_evdev.add(
        "/dev/input/event0", name="Kbd A", capabilities=keyboard_capabilities()
    )
    device_b = fake_evdev.add(
        "/dev/input/event1", name="Kbd B", capabilities=keyboard_capabilities()
    )
    config = make_config(enabled_devices=["/dev/input/event0"])
    app = Application(config, connect_timeout=1.0)
    app.start()
    try:
        client = fake_mqtt.last()
        slug_a = slugify("Kbd A")
        slug_b = slugify("Kbd B")

        assert wait_for(lambda: published(client, switch_state_topic(slug_a)))
        assert published(client, switch_state_topic(slug_a))[-1].payload == "ON"
        assert wait_for(lambda: published(client, switch_state_topic(slug_b)))
        assert published(client, switch_state_topic(slug_b))[-1].payload == "OFF"

        device_a.push(press("KEY_A"))
        assert wait_for(lambda: published(client, state_topic(slug_a)))

        device_b.push(press("KEY_A"))
        grew = wait_for(
            lambda: len(published(client, state_topic(slug_b))) > 0, timeout=0.15
        )
        assert not grew
    finally:
        app.stop()


def test_switch_command_off_on_and_garbage_ignored(fake_evdev, fake_mqtt) -> None:
    device = fake_evdev.add(
        "/dev/input/event0", name="Kbd A", capabilities=keyboard_capabilities()
    )
    config = make_config()
    app = Application(config, connect_timeout=1.0)
    app.start()
    try:
        client = fake_mqtt.last()
        slug = slugify("Kbd A")

        client.inject(switch_command_topic(slug), "OFF")
        assert wait_for(
            lambda: published(client, switch_state_topic(slug))[-1].payload == "OFF"
        )

        device.push(press("KEY_A"))
        grew = wait_for(
            lambda: len(published(client, state_topic(slug))) > 0, timeout=0.15
        )
        assert not grew

        client.inject(switch_command_topic(slug), "garbage")
        assert published(client, switch_state_topic(slug))[-1].payload == "OFF"

        client.inject(switch_command_topic(slug), "ON")
        assert wait_for(
            lambda: published(client, switch_state_topic(slug))[-1].payload == "ON"
        )

        device.push(press("KEY_B"))
        assert wait_for(lambda: published(client, state_topic(slug)))
    finally:
        app.stop()


def test_manual_mode_uses_path_based_topics(fake_evdev, fake_mqtt) -> None:
    fake_evdev.add(
        "/dev/input/event0", name="Manual Kbd", capabilities=keyboard_capabilities()
    )
    config = make_config(
        auto_discover=False, devices=["/dev/input/event0"], enabled_devices=[]
    )
    app = Application(config, connect_timeout=1.0)
    app.start()
    try:
        client = fake_mqtt.last()
        expected_slug = "_dev_input_event0"
        assert wait_for(lambda: published(client, sensor_config_topic(expected_slug)))
    finally:
        app.stop()


def test_manual_mode_skips_missing_and_denied_devices(fake_evdev, fake_mqtt) -> None:
    fake_evdev.add(
        "/dev/input/event0", name="Good Kbd", capabilities=keyboard_capabilities()
    )
    fake_evdev.deny("/dev/input/denied")
    config = make_config(
        auto_discover=False,
        devices=["/dev/input/missing", "/dev/input/denied", "/dev/input/event0"],
    )
    app = Application(config, connect_timeout=1.0)
    app.start()
    try:
        client = fake_mqtt.last()
        assert wait_for(
            lambda: published(client, sensor_config_topic("_dev_input_event0"))
        )
        config_topics = {
            r.topic
            for r in client.published
            if r.topic.startswith(BASE_TOPIC) and r.topic.endswith("/config")
        }
        assert config_topics == {sensor_config_topic("_dev_input_event0")}
    finally:
        app.stop()


def test_manual_mode_all_devices_fail_raises_runtime_error(
    fake_evdev, fake_mqtt
) -> None:
    config = make_config(auto_discover=False, devices=["/dev/input/missing"])
    app = Application(config, connect_timeout=1.0)
    with pytest.raises(RuntimeError):
        app.start()


def test_broker_rejects_connect_raises_connection_error(fake_evdev, fake_mqtt) -> None:
    fake_evdev.add(
        "/dev/input/event0", name="Kbd A", capabilities=keyboard_capabilities()
    )
    fake_mqtt.connect_should_fail = True
    config = make_config()
    app = Application(config, connect_timeout=0.2)
    with pytest.raises(ConnectionError):
        app.start()


def test_denied_device_skipped_during_auto_discovery(fake_evdev, fake_mqtt) -> None:
    fake_evdev.add(
        "/dev/input/event0", name="Kbd A", capabilities=keyboard_capabilities()
    )
    fake_evdev.deny("/dev/input/event1")
    config = make_config()
    app = Application(config, connect_timeout=1.0)
    app.start()
    try:
        client = fake_mqtt.last()
        slug_a = slugify("Kbd A")
        assert wait_for(lambda: published(client, sensor_config_topic(slug_a)))
        config_topics = {
            r.topic
            for r in client.published
            if r.topic.startswith(BASE_TOPIC) and r.topic.endswith("/config")
        }
        assert config_topics == {sensor_config_topic(slug_a)}
    finally:
        app.stop()


def test_broker_never_connects_raises_connection_error(fake_evdev, fake_mqtt) -> None:
    fake_evdev.add(
        "/dev/input/event0", name="Kbd A", capabilities=keyboard_capabilities()
    )
    fake_mqtt.auto_connect = False
    config = make_config()
    app = Application(config, connect_timeout=0.1)
    with pytest.raises(ConnectionError):
        app.start()


def test_reconnect_after_drop_resubscribes_and_commands_still_work(
    fake_evdev, fake_mqtt
) -> None:
    device = fake_evdev.add(
        "/dev/input/event0", name="Kbd A", capabilities=keyboard_capabilities()
    )
    config = make_config()
    app = Application(config, connect_timeout=1.0)
    app.start()
    try:
        client = fake_mqtt.last()
        slug = slugify("Kbd A")
        cmd_topic = switch_command_topic(slug)
        assert wait_for(lambda: cmd_topic in client.subscriptions)
        subscribe_count_before = client.subscriptions.count(cmd_topic)

        client.drop()
        assert wait_for(lambda: not client.connected)
        client.reconnect()
        assert wait_for(
            lambda: client.subscriptions.count(cmd_topic) > subscribe_count_before
        )

        client.inject(cmd_topic, "OFF")
        assert wait_for(
            lambda: published(client, switch_state_topic(slug))[-1].payload == "OFF"
        )

        device.push(press("KEY_A"))
        grew = wait_for(
            lambda: len(published(client, state_topic(slug))) > 0, timeout=0.15
        )
        assert not grew
    finally:
        app.stop()


def test_modifier_state_is_not_shared_across_devices(fake_evdev, fake_mqtt) -> None:
    device_a = fake_evdev.add(
        "/dev/input/event0", name="Kbd A", capabilities=keyboard_capabilities()
    )
    device_b = fake_evdev.add(
        "/dev/input/event1", name="Kbd B", capabilities=keyboard_capabilities()
    )
    config = make_config()
    app = Application(config, connect_timeout=1.0)
    app.start()
    try:
        client = fake_mqtt.last()
        slug_a = slugify("Kbd A")
        slug_b = slugify("Kbd B")

        # Press shift on device A, then a non-modifier key on the same device
        # so its publish proves (via the happens-before edge on KeyHandler's
        # lock) that the shared handler has registered shift as active.
        device_a.push(press("KEY_LEFTSHIFT"))
        device_a.push(press("KEY_Z"))
        assert wait_for(lambda: published(client, state_topic(slug_a)))
        payload_a = json.loads(published(client, state_topic(slug_a))[-1].payload)
        assert payload_a["key"] == "KEY_Z_KEY_LEFTSHIFT"

        device_b.push(press("KEY_A"))
        assert wait_for(lambda: published(client, state_topic(slug_b)))
        payload_b = json.loads(published(client, state_topic(slug_b))[-1].payload)
        assert payload_b["key"] == "KEY_A"
    finally:
        app.stop()
