"""Polling hotplug hook."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterable

from evmqtt.core.devices import DeviceInfo, list_devices

DeviceCallback = Callable[[DeviceInfo], None]


class DeviceWatcher:
    """Diffs successive device scans by id and reports added/removed.

    A device whose id is unchanged but whose path moved is reported as
    removed then added. update() is the pure diff for callers that scan
    themselves (e.g. HA's executor); rescan() scans in the default executor.
    """

    def __init__(
        self,
        on_added: DeviceCallback | None = None,
        on_removed: DeviceCallback | None = None,
        *,
        predicate: Callable[[DeviceInfo], bool] | None = None,
        interval: float = 5.0,
    ) -> None:
        self._on_added = on_added
        self._on_removed = on_removed
        self._predicate = predicate
        self.interval = interval
        self.devices: dict[str, DeviceInfo] = {}

    def update(
        self, current: Iterable[DeviceInfo]
    ) -> tuple[list[DeviceInfo], list[DeviceInfo]]:
        latest = {info.id: info for info in current}
        removed = [
            info
            for device_id, info in self.devices.items()
            if device_id not in latest or latest[device_id].path != info.path
        ]
        added = [
            info
            for device_id, info in latest.items()
            if device_id not in self.devices
            or self.devices[device_id].path != info.path
        ]
        self.devices = latest
        for info in removed:
            if self._on_removed is not None:
                self._on_removed(info)
        for info in added:
            if self._on_added is not None:
                self._on_added(info)
        return added, removed

    async def rescan(self) -> tuple[list[DeviceInfo], list[DeviceInfo]]:
        loop = asyncio.get_running_loop()
        current = await loop.run_in_executor(None, list_devices, self._predicate)
        return self.update(current)

    async def run(self) -> None:
        while True:
            await self.rescan()
            await asyncio.sleep(self.interval)
