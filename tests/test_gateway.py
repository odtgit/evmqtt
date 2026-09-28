"""Scenario tests for the 2.0 MQTT contract: fake evdev, fake sysfs, fake broker."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from evdev import ecodes

import evmqtt
from evmqtt.config import Config
from evmqtt.core import make_device_id
from evmqtt.gateway import Gateway
from tests.fakes import (
    drained,
    hold,
    keyboard_capabilities,
    mouse_capabilities,
    press,
    published,
    release,
    until,
)

BASE = "evmqtt/test"
PREFIX = "homeassistant"
PHYS = "usb-0000:00:14.0-1/input0"


def make_config(tmp_path: Path, **overrides: object) -> Config:
    data: dict[str, object] = {
        "mqtt_host": "broker.local",
        "name": "Gateway",
        "base_topic": BASE,
        "state_file": str(tmp_path / "state.json"),
        "rescan_interval": 0,
    }
    data.update(overrides)
    return Config.from_dict(data)


def kbd_id(name: str = "Kbd A", phys: str = PHYS, vendor: int = 0x046D) -> str:
    return make_device_id(name, phys, "", 0x03, vendor, 0xC52B)


def add_kbd(fake_evdev, path: str = "/dev/input/event0", name: str = "Kbd A", **kw):
    attrs = {"phys": PHYS, "vendor": 0x046D, "product": 0xC52B}
    attrs.update(kw)
    return fake_evdev.add(
        path, name=name, capabilities=keyboard_capabilities(), **attrs
    )


async def start(config: Config) -> Gateway:
    gateway = Gateway(config, cleanup_window=0.01)
    await gateway.start()
    assert await until(
        lambda: (
            gateway.mqtt is not None
            and any(r.topic == f"{BASE}/status" for r in gateway.mqtt.client.published)
        )
    )
    return gateway


def last(client, topic: str):
    records = published(client, topic)
    return records[-1] if records else None


def payloads(client, topic: str) -> list[dict]:
    return [json.loads(r.payload) for r in published(client, topic)]


def topic(device_id: str, leaf: str) -> str:
    return f"{BASE}/{device_id}/{leaf}"


def discovery_topic(device_id: str) -> str:
    return f"{PREFIX}/device/evmqtt_test_{device_id}/config"


# -- discovery ---------------------------------------------------------------


async def test_discovery_payloads_full_structure(
    fake_evdev, fake_mqtt, sysfs, tmp_path
) -> None:
    add_kbd(fake_evdev)
    sysfs.add("event0", manufacturer="Logitech", product="USB Receiver")
    gateway = await start(make_config(tmp_path, keystates=["PRESS", "RELEASE"]))
    try:
        client = fake_mqtt.last()
        dev_id = kbd_id()
        uid = f"evmqtt_test_{dev_id}"
        assert await until(lambda: last(client, discovery_topic(dev_id)))
        origin = {
            "name": "evmqtt",
            "sw_version": evmqtt.__version__,
            "support_url": "https://github.com/odtgit/evmqtt",
        }

        gw_record = last(client, f"{PREFIX}/device/evmqtt_test/config")
        assert (gw_record.retain, gw_record.qos) == (True, 1)
        assert json.loads(gw_record.payload) == {
            "device": {
                "identifiers": ["evmqtt_test"],
                "name": "Gateway",
                "manufacturer": "evmqtt",
                "model": "evmqtt gateway",
                "sw_version": evmqtt.__version__,
            },
            "origin": origin,
            "components": {
                "status": {
                    "platform": "binary_sensor",
                    "unique_id": "evmqtt_test_status",
                    "name": "Status",
                    "device_class": "connectivity",
                    "entity_category": "diagnostic",
                    "state_topic": f"{BASE}/status",
                    "payload_on": "online",
                    "payload_off": "offline",
                }
            },
        }

        record = last(client, discovery_topic(dev_id))
        assert (record.retain, record.qos) == (True, 1)
        assert json.loads(record.payload) == {
            "device": {
                "identifiers": [uid],
                "name": "Kbd A",
                "manufacturer": "Logitech",
                "model": "USB Receiver",
                "model_id": "046d:c52b",
                "via_device": "evmqtt_test",
            },
            "origin": origin,
            "availability": [
                {
                    "topic": f"{BASE}/status",
                    "payload_available": "online",
                    "payload_not_available": "offline",
                },
                {
                    "topic": topic(dev_id, "availability"),
                    "payload_available": "online",
                    "payload_not_available": "offline",
                },
            ],
            "availability_mode": "all",
            "components": {
                "event": {
                    "platform": "event",
                    "unique_id": f"{uid}_event",
                    "name": "Key",
                    "icon": "mdi:keyboard",
                    "device_class": "button",
                    "state_topic": topic(dev_id, "event"),
                    "event_types": ["press", "release"],
                },
                "switch": {
                    "platform": "switch",
                    "unique_id": f"{uid}_switch",
                    "name": "Enabled",
                    "icon": "mdi:keyboard-settings",
                    "entity_category": "config",
                    "state_topic": topic(dev_id, "switch/state"),
                    "command_topic": topic(dev_id, "switch/set"),
                    "payload_on": "ON",
                    "payload_off": "OFF",
                    "state_on": "ON",
                    "state_off": "OFF",
                },
            },
        }
        for leaf, value in (("availability", "online"), ("switch/state", "ON")):
            state = last(client, topic(dev_id, leaf))
            assert (state.payload, state.retain) == (value, True)
        status = last(client, f"{BASE}/status")
        assert (status.payload, status.retain) == ("online", True)
        assert client.will.topic == f"{BASE}/status"
        assert (client.will.payload, client.will.retain) == ("offline", True)
        assert f"{BASE}/+/switch/set" in client.subscriptions
    finally:
        await gateway.stop()


async def test_device_block_without_sysfs_or_ids_is_minimal(
    fake_evdev, fake_mqtt, tmp_path
) -> None:
    add_kbd(
        fake_evdev, name="gpio_ir_recv", phys="gpio_ir_recv/input0", vendor=0, product=0
    )
    gateway = await start(make_config(tmp_path))
    try:
        client = fake_mqtt.last()
        dev_id = make_device_id("gpio_ir_recv", "gpio_ir_recv/input0", "", 3, 0, 0)
        assert await until(lambda: last(client, discovery_topic(dev_id)))
        device = json.loads(last(client, discovery_topic(dev_id)).payload)["device"]
        assert device == {
            "identifiers": [f"evmqtt_test_{dev_id}"],
            "name": "gpio_ir_recv",
            "via_device": "evmqtt_test",
        }
    finally:
        await gateway.stop()


async def test_ha_birth_republishes_discovery(fake_evdev, fake_mqtt, tmp_path) -> None:
    add_kbd(fake_evdev)
    gateway = await start(make_config(tmp_path))
    try:
        client = fake_mqtt.last()
        dev_topic = discovery_topic(kbd_id())
        assert await until(lambda: published(client, dev_topic))
        before = len(published(client, dev_topic))
        client.inject(f"{PREFIX}/status", "offline")
        client.inject(f"{PREFIX}/status", "online")
        assert await until(lambda: len(published(client, dev_topic)) == before + 1)
    finally:
        await gateway.stop()


# -- events --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("keystates", "expected"),
    [
        (None, ["press"]),
        (["RELEASE"], ["release"]),
        (["PRESS", "REPEAT", "RELEASE"], ["press", "repeat", "release"]),
    ],
)
async def test_event_payload_per_keystate(
    fake_evdev, fake_mqtt, tmp_path, keystates, expected
) -> None:
    device = add_kbd(fake_evdev)
    overrides = {} if keystates is None else {"keystates": keystates}
    gateway = await start(make_config(tmp_path, **overrides))
    try:
        client = fake_mqtt.last()
        dev_id = kbd_id()
        assert await until(lambda: last(client, discovery_topic(dev_id)))
        discovery = json.loads(last(client, discovery_topic(dev_id)).payload)
        assert discovery["components"]["event"]["event_types"] == expected

        device.push_all([press("KEY_A"), hold("KEY_A"), release("KEY_A")])
        await drained(device)
        events = published(client, topic(dev_id, "event"))
        assert all(not r.retain for r in events)
        assert [json.loads(r.payload) for r in events] == [
            {
                "event_type": t,
                "key": "KEY_A",
                "modifiers": [],
                "state": t.upper(),
                "deviceId": dev_id,
                "deviceName": "Kbd A",
                "devicePath": "/dev/input/event0",
            }
            for t in expected
        ]
    finally:
        await gateway.stop()


async def test_event_modifiers_aliases_and_ignored_keys(
    fake_evdev, fake_mqtt, tmp_path
) -> None:
    device = add_kbd(fake_evdev)
    gateway = await start(make_config(tmp_path))
    try:
        client = fake_mqtt.last()
        device.push_all(
            [
                press("KEY_LEFTSHIFT"),
                press("KEY_LEFTCTRL"),
                hold("KEY_LEFTSHIFT"),
                press("KEY_A"),
                release("KEY_LEFTCTRL"),
                release("KEY_LEFTSHIFT"),
                press("KEY_NUMLOCK"),
                press("KEY_MUTE"),
            ]
        )
        await drained(device)
        events = payloads(client, topic(kbd_id(), "event"))
        assert [(e["key"], e["modifiers"]) for e in events] == [
            ("KEY_A", ["KEY_LEFTCTRL", "KEY_LEFTSHIFT"]),
            ("KEY_MUTE", []),
        ]
    finally:
        await gateway.stop()


# -- switch, grab, persistence --------------------------------------------------


async def test_switch_off_ungrabs_and_on_grabs(fake_evdev, fake_mqtt, tmp_path) -> None:
    device = add_kbd(fake_evdev)
    config = make_config(tmp_path, devices=["Kbd A"])
    gateway = await start(config)
    try:
        client = fake_mqtt.last()
        dev_id = kbd_id()
        assert device.grabbed

        client.inject(topic(dev_id, "switch/set"), "OFF")
        assert await until(
            lambda: last(client, topic(dev_id, "switch/state")).payload == "OFF"
        )
        assert not device.grabbed
        device.push(press("KEY_A"))
        await drained(device)
        assert published(client, topic(dev_id, "event")) == []
        state = json.loads(config.state_path.read_text())
        assert state["devices"][dev_id]["enabled"] is False

        client.inject(topic(dev_id, "switch/set"), "bogus")
        client.inject(f"{BASE}/nope/switch/set", "ON")
        assert last(client, topic(dev_id, "switch/state")).payload == "OFF"

        client.inject(topic(dev_id, "switch/set"), "on")
        assert await until(
            lambda: last(client, topic(dev_id, "switch/state")).payload == "ON"
        )
        assert device.grabbed
        device.push(press("KEY_B"))
        assert await until(lambda: published(client, topic(dev_id, "event")))
        assert json.loads(config.state_path.read_text())["devices"][dev_id]["enabled"]
    finally:
        await gateway.stop()
    assert not device.grabbed
    assert device.closed


async def test_switch_on_with_grab_failure_stays_off(
    fake_evdev, fake_mqtt, tmp_path
) -> None:
    device = add_kbd(fake_evdev)
    gateway = await start(
        make_config(tmp_path, devices=["Kbd A"], enabled_devices=["nothing"])
    )
    try:
        client = fake_mqtt.last()
        dev_id = kbd_id()
        assert not device.grabbed
        device.fail_grab()
        client.inject(topic(dev_id, "switch/set"), "ON")
        assert await until(
            lambda: len(published(client, topic(dev_id, "switch/state"))) >= 2
        )
        assert last(client, topic(dev_id, "switch/state")).payload == "OFF"
        assert gateway.store.enabled(dev_id) is False
    finally:
        await gateway.stop()


@pytest.mark.parametrize("selector", ["id", "/dev/input/event1", "Kbd B"])
async def test_enabled_devices_matches_id_path_or_name(
    fake_evdev, fake_mqtt, tmp_path, selector
) -> None:
    a = add_kbd(fake_evdev, "/dev/input/event0", "Kbd A")
    b = add_kbd(fake_evdev, "/dev/input/event1", "Kbd B")
    if selector == "id":
        selector = kbd_id("Kbd B")
    gateway = await start(make_config(tmp_path, enabled_devices=[selector]))
    try:
        client = fake_mqtt.last()
        assert (a.grabbed, b.grabbed) == (False, True)
        assert last(client, topic(kbd_id("Kbd A"), "switch/state")).payload == "OFF"
        assert last(client, topic(kbd_id("Kbd B"), "switch/state")).payload == "ON"
        a.push(press("KEY_A"))
        await drained(a)
        assert published(client, topic(kbd_id("Kbd A"), "event")) == []
    finally:
        await gateway.stop()


async def test_enable_state_persists_across_restart(
    fake_evdev, fake_mqtt, tmp_path
) -> None:
    device = add_kbd(fake_evdev)
    config = make_config(tmp_path)
    dev_id = kbd_id()
    gateway = await start(config)
    try:
        fake_mqtt.last().inject(topic(dev_id, "switch/set"), "OFF")
        assert await until(lambda: not device.grabbed)
    finally:
        await gateway.stop()

    gateway = await start(config)
    try:
        client = fake_mqtt.last()
        assert not device.grabbed
        assert last(client, topic(dev_id, "switch/state")).payload == "OFF"
        assert fake_mqtt.retained[topic(dev_id, "switch/state")] == "OFF"
    finally:
        await gateway.stop()

    gateway = await start(make_config(tmp_path, enabled_devices=[dev_id]))
    try:
        assert not device.grabbed, "stored state wins over enabled_devices"
    finally:
        await gateway.stop()


async def test_unwritable_state_file_is_not_fatal(
    fake_evdev, fake_mqtt, tmp_path
) -> None:
    add_kbd(fake_evdev)
    blocker = tmp_path / "file"
    blocker.write_text("")
    gateway = await start(make_config(tmp_path, state_file=str(blocker / "state.json")))
    try:
        client = fake_mqtt.last()
        client.inject(topic(kbd_id(), "switch/set"), "OFF")
        assert await until(
            lambda: last(client, topic(kbd_id(), "switch/state")).payload == "OFF"
        )
        assert gateway.fatal_error is None
    finally:
        await gateway.stop()


async def test_corrupt_state_file_is_ignored(fake_evdev, fake_mqtt, tmp_path) -> None:
    device = add_kbd(fake_evdev)
    config = make_config(tmp_path, devices=["Kbd A"])
    config.state_path.write_text("{broken")
    gateway = await start(config)
    try:
        assert device.grabbed
    finally:
        await gateway.stop()


# -- availability and broker lifecycle --------------------------------------------


async def test_availability_lwt_and_reconnect(fake_evdev, fake_mqtt, tmp_path) -> None:
    add_kbd(fake_evdev)
    gateway = await start(make_config(tmp_path))
    client = fake_mqtt.last()
    dev_id = kbd_id()
    try:
        assert fake_mqtt.retained[f"{BASE}/status"] == "online"
        client.drop()
        assert fake_mqtt.retained[f"{BASE}/status"] == "offline"
        assert await until(lambda: not gateway.mqtt.is_connected)

        count = len(published(client, discovery_topic(dev_id)))
        client.reconnect()
        assert await until(lambda: fake_mqtt.retained[f"{BASE}/status"] == "online")
        assert await until(
            lambda: len(published(client, discovery_topic(dev_id))) > count
        )
        assert fake_mqtt.retained[topic(dev_id, "availability")] == "online"
        assert client.subscriptions.count(f"{BASE}/+/switch/set") == 2
    finally:
        await gateway.stop()
    assert fake_mqtt.retained[f"{BASE}/status"] == "offline"
    assert not client.loop_running


async def test_broker_down_at_startup_then_up(
    fake_evdev, fake_mqtt, tmp_path, caplog
) -> None:
    device = add_kbd(fake_evdev)
    fake_mqtt.auto_connect = False
    gateway = Gateway(make_config(tmp_path, devices=["Kbd A"]), cleanup_window=0.01)
    await gateway.start()
    try:
        assert await until(lambda: gateway.mqtt is not None)
        client = fake_mqtt.last()
        assert device.grabbed
        client.fail_connect()
        client.fail_connect()
        assert "unreachable" in caplog.text
        device.push(press("KEY_A"))
        await drained(device)
        assert client.published == []
        assert not gateway._wake.is_set()

        client.fire_connect(success=False)
        assert "refused" in caplog.text
        assert client.published == []

        client.fire_connect(success=True)
        assert await until(lambda: published(client, discovery_topic(kbd_id())))
        device.push(press("KEY_B"))
        assert await until(lambda: published(client, topic(kbd_id(), "event")))
        assert [e["key"] for e in payloads(client, topic(kbd_id(), "event"))] == [
            "KEY_B"
        ]
    finally:
        await gateway.stop()


async def test_missing_tls_ca_is_fatal(fake_evdev, fake_mqtt, tmp_path) -> None:
    gateway = Gateway(make_config(tmp_path, mqtt_tls_ca=str(tmp_path / "nope.pem")))
    await gateway.start()
    try:
        await gateway.wait()
        assert isinstance(gateway.fatal_error, FileNotFoundError)
    finally:
        await gateway.stop()


# -- device selection and hotplug --------------------------------------------------


async def test_default_selection_excludes_virtual_mice_and_power_buttons(
    fake_evdev, fake_mqtt, sysfs, tmp_path
) -> None:
    add_kbd(fake_evdev, "/dev/input/event0", "Kbd A")
    fake_evdev.add(
        "/dev/input/event1",
        name="keyd virtual keyboard",
        capabilities=keyboard_capabilities(),
        vendor=0x0FAC,
        product=0x0ADE,
    )
    sysfs.add("event1", virtual=True)
    fake_evdev.add(
        "/dev/input/event2",
        name="ydotoold",
        capabilities=keyboard_capabilities(),
        bustype=0x06,
    )
    fake_evdev.add("/dev/input/event3", name="Mouse", capabilities=mouse_capabilities())
    fake_evdev.add(
        "/dev/input/event4",
        name="Power Button",
        capabilities={ecodes.EV_KEY: [ecodes.KEY_POWER]},
    )
    gateway = await start(make_config(tmp_path))
    try:
        assert [d.info.name for d in gateway.devices.values()] == ["Kbd A"]
    finally:
        await gateway.stop()

    gateway = await start(
        make_config(tmp_path, devices=["keyd virtual keyboard", "/dev/input/event3"])
    )
    try:
        names = sorted(d.info.name for d in gateway.devices.values())
        assert names == ["Kbd A", "Mouse", "keyd virtual keyboard"]
    finally:
        await gateway.stop()


async def test_auto_discovered_devices_are_read_but_not_grabbed(
    fake_evdev, fake_mqtt, tmp_path
) -> None:
    """#20: a discovered device may be the host's keyboard; only listed ones
    are grabbed."""
    a = add_kbd(fake_evdev, "/dev/input/event0", "Kbd A")
    b = add_kbd(fake_evdev, "/dev/input/event1", "Kbd B")
    gateway = await start(make_config(tmp_path, devices=["Kbd B"]))
    try:
        client = fake_mqtt.last()
        dev_id = kbd_id()
        assert (a.grabbed, b.grabbed) == (False, True)
        assert last(client, topic(dev_id, "switch/state")).payload == "ON"
        a.push(press("KEY_A"))
        assert await until(lambda: published(client, topic(dev_id, "event")))

        client.inject(topic(dev_id, "switch/set"), "OFF")
        assert await until(
            lambda: last(client, topic(dev_id, "switch/state")).payload == "OFF"
        )
        client.inject(topic(dev_id, "switch/set"), "ON")
        assert await until(
            lambda: last(client, topic(dev_id, "switch/state")).payload == "ON"
        )
        assert a.grab_calls == 0
    finally:
        await gateway.stop()


async def test_default_selection_keeps_bluetooth_le_devices_behind_uhid(
    fake_evdev, fake_mqtt, sysfs, tmp_path
) -> None:
    """#20: BlueZ HoG remotes live under /sys/devices/virtual/misc/uhid."""
    fake_evdev.add(
        "/dev/input/event23",
        name="BT Remote",
        capabilities=keyboard_capabilities(),
        bustype=0x05,
    )
    sysfs.add("event23", uhid=True)
    gateway = await start(make_config(tmp_path))
    try:
        assert [d.info.name for d in gateway.devices.values()] == ["BT Remote"]
    finally:
        await gateway.stop()


