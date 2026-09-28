"""The MQTT daemon: core readers, hotplug, persistence and HA discovery."""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Awaitable, Callable, Coroutine
from dataclasses import dataclass, field
from functools import partial

from evmqtt import ha
from evmqtt.config import Config
from evmqtt.core import (
    DeviceInfo,
    DeviceReader,
    DeviceWatcher,
    GrabMode,
    KeyEvent,
    ReaderStopped,
    StopReason,
    is_keyboard_like,
    open_device,
)
from evmqtt.mqtt_client import BrokerSettings, MQTTClientWrapper, Will
from evmqtt.state import StateStore
from evmqtt.supervisor import resolve_broker
from evmqtt.sysinfo import is_virtual

logger = logging.getLogger(__name__)

CLEANUP_WINDOW = 3.0


def matches(info: DeviceInfo, selectors: tuple[str, ...]) -> bool:
    return any(s in (info.id, info.path, info.name) for s in selectors)


@dataclass
class Device:
    info: DeviceInfo
    enabled: bool
    present: bool = True
    reader: DeviceReader | None = None
    failures: int = 0
    announced: bool = field(default=False)


class Gateway:
    """Runs until stop(). Never exits for broker trouble; see fatal_error."""

    def __init__(
        self,
        config: Config,
        *,
        cleanup_window: float = CLEANUP_WINDOW,
        broker_resolver: Callable[[Config], Awaitable[BrokerSettings]] | None = None,
    ) -> None:
        self.config = config
        self.topics = ha.Topics(
            config.discovery_prefix, config.base_topic, config.node_id
        )
        self.store = StateStore(config.state_path)
        self.devices: dict[str, Device] = {}
        self.mqtt: MQTTClientWrapper | None = None
        self.fatal_error: BaseException | None = None
        self._cleanup_window = cleanup_window
        self._resolver = broker_resolver or resolve_broker
        self._watcher = DeviceWatcher(
            self._on_added,
            self._on_removed,
            predicate=self.selected,
            interval=config.rescan_interval,
        )
        self._tasks: list[asyncio.Task[None]] = []
        self._wake = asyncio.Event()
        self._cleaned = False
        self._stopped = False

    # -- selection and initial state ------------------------------------

    def selected(self, info: DeviceInfo) -> bool:
        cfg = self.config
        if matches(info, cfg.devices) or matches(info, cfg.enabled_devices):
            return True
        if not cfg.auto_discover:
            return False
        return is_keyboard_like(info) and not is_virtual(info)

    def initially_enabled(self, info: DeviceInfo) -> bool:
        stored = self.store.enabled(info.id)
        if stored is not None:
            return stored
        if not self.config.enabled_devices:
            return True
        return matches(info, self.config.enabled_devices)

    # -- lifecycle -------------------------------------------------------

    async def start(self) -> None:
        """Scan and grab devices, then connect to MQTT in the background."""
        self.store.load()
        await self.rescan()
        logger.info(
            "Managing %d device(s): %s",
            len(self.devices),
            ", ".join(
                f"{d.info.name} [{d.info.id}] {'on' if d.enabled else 'off'}"
                for d in self.devices.values()
            )
            or "none yet",
        )
        if self.config.rescan_interval > 0:
            self._spawn(self._rescan_loop())
        self._spawn(self._run_mqtt())

    def _spawn(self, coro: Coroutine[object, object, None]) -> None:
        task = asyncio.ensure_future(coro)
        task.add_done_callback(self._task_done)
        self._tasks.append(task)

    def _task_done(self, task: asyncio.Task[None]) -> None:
        if task.cancelled():
            return
        err = task.exception()
        if err is not None and self.fatal_error is None:
            self.fatal_error = err
            self._wake.set()

    async def wait(self) -> None:
        await self._wake.wait()

    def request_stop(self) -> None:
        self._wake.set()

    async def stop(self) -> None:
        if self._stopped:
            return
        self._stopped = True
        logger.info("Shutting down...")
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        for device in self.devices.values():
            if device.reader is not None:
                device.reader.close()
        if self.mqtt is not None:
            info = self.mqtt.publish(self.topics.status, ha.OFFLINE, qos=1, retain=True)
            if info is not None:
                await asyncio.to_thread(_wait_published, info)
            await asyncio.to_thread(self.mqtt.stop)
        logger.info("Shutdown complete")

    async def rescan(self) -> None:
        await self._watcher.rescan()

    async def _rescan_loop(self) -> None:
        while True:
            await asyncio.sleep(self.config.rescan_interval)
            try:
                await self.rescan()
            except Exception:
                logger.exception("Device rescan failed")

    # -- devices ---------------------------------------------------------

    def _on_added(self, info: DeviceInfo) -> None:
        device = self.devices.get(info.id)
        if device is None:
            device = Device(info, self.initially_enabled(info))
            self.devices[info.id] = device
            logger.info(
                "New device '%s' (%s) id %s, %s",
                info.name,
                info.path,
                info.id,
                "enabled" if device.enabled else "disabled",
            )
        else:
            device.info = info
            if not device.present:
                logger.info("Device '%s' (%s) is back", info.name, info.path)
        device.present = True
        self._persist(device)
        if device.reader is None:
            self._start_reader(device)
        self._announce(device)

    def _on_removed(self, info: DeviceInfo) -> None:
        device = self.devices.get(info.id)
        if device is None:
            return
        reader = device.reader
        if reader is not None and reader.info.path == info.path:
            device.reader = None
            reader.close()
        device.present = False
        logger.info("Device '%s' (%s) removed", info.name, info.path)
        self._publish_availability(device)

    def _start_reader(self, device: Device) -> None:
        info = device.info
        try:
            handle = open_device(info.path)
        except OSError as err:
            self._reader_failed(device, f"cannot open: {err}")
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
            self._reader_failed(device, f"cannot grab: {err}")
            return
        device.reader = reader
        device.failures = 0
        logger.info(
            "Reading '%s' (%s)%s",
            info.name,
            info.path,
            ", grabbed" if reader.grabbed else "",
        )
        self._publish_availability(device)

    def _reader_failed(self, device: Device, reason: str) -> None:
        device.failures += 1
        log = logger.error if device.failures == 1 else logger.debug
        log(
            "Device '%s' (%s) %s; retrying on rescan",
            device.info.name,
            device.info.path,
            reason,
        )
        self._watcher.devices.pop(device.info.id, None)
        self._publish_availability(device)

    def _on_reader_stopped(self, device: Device, stopped: ReaderStopped) -> None:
        if stopped.reason is StopReason.STOPPED:
            return
        if device.reader is None or device.reader.result is not stopped:
            return
        device.reader = None
        if stopped.reason is StopReason.UNPLUGGED:
            device.present = False
            logger.info("Device '%s' unplugged", device.info.name)
        self._watcher.devices.pop(device.info.id, None)
        self._publish_availability(device)

    def _on_key(self, device: Device, event: KeyEvent) -> None:
        if event.is_modifier or event.state not in self.config.keystates:
            return
        payload = ha.dumps(ha.event_payload(event))
        if self.mqtt is not None:
            self.mqtt.publish(self.topics.event(device.info.id), payload)
        logger.debug("Event %s", payload)

    def set_enabled(self, device: Device, enabled: bool) -> None:
        if device.reader is not None and device.reader.running:
            try:
                device.reader.set_enabled(enabled)
            except OSError as err:
                logger.error("Could not grab '%s': %s", device.info.name, err)
                self._publish_switch(device)
                return
        if device.enabled != enabled:
            logger.info(
                "Device '%s' %s", device.info.name, "enabled" if enabled else "disabled"
            )
        device.enabled = enabled
        self._persist(device)
        self._publish_switch(device)

    def _persist(self, device: Device) -> None:
        self.store.set(
            device.info.id, device.enabled, device.info.name, device.info.path
        )

    # -- MQTT --------------------------------------------------------------

    async def _run_mqtt(self) -> None:
        settings = await self._resolver(self.config)
        self.mqtt = MQTTClientWrapper(
            f"evmqtt-{self.config.node_id}-{os.getpid()}",
            settings,
            asyncio.get_running_loop(),
            will=Will(self.topics.status, ha.OFFLINE),
            on_connect=self._on_connected,
        )
        self.mqtt.subscribe(self.topics.switch_commands, self._on_switch_command)
        self.mqtt.subscribe(self.topics.ha_status, self._on_ha_status)
        self.mqtt.start()

    def _on_connected(self) -> None:
        assert self.mqtt is not None
        self.mqtt.publish(self.topics.status, ha.ONLINE, qos=1, retain=True)
        self._publish_discovery()
        for device_id in self.store.ids():
            if device_id not in self.devices:
                self.mqtt.publish(
                    self.topics.availability(device_id), ha.OFFLINE, qos=1, retain=True
                )
        if self.config.cleanup_legacy and not self._cleaned:
            self._cleaned = True
            self._spawn(self._cleanup_legacy())

    def _publish_discovery(self) -> None:
        assert self.mqtt is not None
        self.mqtt.publish(
            self.topics.gateway_discovery,
            ha.dumps(ha.gateway_discovery(self.topics, self.config.gateway_name)),
            qos=1,
            retain=True,
        )
        for device in self.devices.values():
            device.announced = False
            self._announce(device)

    def _announce(self, device: Device) -> None:
        if self.mqtt is None or not self.mqtt.is_connected:
            return
        if not device.announced:
            payload = ha.device_discovery(
                self.topics, device.info, self.config.keystates
            )
            self.mqtt.publish(
                self.topics.device_discovery(device.info.id),
                ha.dumps(payload),
                qos=1,
                retain=True,
            )
            device.announced = True
        self._publish_switch(device)
        self._publish_availability(device)

    def _publish_switch(self, device: Device) -> None:
        if self.mqtt is not None:
            self.mqtt.publish(
                self.topics.switch_state(device.info.id),
                ha.ON if device.enabled else ha.OFF,
                qos=1,
                retain=True,
            )

    def _publish_availability(self, device: Device) -> None:
        if self.mqtt is None:
            return
        online = device.present and device.reader is not None
        self.mqtt.publish(
            self.topics.availability(device.info.id),
            ha.ONLINE if online else ha.OFFLINE,
            qos=1,
            retain=True,
        )

    def _on_switch_command(self, topic: str, payload: str, retain: bool) -> None:
        device_id = self.topics.device_id_from_command(topic)
        device = self.devices.get(device_id or "")
        if device is None:
            logger.debug("Switch command for unknown device on '%s'", topic)
            return
        value = payload.strip().upper()
        if value not in (ha.ON, ha.OFF):
            logger.warning("Invalid switch command %r on '%s'", payload, topic)
            return
        self.set_enabled(device, value == ha.ON)

    def _on_ha_status(self, topic: str, payload: str, retain: bool) -> None:
        if payload.strip().lower() == ha.ONLINE and self.mqtt is not None:
            logger.info("Home Assistant is online, republishing discovery")
            self._publish_discovery()

    # -- 1.x cleanup ---------------------------------------------------------

    async def _cleanup_legacy(self) -> None:
        try:
            await self._cleanup_legacy_once()
        except Exception:
            logger.exception("1.x discovery cleanup failed")

    async def _cleanup_legacy_once(self) -> None:
        assert self.mqtt is not None
        cfg = self.config
        found: dict[str, str] = {}

        def collect(topic: str, payload: str, retain: bool) -> None:
            if retain and payload:
                found[topic] = payload

        patterns = list(
            dict.fromkeys(
                [f"{cfg.discovery_prefix}/+/+/config", f"{cfg.legacy_topic}/+/config"]
            )
        )
        for pattern in patterns:
            self.mqtt.subscribe(pattern, collect)
        try:
            await asyncio.sleep(self._cleanup_window)
        finally:
            for pattern in patterns:
                self.mqtt.unsubscribe(pattern)
        own = {self.topics.switch_state(i) for i in self.devices}
        cleared: list[str] = []
        for topic, payload in sorted(found.items()):
            for target in ha.is_legacy_config(
                topic,
                payload,
                legacy_topic=cfg.legacy_topic,
                discovery_prefix=cfg.discovery_prefix,
            ):
                if target in own:
                    continue
                self.mqtt.publish(target, "", qos=1, retain=True)
                cleared.append(target)
        if cleared:
            logger.info(
                "Removed %d retained 1.x topic(s): %s", len(cleared), ", ".join(cleared)
            )
        else:
            logger.debug("No 1.x discovery found to clean up")


def _wait_published(info: object) -> None:
    wait = getattr(info, "wait_for_publish", None)
    if wait is None:
        return
    try:
        wait(timeout=2.0)
    except (RuntimeError, ValueError):
        pass
