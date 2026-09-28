"""Entities, events, switch, persistence, hotplug, EBUSY and unload."""

from __future__ import annotations

import errno
import logging
from datetime import timedelta

import pytest
from homeassistant import block_async_io
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.evmqtt import async_remove_config_entry_device
from custom_components.evmqtt.const import CONF_ENABLED_DEVICES, DOMAIN
from tests.fakes import (
    FakeEvdevRegistry,
    FakeSysfs,
    hold,
    keyboard_capabilities,
    press,
    release,
    tap,
)
from tests_ha.common import device_id, settle, setup_entry

REMOTE = "/dev/input/event0"


async def rescan(hass: HomeAssistant, entry) -> None:
    await entry.runtime_data.async_rescan()
    await hass.async_block_till_done()


async def switch(hass: HomeAssistant, entity_id: str, on: bool) -> None:
    await hass.services.async_call(
        "switch",
        "turn_on" if on else "turn_off",
        {"entity_id": entity_id},
        blocking=True,
    )


async def test_entities_and_device_registry(
    hass: HomeAssistant,
    fake_evdev: FakeEvdevRegistry,
    sysfs: FakeSysfs,
    device_registry: dr.DeviceRegistry,
    entity_registry: er.EntityRegistry,
) -> None:
    remote = fake_evdev.add(
        REMOTE, "IR Remote", keyboard_capabilities(), vendor=0x1234, product=0xABCD
    )
    sysfs.add("event0", manufacturer="Acme", product="Receiver 3000")
    entry = await setup_entry(hass, enabled_devices=[device_id(remote)])

    device = device_registry.async_get_device_by_identifier(
        (DOMAIN, device_id(remote)), entry.entry_id
    )
    assert device is not None
    assert device.name == "IR Remote"
    assert device.manufacturer == "Acme"
    assert device.model == "Receiver 3000"
    assert device.model_id == "1234:abcd"
    assert device.config_entries == {entry.entry_id}

    entities = er.async_entries_for_device(entity_registry, device.id)
    assert {e.entity_id for e in entities} == {
        "event.ir_remote_key",
        "switch.ir_remote_enabled",
    }
    event = entity_registry.async_get("event.ir_remote_key")
    assert event.unique_id == f"{device_id(remote)}_event"
    assert event.translation_key == "key"
    sw = entity_registry.async_get("switch.ir_remote_enabled")
    assert sw.entity_category is EntityCategory.CONFIG

    state = hass.states.get("event.ir_remote_key")
    assert state.state == STATE_UNKNOWN
    assert state.attributes["device_class"] == "button"
    assert state.attributes["event_types"] == ["press"]
    assert hass.states.get("switch.ir_remote_enabled").state == "on"
    assert remote.grabbed


async def test_key_press_fires_event(
    hass: HomeAssistant, fake_evdev: FakeEvdevRegistry
) -> None:
    remote = fake_evdev.add(REMOTE, "Remote", keyboard_capabilities())
    await setup_entry(
        hass, enabled_devices=[device_id(remote)], keystates=["press", "repeat"]
    )
    remote.push_all([press("KEY_LEFTSHIFT"), press("KEY_A")])
    await settle(hass, remote)
    state = hass.states.get("event.remote_key")
    assert state.attributes["event_type"] == "press"
    assert state.attributes["key"] == "KEY_A"
    assert state.attributes["modifiers"] == ["KEY_LEFTSHIFT"]
    assert state.attributes["state"] == "PRESS"
    assert state.attributes["device_id"] == device_id(remote)
    assert state.attributes["device_name"] == "Remote"
    assert state.attributes["device_path"] == REMOTE
    fired = state.state

    remote.push_all([release("KEY_A"), release("KEY_LEFTSHIFT")])
    await settle(hass, remote)
    assert hass.states.get("event.remote_key").state == fired

    remote.push(hold("KEY_B"))
    await settle(hass, remote)
    state = hass.states.get("event.remote_key")
    assert state.attributes["event_type"] == "repeat"
    assert state.attributes["key"] == "KEY_B"
    assert state.attributes["modifiers"] == []