@pytest.mark.parametrize("option", ["devices", "enabled_devices"])
async def test_selectors_follow_symlinks(
    fake_evdev, fake_mqtt, tmp_path, option
) -> None:
    """#20: a udev symlink such as /dev/input/rc selects its eventN."""
    node = tmp_path / "input" / "event23"
    node.parent.mkdir()
    node.touch()
    (node.parent / "rc").symlink_to(node.name)
    real = os.path.realpath(node)
    a = add_kbd(fake_evdev, "/dev/input/event0", "Kbd A")
    rc = add_kbd(fake_evdev, real, "RC")
    overrides: dict[str, object] = {option: [str(node.parent / "rc")]}
    if option == "devices":
        overrides["auto_discover"] = False
    gateway = await start(make_config(tmp_path, **overrides))
    try:
        assert rc.grabbed
        assert not a.grabbed
    finally:
        await gateway.stop()


async def test_auto_discover_off_uses_only_listed_devices(
    fake_evdev, fake_mqtt, tmp_path
) -> None:
    add_kbd(fake_evdev, "/dev/input/event0", "Kbd A")
    add_kbd(fake_evdev, "/dev/input/event1", "Kbd B")
    gateway = await start(
        make_config(tmp_path, auto_discover=False, devices=["/dev/input/event1"])
    )
    try:
        assert [d.info.name for d in gateway.devices.values()] == ["Kbd B"]
    finally:
        await gateway.stop()


