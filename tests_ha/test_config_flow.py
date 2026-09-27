"""Config and options flow."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.evmqtt.const import (
    CONF_ENABLED_DEVICES,
    CONF_INCLUDE_VIRTUAL,
    CONF_KEYSTATES,
    CONF_MQTT_BASE_TOPIC,
    CONF_MQTT_MIRROR,
    CONF_NEW_DEVICES_ENABLED,
    CONF_RESCAN_INTERVAL,
    DOMAIN,
)
from tests.fakes import (
    FakeEvdevRegistry,
    FakeSysfs,
    keyboard_capabilities,
    mouse_capabilities,
)
from tests_ha.common import BASE_TOPIC, device_id, setup_entry


def _schema_keys(result: dict) -> set[str]:
    return {str(k) for k in result["data_schema"].schema}


def _device_values(result: dict) -> list[str]:
    for key, selector in result["data_schema"].schema.items():
        if str(key) == CONF_ENABLED_DEVICES:
            return [o["value"] for o in selector.config["options"]]
    return []


async def test_user_flow_happy_path(
    hass: HomeAssistant, fake_evdev: FakeEvdevRegistry, sysfs: FakeSysfs
) -> None:
    kbd = fake_evdev.add("/dev/input/event0", "Keyboard", keyboard_capabilities())
    remote = fake_evdev.add("/dev/input/event1", "Remote", keyboard_capabilities())
    fake_evdev.add("/dev/input/event2", "Mouse", mouse_capabilities())
    keyd = fake_evdev.add(
        "/dev/input/event3", "keyd virtual keyboard", keyboard_capabilities()
    )
    sysfs.add("event3", virtual=True)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert not result["errors"]
    assert result["description_placeholders"] == {"count": "2", "virtual": "1"}
    assert _device_values(result) == [device_id(kbd), device_id(remote)]

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_INCLUDE_VIRTUAL: True, CONF_ENABLED_DEVICES: []}
    )
    assert result["type"] is FlowResultType.FORM
    assert device_id(keyd) in _device_values(result)

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_INCLUDE_VIRTUAL: True, CONF_ENABLED_DEVICES: [device_id(remote)]},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["options"] == {
        CONF_ENABLED_DEVICES: [device_id(remote)],
        CONF_INCLUDE_VIRTUAL: True,
        CONF_KEYSTATES: ["press"],
        CONF_RESCAN_INTERVAL: 5,
        CONF_NEW_DEVICES_ENABLED: False,
        CONF_MQTT_MIRROR: False,
        CONF_MQTT_BASE_TOPIC: BASE_TOPIC,
    }
    await hass.async_block_till_done()
    assert remote.grabbed
    assert not kbd.grabbed
    assert not keyd.grabbed
    assert hass.states.get("switch.keyboard_enabled").state == "off"
    assert hass.states.get("switch.remote_enabled").state == "on"
    assert hass.states.get("event.keyd_virtual_keyboard_key") is not None


async def test_user_flow_single_instance(
    hass: HomeAssistant, fake_evdev: FakeEvdevRegistry
) -> None:
    await setup_entry(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "single_instance_allowed"


async def test_user_flow_no_devices(
    hass: HomeAssistant, fake_evdev: FakeEvdevRegistry
) -> None:
    fake_evdev.add("/dev/input/event0", "Mouse", mouse_capabilities())
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "no_devices"}
    assert CONF_ENABLED_DEVICES not in _schema_keys(result)

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_INCLUDE_VIRTUAL: False}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["options"][CONF_ENABLED_DEVICES] == []


async def test_user_flow_no_input_then_permission_then_ok(
    hass: HomeAssistant, fake_evdev: FakeEvdevRegistry
) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "no_input"}
    assert _schema_keys(result) == set()

    fake_evdev.deny("/dev/input/event0")
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["errors"] == {"base": "permission_denied"}

    fake_evdev._errors.clear()
    fake_evdev.add("/dev/input/event0", "Remote", keyboard_capabilities())
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.FORM
    assert not result["errors"]
    assert _device_values(result) == [device_id(fake_evdev.open("/dev/input/event0"))]
    fake_evdev.open("/dev/input/event0").close()


async def test_options_flow_live_enable_without_reload(
    hass: HomeAssistant, fake_evdev: FakeEvdevRegistry
) -> None:
    remote = fake_evdev.add("/dev/input/event0", "Remote", keyboard_capabilities())
    entry = await setup_entry(hass)
    assert not remote.grabbed

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    assert CONF_MQTT_MIRROR not in _schema_keys(result)
    assert _device_values(result) == [device_id(remote)]

    with patch.object(hass.config_entries, "async_schedule_reload") as reload:
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            {
                CONF_ENABLED_DEVICES: [device_id(remote)],
                CONF_KEYSTATES: ["press"],
                CONF_RESCAN_INTERVAL: 5,
                CONF_INCLUDE_VIRTUAL: False,
                CONF_NEW_DEVICES_ENABLED: False,
            },
        )
        await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    reload.assert_not_called()
    assert remote.grabbed
    assert hass.states.get("switch.remote_enabled").state == "on"
    assert entry.options[CONF_MQTT_BASE_TOPIC] == BASE_TOPIC


async def test_options_flow_structural_change_reloads(
    hass: HomeAssistant, fake_evdev: FakeEvdevRegistry
) -> None:
    fake_evdev.add("/dev/input/event0", "Remote", keyboard_capabilities())
    entry = await setup_entry(hass)
    hub = entry.runtime_data
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_ENABLED_DEVICES: [],
            CONF_KEYSTATES: [],
            CONF_RESCAN_INTERVAL: 5,
            CONF_INCLUDE_VIRTUAL: False,
            CONF_NEW_DEVICES_ENABLED: False,
        },
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_KEYSTATES: "keystates_required"}

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_ENABLED_DEVICES: [],
            CONF_KEYSTATES: ["release", "press"],
            CONF_RESCAN_INTERVAL: 0,
            CONF_INCLUDE_VIRTUAL: False,
            CONF_NEW_DEVICES_ENABLED: True,
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert entry.runtime_data is not hub
    assert entry.options[CONF_RESCAN_INTERVAL] == 0
    state = hass.states.get("event.remote_key")
    assert state.attributes["event_types"] == ["press", "release"]


@pytest.mark.parametrize("expected_lingering_timers", [True])
async def test_options_flow_mqtt_fields(
    hass: HomeAssistant, fake_evdev: FakeEvdevRegistry, mqtt_mock
) -> None:
    fake_evdev.add("/dev/input/event0", "Remote", keyboard_capabilities())
    entry = await setup_entry(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert {CONF_MQTT_MIRROR, CONF_MQTT_BASE_TOPIC} <= _schema_keys(result)
    base = {
        CONF_ENABLED_DEVICES: [],
        CONF_KEYSTATES: ["press"],
        CONF_RESCAN_INTERVAL: 5,
        CONF_INCLUDE_VIRTUAL: False,
        CONF_NEW_DEVICES_ENABLED: False,
        CONF_MQTT_MIRROR: True,
    }
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {**base, CONF_MQTT_BASE_TOPIC: "bad/#"}
    )
    assert result["errors"] == {CONF_MQTT_BASE_TOPIC: "invalid_topic"}
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {**base, CONF_MQTT_BASE_TOPIC: "/home/keys/"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_MQTT_MIRROR] is True
    assert entry.options[CONF_MQTT_BASE_TOPIC] == "home/keys"
