"""Device readers, hotplug and state for one config entry."""

from __future__ import annotations

import asyncio
import errno
import logging
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from functools import partial
from types import ModuleType
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo as HADeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.importlib import async_import_module

from evmqtt import ha
from evmqtt.config import default_base_topic
from evmqtt.core import (
    DeviceInfo,
    DeviceReader,
    DeviceWatcher,
    GrabMode,
    KeyEvent,
    KeyState,
    ReaderStopped,
    StopReason,
    open_device,
)

from .const import (
    CONF_ENABLED_DEVICES,
    CONF_INCLUDE_VIRTUAL,
    CONF_KEYSTATES,
    CONF_MQTT_BASE_TOPIC,
    CONF_MQTT_MIRROR,
    CONF_RESCAN_INTERVAL,
    DEFAULT_KEYSTATES,
    DEFAULT_RESCAN_INTERVAL,
    DOMAIN,
)
from .scan import FoundDevice, scan_devices

_LOGGER = logging.getLogger(__name__)

type EvmqttConfigEntry = ConfigEntry[EvmqttHub]


@dataclass(frozen=True, slots=True)
class Settings:
    enabled: frozenset[str]
    include_virtual: bool
    keystates: tuple[str, ...]
    rescan_interval: int
    mqtt_mirror: bool
    mqtt_base_topic: str

    @classmethod
    def from_options(cls, options: Mapping[str, Any]) -> Settings:
        states = set(options.get(CONF_KEYSTATES) or DEFAULT_KEYSTATES)
        return cls(
            enabled=frozenset(options.get(CONF_ENABLED_DEVICES, ())),
            include_virtual=bool(options.get(CONF_INCLUDE_VIRTUAL, False)),
            keystates=tuple(ha.event_types(KeyState[s.upper()] for s in states)),
            rescan_interval=int(
                options.get(CONF_RESCAN_INTERVAL, DEFAULT_RESCAN_INTERVAL)
            ),
            mqtt_mirror=bool(options.get(CONF_MQTT_MIRROR, False)),
            mqtt_base_topic=str(
                options.get(CONF_MQTT_BASE_TOPIC) or default_base_topic()
            ).strip("/"),
        )

    def structural(self) -> Settings:
        return replace(self, enabled=frozenset())


@dataclass
class TrackedDevice:
    id: str
    name: str
    enabled: bool
    info: DeviceInfo | None = None
    found: FoundDevice | None = None
    present: bool = False
    reader: DeviceReader | None = None
    failures: int = 0
    busy: bool = False

    @property
    def available(self) -> bool:
        return self.present and self.reader is not None