async def test_hotplug_add_unplug_replug(fake_evdev, fake_mqtt, tmp_path) -> None:
    gateway = await start(make_config(tmp_path, devices=["Kbd A"]))
    client = fake_mqtt.last()
    dev_id = kbd_id()
    try:
        assert gateway.devices == {}
        device = add_kbd(fake_evdev)
        await gateway.rescan()
        assert device.grabbed
        assert await until(lambda: last(client, discovery_topic(dev_id)))
        assert fake_mqtt.retained[topic(dev_id, "availability")] == "online"

        device.unplug()
        assert await until(
            lambda: fake_mqtt.retained[topic(dev_id, "availability")] == "offline"
        )
        fake_evdev.remove("/dev/input/event0")
        await gateway.rescan()
        assert dev_id in gateway.devices
        assert fake_mqtt.retained[discovery_topic(dev_id)]
        assert all(r.payload for r in published(client, discovery_topic(dev_id)))

        device = add_kbd(fake_evdev, "/dev/input/event7")
        await gateway.rescan()
        assert device.grabbed
        assert fake_mqtt.retained[topic(dev_id, "availability")] == "online"
        device.push(press("KEY_A"))
        assert await until(lambda: published(client, topic(dev_id, "event")))
        assert payloads(client, topic(dev_id, "event"))[-1]["devicePath"] == (
            "/dev/input/event7"
        )
    finally:
        await gateway.stop()


