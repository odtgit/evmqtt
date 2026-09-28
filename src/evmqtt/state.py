"""Persisted per-device enable state, keyed by stable device id."""

from __future__ import annotations

import json
import logging
import os
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from evmqtt.core import matches_selectors

logger = logging.getLogger(__name__)

# 1: no version field, or 1. Devices default to "on"; empty enabled_devices
#    meant "enable all", so "on" could mean either a user's choice or the
#    old default. Migrated on load: see _migrate.
# 2: "on" always means a person enabled the device, via enabled_devices,
#    devices, or the HA switch.
VERSION = 2


class StateStore:
    """JSON file: {"version": 2, "devices": {id: {"enabled", "name", "path"}}}.

    Write errors are logged, never raised: losing the file costs a reseed
    from enabled_devices, not the daemon.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self._devices: dict[str, dict[str, Any]] = {}

    def load(self, *, selectors: Sequence[str] = ()) -> None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            logger.info("No state file at '%s', starting fresh", self.path)
            return
        except (OSError, ValueError) as err:
            logger.warning("Ignoring unreadable state file '%s': %s", self.path, err)
            return
        devices = data.get("devices") if isinstance(data, dict) else None
        if not isinstance(devices, dict):
            logger.warning("Ignoring state file '%s': no devices map", self.path)
            return
        self._devices = {
            str(k): dict(v)
            for k, v in devices.items()
            if isinstance(v, dict) and isinstance(v.get("enabled"), bool)
        }
        version = data.get("version") if isinstance(data, dict) else None
        logger.info(
            "Loaded state for %d device(s) from '%s'", len(self._devices), self.path
        )
        if not isinstance(version, int) or version < VERSION:
            self._migrate(selectors)

    def _migrate(self, selectors: Sequence[str]) -> None:
        """2.0/2.1 seeded "on" for every device: empty enabled_devices meant
        "enable all", so a pre-3.0 state file cannot tell that default apart
        from a person's choice. Keep "on" only where devices/enabled_devices
        still name the device; disable the rest, so a device is never
        silently publishing keys just because it predates opt-in."""
        disabled: list[str] = []
        for device_id, entry in self._devices.items():
            if not entry.get("enabled"):
                continue
            name = str(entry.get("name", ""))
            path = str(entry.get("path", ""))
            if matches_selectors(device_id, path, name, selectors):
                continue
            entry["enabled"] = False
            disabled.append(f"{name or device_id} ({device_id})")
        if disabled:
            logger.warning(
                "Migrating state file '%s' to schema %d: disabling %d device(s) "
                "that were only 'on' because of the old all-enabled default: %s. "
                "Re-enable via the Home Assistant switch, or list them in "
                "'devices'/'enabled_devices'.",
                self.path,
                VERSION,
                len(disabled),
                ", ".join(sorted(disabled)),
            )
        self.save()

    def ids(self) -> list[str]:
        return list(self._devices)

    def enabled(self, device_id: str) -> bool | None:
        entry = self._devices.get(device_id)
        return None if entry is None else bool(entry["enabled"])

    def set(self, device_id: str, enabled: bool, name: str, path: str) -> None:
        entry = {"enabled": enabled, "name": name, "path": path}
        if self._devices.get(device_id) == entry:
            return
        self._devices[device_id] = entry
        self.save()

    def save(self) -> None:
        data = {"version": VERSION, "devices": self._devices}
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(
                prefix=".evmqtt-state-", dir=str(self.path.parent)
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2, sort_keys=True)
                os.replace(tmp, self.path)
            except BaseException:
                os.unlink(tmp)
                raise
        except OSError as err:
            logger.warning("Could not write state file '%s': %s", self.path, err)
