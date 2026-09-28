"""Enable switch per input device. Grabs the device while on."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .entity import EvmqttEntity, async_setup_device_entities
from .hub import EvmqttConfigEntry

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EvmqttConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_setup_device_entities(entry, async_add_entities, EvmqttEnabledSwitch)


class EvmqttEnabledSwitch(EvmqttEntity, SwitchEntity):
    _key = "switch"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_translation_key = "enabled"

    @property
    def available(self) -> bool:
        return self._device.present

    @property
    def is_on(self) -> bool:
        return self._device.enabled

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._hub.async_set_enabled(self._device.id, True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._hub.async_set_enabled(self._device.id, False)