async def test_removed_without_read_error_goes_offline(
    fake_evdev, fake_mqtt, tmp_path
) -> None:
    device = add_kbd(fake_evdev)
    gateway = await start(make_config(tmp_path))
    try:
        fake_evdev.remove("/dev/input/event0")
        await gateway.rescan()
        assert device.closed
        assert fake_mqtt.retained[topic(kbd_id(), "availability")] == "offline"
    finally:
        await gateway.stop()


async def test_grab_failure_retries_on_rescan(fake_evdev, fake_mqtt, tmp_path) -> None:
    device = add_kbd(fake_evdev)
    device.fail_grab()
    gateway = await start(make_config(tmp_path, devices=["Kbd A"]))
    try:
        assert await until(
            lambda: last(client_of(fake_mqtt), discovery_topic(kbd_id()))
        )
        assert fake_mqtt.retained[topic(kbd_id(), "availability")] == "offline"
        await gateway.rescan()
        assert not device.grabbed
        device.grab_error = None
        await gateway.rescan()
        assert device.grabbed
        assert fake_mqtt.retained[topic(kbd_id(), "availability")] == "online"
    finally:
        await gateway.stop()


def client_of(fake_mqtt):
    return fake_mqtt.last()


async def test_devices_seen_before_but_absent_are_offline(
    fake_evdev, fake_mqtt, tmp_path
) -> None:
    add_kbd(fake_evdev)
    config = make_config(tmp_path)
    gateway = await start(config)
    await gateway.stop()
    fake_evdev.remove("/dev/input/event0")
    fake_mqtt.retained[topic(kbd_id(), "availability")] = "online"
    gateway = await start(config)
    try:
        assert fake_mqtt.retained[topic(kbd_id(), "availability")] == "offline"
    finally:
        await gateway.stop()


