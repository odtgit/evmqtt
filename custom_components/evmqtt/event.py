"""Key event entity per input device."""

from __future__ import annotations

from homeassistant.components.event import EventDeviceClass, EventEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from evmqtt.core import KeyEvent

from .entity import EvmqttEntity, async_setup_device_entities
from .hub import EvmqttConfigEntry, EvmqttHub, TrackedDevice

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EvmqttConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_setup_device_entities(entry, async_add_entities, EvmqttKeyEvent)


class EvmqttKeyEvent(EvmqttEntity, EventEntity):
    _key = "event"
    _attr_device_class = EventDeviceClass.BUTTON
    _attr_translation_key = "key"

    def __init__(self, hub: EvmqttHub, device: TrackedDevice) -> None:
        super().__init__(hub, device)
        self._attr_event_types = hub.event_types

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, self._hub.signal_event(self._device.id), self._handle_key
            )
        )

    @callback
    def _handle_key(self, event: KeyEvent) -> None:
        self._trigger_event(
            event.state.name.lower(),
            {
                "key": event.key,
                "modifiers": list(event.modifiers),
                "state": event.state.name,
                "device_id": event.device_id,
                "device_name": event.device_name,
                "device_path": event.device_path,
            },
        )
        self.async_write_ha_state()