async def test_switch_grabs_and_ungrabs(
    hass: HomeAssistant, fake_evdev: FakeEvdevRegistry
) -> None:
    remote = fake_evdev.add(REMOTE, "Remote", keyboard_capabilities())
    entry = await setup_entry(hass)
    assert not remote.grabbed
    assert hass.states.get("event.remote_key").state != STATE_UNAVAILABLE

    remote.push_all(tap("KEY_A"))
    await settle(hass, remote)
    assert hass.states.get("event.remote_key").state == STATE_UNKNOWN

    await switch(hass, "switch.remote_enabled", True)
    await hass.async_block_till_done()
    assert remote.grabbed
    assert hass.states.get("switch.remote_enabled").state == "on"
    assert entry.options[CONF_ENABLED_DEVICES] == [device_id(remote)]
    remote.push_all(tap("KEY_A"))
    await settle(hass, remote)
    assert hass.states.get("event.remote_key").attributes["key"] == "KEY_A"

    await switch(hass, "switch.remote_enabled", False)
    await hass.async_block_till_done()
    assert not remote.grabbed
    assert hass.states.get("switch.remote_enabled").state == "off"
    assert entry.options[CONF_ENABLED_DEVICES] == []


async def test_enabled_state_persists_across_reload(
    hass: HomeAssistant, fake_evdev: FakeEvdevRegistry
) -> None:
    remote = fake_evdev.add(REMOTE, "Remote", keyboard_capabilities())
    entry = await setup_entry(hass)
    await switch(hass, "switch.remote_enabled", True)
    await hass.async_block_till_done()

    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    assert remote.grabbed
    assert hass.states.get("switch.remote_enabled").state == "on"


async def test_unplug_and_replug(
    hass: HomeAssistant, fake_evdev: FakeEvdevRegistry
) -> None:
    remote = fake_evdev.add(REMOTE, "Remote", keyboard_capabilities())
    entry = await setup_entry(hass, enabled_devices=[device_id(remote)])
    remote.unplug()
    fake_evdev.remove(REMOTE)
    await settle(hass, remote)
    assert hass.states.get("event.remote_key").state == STATE_UNAVAILABLE
    assert hass.states.get("switch.remote_enabled").state == STATE_UNAVAILABLE

    await rescan(hass, entry)
    assert hass.states.get("event.remote_key").state == STATE_UNAVAILABLE

    back = fake_evdev.add(REMOTE, "Remote", keyboard_capabilities())
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=6))
    await hass.async_block_till_done(wait_background_tasks=True)
    assert hass.states.get("event.remote_key").state != STATE_UNAVAILABLE
    assert hass.states.get("switch.remote_enabled").state == "on"
    assert back.grabbed
    back.push(press("KEY_C"))
    await settle(hass, back)
    assert hass.states.get("event.remote_key").attributes["key"] == "KEY_C"


async def test_known_device_absent_at_start(
    hass: HomeAssistant, fake_evdev: FakeEvdevRegistry
) -> None:
    remote = fake_evdev.add(REMOTE, "Remote", keyboard_capabilities())
    entry = await setup_entry(hass, enabled_devices=[device_id(remote)])
    assert await hass.config_entries.async_unload(entry.entry_id)
    fake_evdev.remove(REMOTE)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get("event.remote_key").state == STATE_UNAVAILABLE
    assert hass.states.get("switch.remote_enabled").state == STATE_UNAVAILABLE
    assert entry.options[CONF_ENABLED_DEVICES] == [device_id(remote)]


async def test_hotplugged_device_starts_disabled(
    hass: HomeAssistant, fake_evdev: FakeEvdevRegistry
) -> None:
    fake_evdev.add(REMOTE, "Remote", keyboard_capabilities())
    entry = await setup_entry(hass)
    kbd = fake_evdev.add("/dev/input/event5", "USB Keyboard", keyboard_capabilities())
    await rescan(hass, entry)
    assert hass.states.get("switch.usb_keyboard_enabled").state == "off"
    assert hass.states.get("event.usb_keyboard_key").state == STATE_UNKNOWN
    assert not kbd.grabbed
    assert entry.options[CONF_ENABLED_DEVICES] == []