async def test_rescan_loop_picks_up_new_devices(
    fake_evdev, fake_mqtt, tmp_path
) -> None:
    gateway = await start(
        make_config(tmp_path, devices=["Kbd A"], rescan_interval=0.01)
    )
    try:
        device = add_kbd(fake_evdev)
        assert await until(lambda: device.grabbed)
    finally:
        await gateway.stop()


# -- 1.x cleanup ---------------------------------------------------------------------

LEGACY = "homeassistant/sensor/evmqtt"


def legacy_retained() -> dict[str, str]:
    sensor = {
        "name": "Input Events - Kbd A",
        "state_topic": f"{LEGACY}/kbd-a/state",
        "unique_id": "evmqtt_kbd-a_event0_sensor",
    }
    switch = {
        "name": "Kbd A Enable",
        "state_topic": f"{LEGACY}/kbd-a/switch/state",
        "command_topic": f"{LEGACY}/kbd-a/switch/set",
        "unique_id": "evmqtt_kbd-a_event0_switch",
    }
    manual = {
        "state_topic": f"{LEGACY}/_dev_input_event3/state",
        "unique_id": "evmqtt__dev_input_event3_sensor",
    }
    return {
        f"{LEGACY}/kbd-a/config": json.dumps(sensor),
        "homeassistant/switch/evmqtt_kbd-a_event0/config": json.dumps(switch),
        f"{LEGACY}/kbd-a/switch/state": "ON",
        f"{LEGACY}/_dev_input_event3/config": json.dumps(manual),
    }


