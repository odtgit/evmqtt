"""Optional MQTT mirror."""

from __future__ import annotations

import json

import pytest
from homeassistant.core import HomeAssistant

from tests.fakes import FakeEvdevRegistry, keyboard_capabilities, press, tap
from tests_ha.common import BASE_TOPIC, device_id, settle, setup_entry

REMOTE = "/dev/input/event0"


@pytest.mark.parametrize("expected_lingering_timers", [True])
async def test_mirror_publishes_daemon_payload(
    hass: HomeAssistant, fake_evdev: FakeEvdevRegistry, mqtt_mock
) -> None:
    remote = fake_evdev.add(REMOTE, "Remote", keyboard_capabilities())
    rid = device_id(remote)
    await setup_entry(hass, enabled_devices=[rid], mqtt_mirror=True)
    remote.push_all([press("KEY_LEFTSHIFT"), *tap("KEY_A")])
    await settle(hass, remote)
    mqtt_mock.async_publish.assert_called_once()
    topic, payload, qos, retain = mqtt_mock.async_publish.call_args.args
    assert topic == f"{BASE_TOPIC}/{rid}/event"
    assert (qos, retain) == (0, False)
    assert json.loads(payload) == {
        "event_type": "press",
        "key": "KEY_A",
        "modifiers": ["KEY_LEFTSHIFT"],
        "state": "PRESS",
        "deviceId": rid,
        "deviceName": "Remote",
        "devicePath": REMOTE,
    }
    assert "," in payload and ", " not in payload


@pytest.mark.parametrize("expected_lingering_timers", [True])
async def test_mirror_off_publishes_nothing(
    hass: HomeAssistant, fake_evdev: FakeEvdevRegistry, mqtt_mock
) -> None:
    remote = fake_evdev.add(REMOTE, "Remote", keyboard_capabilities())
    await setup_entry(hass, enabled_devices=[device_id(remote)])
    remote.push_all(tap("KEY_A"))
    await settle(hass, remote)
    mqtt_mock.async_publish.assert_not_called()
    assert hass.states.get("event.remote_key").attributes["key"] == "KEY_A"


async def test_mirror_without_mqtt_warns_once(
    hass: HomeAssistant,
    fake_evdev: FakeEvdevRegistry,
    caplog: pytest.LogCaptureFixture,
) -> None:
    remote = fake_evdev.add(REMOTE, "Remote", keyboard_capabilities())
    await setup_entry(hass, enabled_devices=[device_id(remote)], mqtt_mirror=True)
    remote.push_all([*tap("KEY_A"), *tap("KEY_B")])
    await settle(hass, remote)
    assert hass.states.get("event.remote_key").attributes["key"] == "KEY_B"
    assert caplog.text.count("MQTT mirror publish") == 1