class EvmqttHub:
    """Mirrors evmqtt.gateway.Gateway, with HA entities in place of MQTT."""

    def __init__(self, hass: HomeAssistant, entry: EvmqttConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.settings = Settings.from_options(entry.options)
        self.devices: dict[str, TrackedDevice] = {}
        self._keystates = frozenset(
            KeyState[s.upper()] for s in self.settings.keystates
        )
        self._watcher = DeviceWatcher()
        self._lock = asyncio.Lock()
        self._unsub_interval: CALLBACK_TYPE | None = None
        self._mirror_warned = False
        self._mqtt: ModuleType | None = None
        self._stopped = False

    @property
    def event_types(self) -> list[str]:
        return list(self.settings.keystates)

    @property
    def signal_new_device(self) -> str:
        return f"{DOMAIN}_{self.entry.entry_id}_new_device"

    def signal_update(self, device_id: str) -> str:
        return f"{DOMAIN}_{self.entry.entry_id}_update_{device_id}"

    def signal_event(self, device_id: str) -> str:
        return f"{DOMAIN}_{self.entry.entry_id}_event_{device_id}"

    def device_info(self, device: TrackedDevice) -> HADeviceInfo:
        info = HADeviceInfo(identifiers={(DOMAIN, device.id)}, name=device.name)
        found = device.found
        if found is not None:
            if found.manufacturer:
                info["manufacturer"] = found.manufacturer
            if found.model:
                info["model"] = found.model
            if found.model_id:
                info["model_id"] = found.model_id
        return info

    # -- lifecycle ---------------------------------------------------------

    async def async_start(self) -> None:
        if self.settings.mqtt_mirror:
            try:
                self._mqtt = await async_import_module(
                    self.hass, "homeassistant.components.mqtt"
                )
            except ImportError as err:
                _LOGGER.warning("MQTT mirror disabled, mqtt unavailable: %s", err)
        registry = dr.async_get(self.hass)
        for entry in dr.async_entries_for_config_entry(registry, self.entry.entry_id):
            device_id = next((i for d, i in entry.identifiers if d == DOMAIN), None)
            if device_id is None or device_id in self.devices:
                continue
            self.devices[device_id] = TrackedDevice(
                id=device_id,
                name=entry.name or device_id,
                enabled=device_id in self.settings.enabled,
            )
        await self.async_rescan()
        if self.settings.rescan_interval > 0:
            self._unsub_interval = async_track_time_interval(
                self.hass,
                self._async_interval,
                timedelta(seconds=self.settings.rescan_interval),
                name=f"{DOMAIN} rescan",
                cancel_on_shutdown=True,
            )

    async def async_stop(self, *_: Any) -> None:
        if self._stopped:
            return
        self._stopped = True
        if self._unsub_interval is not None:
            self._unsub_interval()
            self._unsub_interval = None
        async with self._lock:
            for device in self.devices.values():
                reader, device.reader = device.reader, None
                if reader is not None:
                    reader.close()

    async def _async_interval(self, _now: datetime) -> None:
        await self.async_rescan()

    async def async_rescan(self) -> None:
        if self._lock.locked():
            return
        async with self._lock:
            if self._stopped:
                return
            try:
                found = await self.hass.async_add_executor_job(scan_devices)
            except Exception:
                _LOGGER.exception("Device scan failed")
                return
            if self._stopped:
                return
            selected = {
                f.info.id: f
                for f in found
                if self.settings.include_virtual or not f.virtual
            }
            added, removed = self._watcher.update(f.info for f in selected.values())
            for info in removed:
                self._detach(info)
            for info in added:
                await self._async_attach(selected[info.id])

    # -- devices -------------------------------------------------------------

    async def _async_attach(self, found: FoundDevice) -> None:
        info = found.info
        device = self.devices.get(info.id)
        new = device is None
        if device is None:
            enabled = info.id in self.settings.enabled
            device = TrackedDevice(id=info.id, name=info.name, enabled=enabled)
            self.devices[info.id] = device
            _LOGGER.info(
                "New device '%s' (%s) id %s, %s",
                info.name,
                info.path,
                info.id,
                "enabled" if enabled else "disabled",
            )
        elif not device.present and device.info is not None:
            _LOGGER.info("Device '%s' (%s) is back", info.name, info.path)
        device.info = info
        device.found = found
        device.name = info.name
        device.present = True
        dr.async_get(self.hass).async_get_or_create(
            config_entry_id=self.entry.entry_id, **self.device_info(device)
        )
        if new:
            async_dispatcher_send(self.hass, self.signal_new_device, device)
        if device.reader is None:
            await self._async_start_reader(device)
        self._notify(device)

    @callback
    def _detach(self, info: DeviceInfo) -> None:
        device = self.devices.get(info.id)
        if device is None:
            return
        reader = device.reader
        if reader is not None and reader.info.path == info.path:
            device.reader = None
            reader.close()
        device.present = False
        _LOGGER.info("Device '%s' (%s) removed", info.name, info.path)
        self._notify(device)

    async def _async_start_reader(self, device: TrackedDevice) -> None:
        info = device.info
        assert info is not None
        try:
            handle = await self.hass.async_add_executor_job(open_device, info.path)
        except OSError as err:
            self._reader_failed(device, "cannot open", err)
            return
        if self._stopped or device.reader is not None:
            handle.close()
            return
        reader = DeviceReader(
            handle,
            partial(self._on_key, device),
            info=info,
            grab=GrabMode.WHILE_ENABLED,
            enabled=device.enabled,
            on_stopped=partial(self._on_reader_stopped, device),
        )
        try:
            reader.start()
        except OSError as err:
            self._reader_failed(device, "cannot grab", err)
            return
        device.reader = reader
        device.failures = 0
        if device.busy:
            _LOGGER.info("Device '%s' is no longer busy", info.name)
        device.busy = False
        _LOGGER.debug(
            "Reading '%s' (%s)%s",
            info.name,
            info.path,
            ", grabbed" if reader.grabbed else "",
        )

    @callback
    def _reader_failed(self, device: TrackedDevice, what: str, err: OSError) -> None:
        device.failures += 1
        busy = err.errno == errno.EBUSY
        name = device.name
        if busy and not device.busy:
            _LOGGER.warning(
                "Device '%s' is grabbed by another process (EBUSY). Is the evmqtt "
                "add-on or daemon, or keyboard_remote, using it? Unavailable until "
                "released, retrying on rescan",
                name,
            )
        elif not busy and device.failures == 1:
            _LOGGER.error("Device '%s' %s: %s; retrying on rescan", name, what, err)
        else:
            _LOGGER.debug("Device '%s' %s: %s", name, what, err)
        device.busy = busy
        self._watcher.devices.pop(device.id, None)
        self._notify(device)

    @callback
    def _on_reader_stopped(self, device: TrackedDevice, stopped: ReaderStopped) -> None:
        if stopped.reason is StopReason.STOPPED:
            return
        if device.reader is None or device.reader.result is not stopped:
            return
        device.reader = None
        if stopped.reason is StopReason.UNPLUGGED:
            device.present = False
            _LOGGER.info("Device '%s' unplugged", device.name)
        self._watcher.devices.pop(device.id, None)
        self._notify(device)

    @callback
    def _on_key(self, device: TrackedDevice, event: KeyEvent) -> None:
        if event.is_modifier or event.state not in self._keystates:
            return
        async_dispatcher_send(self.hass, self.signal_event(device.id), event)
        if self._mqtt is not None:
            self.entry.async_create_background_task(
                self.hass,
                self._async_mirror(device.id, ha.dumps(ha.event_payload(event))),
                f"{DOMAIN} mirror {device.id}",
            )

    async def _async_mirror(self, device_id: str, payload: str) -> None:
        assert self._mqtt is not None
        topic = f"{self.settings.mqtt_base_topic}/{device_id}/event"
        try:
            await self._mqtt.async_publish(self.hass, topic, payload)
        except HomeAssistantError as err:
            if not self._mirror_warned:
                self._mirror_warned = True
                _LOGGER.warning("MQTT mirror publish to %s failed: %s", topic, err)
            return
        self._mirror_warned = False

    @callback
    def _notify(self, device: TrackedDevice) -> None:
        async_dispatcher_send(self.hass, self.signal_update(device.id))

    # -- enable/disable --------------------------------------------------------

    async def async_set_enabled(self, device_id: str, enabled: bool) -> None:
        device = self.devices[device_id]
        if device.enabled != enabled:
            self._apply(device, enabled, raise_on_error=True)
            _LOGGER.info(
                "Device '%s' %s", device.name, "enabled" if enabled else "disabled"
            )
        self._persist()
        if device.present and device.reader is None and device.info is not None:
            await self._async_start_reader(device)
        self._notify(device)

    @callback
    def apply_enabled(self, enabled: frozenset[str]) -> None:
        """Sync devices to enabled_devices from the options, writing back failures."""
        self.settings = replace(self.settings, enabled=enabled)
        changed = False
        for device in self.devices.values():
            want = device.id in enabled
            if device.enabled == want:
                continue
            self._apply(device, want, raise_on_error=False)
            changed |= device.enabled != want
            self._notify(device)
        if changed:
            self._persist()

    def _apply(
        self, device: TrackedDevice, enabled: bool, raise_on_error: bool
    ) -> None:
        reader = device.reader
        if reader is not None and reader.running:
            try:
                reader.set_enabled(enabled)
            except OSError as err:
                _LOGGER.error("Could not grab '%s': %s", device.name, err)
                if raise_on_error:
                    raise HomeAssistantError(
                        translation_domain=DOMAIN,
                        translation_key="grab_failed",
                        translation_placeholders={
                            "name": device.name,
                            "error": str(err),
                        },
                    ) from err
                return
        device.enabled = enabled

    @callback
    def _persist(self) -> None:
        enabled = sorted(d.id for d in self.devices.values() if d.enabled)
        stale = self.settings.enabled - self.devices.keys()
        value = sorted({*enabled, *stale})
        self.settings = replace(self.settings, enabled=frozenset(value))
        if sorted(self.entry.options.get(CONF_ENABLED_DEVICES, [])) == value:
            return
        self.hass.config_entries.async_update_entry(
            self.entry, options={**self.entry.options, CONF_ENABLED_DEVICES: value}
        )

    @callback
    def async_forget(self, device_id: str) -> bool:
        device = self.devices.get(device_id)
        if device is not None and device.present:
            return False
        self.devices.pop(device_id, None)
        self.settings = replace(
            self.settings, enabled=self.settings.enabled - {device_id}
        )
        self._persist()
        return True