def foreign_retained() -> dict[str, str]:
    return {
        "homeassistant/switch/zigbee_plug/config": json.dumps(
            {"unique_id": "0x1234_switch", "state_topic": "zigbee2mqtt/plug"}
        ),
        "homeassistant/sensor/evmqtt_lookalike/config": json.dumps(
            {"unique_id": "evmqtt_other", "state_topic": "someone/else/state"}
        ),
        "homeassistant/sensor/no_uid/config": json.dumps(
            {"state_topic": f"{LEGACY}/x/state"}
        ),
        "homeassistant/sensor/garbage/config": "not json",
        "homeassistant/sensor/list/config": "[1, 2]",
        "homeassistant/device/evmqtt_other/config": json.dumps(
            {"unique_id": "evmqtt_x", "state_topic": f"{LEGACY}/x/state"}
        ),
        "homeassistant/binary_sensor/node/obj/config": json.dumps(
            {"unique_id": "evmqtt_deep", "state_topic": f"{LEGACY}/deep/state"}
        ),
    }


async def test_legacy_cleanup_clears_only_1x_evmqtt_configs(
    fake_evdev, fake_mqtt, tmp_path
) -> None:
    add_kbd(fake_evdev)
    fake_mqtt.retained.update(legacy_retained())
    fake_mqtt.retained.update(foreign_retained())
    gateway = await start(make_config(tmp_path))
    try:
        client = fake_mqtt.last()
        assert await until(lambda: f"{LEGACY}/kbd-a/config" not in fake_mqtt.retained)
        for key in legacy_retained():
            assert key not in fake_mqtt.retained
        for key, value in foreign_retained().items():
            assert fake_mqtt.retained[key] == value
        cleared = {r.topic for r in client.published if r.payload == ""}
        assert cleared == set(legacy_retained())
        assert f"{PREFIX}/+/+/config" not in client.subscriptions
        assert gateway.fatal_error is None
    finally:
        await gateway.stop()


