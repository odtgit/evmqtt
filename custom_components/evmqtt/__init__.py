"""Linux input devices as native Home Assistant event and switch entities."""

from __future__ import annotations

from homeassistant.const import EVENT_HOMEASSISTANT_STOP, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

from .const import CONF_ENABLED_DEVICES, DOMAIN
from .hub import EvmqttConfigEntry, EvmqttHub, Settings

PLATFORMS = [Platform.EVENT, Platform.SWITCH]


async def async_setup_entry(hass: HomeAssistant, entry: EvmqttConfigEntry) -> bool:
    hub = EvmqttHub(hass, entry)
    await hub.async_start()
    entry.runtime_data = hub
    try:
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except BaseException:
        await hub.async_stop()
        raise
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    entry.async_on_unload(
        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, hub.async_stop)
    )
    return True


async def async_unload_entry(hass: HomeAssistant, entry: EvmqttConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.async_stop()
    return unloaded


async def _async_update_listener(hass: HomeAssistant, entry: EvmqttConfigEntry) -> None:
    hub = entry.runtime_data
    settings = Settings.from_options(entry.options)
    if settings.structural() != hub.settings.structural():
        hass.config_entries.async_schedule_reload(entry.entry_id)
        return
    hub.apply_enabled(frozenset(entry.options.get(CONF_ENABLED_DEVICES, ())))


async def async_remove_config_entry_device(
    hass: HomeAssistant, entry: EvmqttConfigEntry, device: dr.DeviceEntry
) -> bool:
    """Allow removing devices that are not plugged in."""
    device_id = next((i for d, i in device.identifiers if d == DOMAIN), None)
    if device_id is None:
        return True
    return entry.runtime_data.async_forget(device_id)
