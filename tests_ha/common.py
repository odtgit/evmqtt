"""Helpers for the Home Assistant integration tests."""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

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
from evmqtt.core import make_device_id
from tests.fakes import FakeInputDevice, drained

BASE_TOPIC = "evmqtt/test-host"


def device_id(fake: FakeInputDevice) -> str:
    return make_device_id(
        fake.name,
        fake.phys,
        fake.uniq,
        fake.info.bustype,
        fake.info.vendor,
        fake.info.product,
    )


def make_entry(**options: Any) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        title="Input devices",
        data={},
        options={
            CONF_ENABLED_DEVICES: [],
            CONF_INCLUDE_VIRTUAL: False,
            CONF_KEYSTATES: ["press"],
            CONF_RESCAN_INTERVAL: 5,
            CONF_NEW_DEVICES_ENABLED: False,
            CONF_MQTT_MIRROR: False,
            CONF_MQTT_BASE_TOPIC: BASE_TOPIC,
            **options,
        },
    )


async def setup_entry(hass: HomeAssistant, **options: Any) -> MockConfigEntry:
    entry = make_entry(**options)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def settle(hass: HomeAssistant, *devices: FakeInputDevice) -> None:
    await drained(*devices)
    await hass.async_block_till_done()