async def test_legacy_cleanup_uses_configured_1x_topic(
    fake_evdev, fake_mqtt, tmp_path
) -> None:
    old = {
        "state_topic": "home/remote/kbd-a/state",
        "unique_id": "evmqtt_kbd-a_event0_sensor",
    }
    fake_mqtt.retained["home/remote/kbd-a/config"] = json.dumps(old)
    fake_mqtt.retained.update(legacy_retained())
    gateway = await start(make_config(tmp_path, topic="home/remote", base_topic=BASE))
    try:
        assert await until(lambda: "home/remote/kbd-a/config" not in fake_mqtt.retained)
        assert f"{LEGACY}/kbd-a/config" in fake_mqtt.retained
    finally:
        await gateway.stop()


async def test_legacy_cleanup_can_be_disabled(fake_evdev, fake_mqtt, tmp_path) -> None:
    fake_mqtt.retained.update(legacy_retained())
    gateway = await start(make_config(tmp_path, cleanup_legacy=False))
    try:
        client = fake_mqtt.last()
        assert await until(lambda: published(client, f"{BASE}/status"))
        assert all(r.payload != "" for r in client.published)
        assert f"{PREFIX}/+/+/config" not in client.subscriptions
        for key, value in legacy_retained().items():
            assert fake_mqtt.retained[key] == value
    finally:
        await gateway.stop()