async def test_ebusy_marks_unavailable_and_logs_once(
    hass: HomeAssistant,
    fake_evdev: FakeEvdevRegistry,
    caplog: pytest.LogCaptureFixture,
) -> None:
    remote = fake_evdev.add(REMOTE, "Remote", keyboard_capabilities())
    remote.fail_grab(OSError(errno.EBUSY, "Device or resource busy"))
    entry = await setup_entry(hass, enabled_devices=[device_id(remote)])
    assert hass.states.get("event.remote_key").state == STATE_UNAVAILABLE
    assert hass.states.get("switch.remote_enabled").state == "on"
    await rescan(hass, entry)
    await rescan(hass, entry)
    busy = [r for r in caplog.records if "grabbed by another process" in r.message]
    assert len(busy) == 1
    assert busy[0].levelno == logging.WARNING

    remote.grab_error = None
    await rescan(hass, entry)
    assert remote.grabbed
    assert hass.states.get("event.remote_key").state != STATE_UNAVAILABLE


async def test_ebusy_switch_off_reads_without_grab(
    hass: HomeAssistant, fake_evdev: FakeEvdevRegistry
) -> None:
    remote = fake_evdev.add(REMOTE, "Remote", keyboard_capabilities())
    remote.fail_grab(OSError(errno.EBUSY, "Device or resource busy"))
    await setup_entry(hass, enabled_devices=[device_id(remote)])
    await switch(hass, "switch.remote_enabled", False)
    await hass.async_block_till_done()
    assert hass.states.get("event.remote_key").state != STATE_UNAVAILABLE
    assert not remote.grabbed


async def test_grab_failure_on_turn_on_raises(
    hass: HomeAssistant, fake_evdev: FakeEvdevRegistry
) -> None:
    from homeassistant.exceptions import HomeAssistantError

    remote = fake_evdev.add(REMOTE, "Remote", keyboard_capabilities())
    entry = await setup_entry(hass)
    remote.fail_grab(OSError(errno.EBUSY, "Device or resource busy"))
    with pytest.raises(HomeAssistantError, match="Could not grab Remote"):
        await switch(hass, "switch.remote_enabled", True)
    assert hass.states.get("switch.remote_enabled").state == "off"
    assert entry.options[CONF_ENABLED_DEVICES] == []


async def test_unload_releases_devices(
    hass: HomeAssistant, fake_evdev: FakeEvdevRegistry
) -> None:
    remote = fake_evdev.add(REMOTE, "Remote", keyboard_capabilities())
    other = fake_evdev.add("/dev/input/event1", "Other", keyboard_capabilities())
    entry = await setup_entry(hass, enabled_devices=[device_id(remote)])
    hub = entry.runtime_data
    assert remote.grabbed
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.NOT_LOADED
    assert not remote.grabbed
    assert remote.closed
    assert other.closed
    assert all(d.reader is None for d in hub.devices.values())
    assert hass.states.get("event.remote_key").state == STATE_UNAVAILABLE


async def test_remove_config_entry_device(
    hass: HomeAssistant,
    fake_evdev: FakeEvdevRegistry,
    device_registry: dr.DeviceRegistry,
) -> None:
    remote = fake_evdev.add(REMOTE, "Remote", keyboard_capabilities())
    gone = fake_evdev.add("/dev/input/event1", "Gone", keyboard_capabilities())
    entry = await setup_entry(
        hass, enabled_devices=[device_id(remote), device_id(gone)]
    )
    gone.unplug()
    fake_evdev.remove("/dev/input/event1")
    await settle(hass, gone)
    present = device_registry.async_get_device_by_identifier(
        (DOMAIN, device_id(remote)), entry.entry_id
    )
    absent = device_registry.async_get_device_by_identifier(
        (DOMAIN, device_id(gone)), entry.entry_id
    )
    assert not await async_remove_config_entry_device(hass, entry, present)
    assert await async_remove_config_entry_device(hass, entry, absent)
    await hass.async_block_till_done()
    assert entry.options[CONF_ENABLED_DEVICES] == [device_id(remote)]


async def test_no_blocking_calls_in_loop(
    hass: HomeAssistant,
    fake_evdev: FakeEvdevRegistry,
    sysfs: FakeSysfs,
    caplog: pytest.LogCaptureFixture,
    disable_block_async_io: None,
) -> None:
    if not block_async_io._BLOCKED_CALLS.calls:
        block_async_io.enable()
    remote = fake_evdev.add(REMOTE, "Remote", keyboard_capabilities())
    sysfs.add("event0", manufacturer="Acme", product="Remote")
    entry = await setup_entry(hass, enabled_devices=[device_id(remote)])
    fake_evdev.add("/dev/input/event1", "Other", keyboard_capabilities())
    await rescan(hass, entry)
    await switch(hass, "switch.remote_enabled", False)
    assert "Detected blocking call" not in caplog.text
