"""Base entity for evmqtt input devices."""

from __future__ import annotations

from collections.abc import Callable

from homeassistant.core import callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .hub import EvmqttConfigEntry, EvmqttHub, TrackedDevice


class EvmqttEntity(Entity):
    _attr_has_entity_name = True
    _attr_should_poll = False
    _key: str

    def __init__(self, hub: EvmqttHub, device: TrackedDevice) -> None:
        self._hub = hub
        self._device = device
        self._attr_unique_id = f"{device.id}_{self._key}"
        self._attr_device_info = hub.device_info(device)

    @property
    def available(self) -> bool:
        return self._device.available

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                self._hub.signal_update(self._device.id),
                self.async_write_ha_state,
            )
        )


@callback
def async_setup_device_entities(
    entry: EvmqttConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
    factory: Callable[[EvmqttHub, TrackedDevice], Entity],
) -> None:
    """Add entities for known devices now and for new devices on hotplug."""
    hub = entry.runtime_data
    added: set[str] = set()

    @callback
    def add(devices: list[TrackedDevice]) -> None:
        new = [d for d in devices if d.id not in added]
        added.update(d.id for d in new)
        if new:
            async_add_entities([factory(hub, d) for d in new])

    @callback
    def add_one(device: TrackedDevice) -> None:
        add([device])

    add(list(hub.devices.values()))
    entry.async_on_unload(
        async_dispatcher_connect(hub.hass, hub.signal_new_device, add_one)
    )
