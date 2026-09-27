"""Persisted per-device enable state, keyed by stable device id."""

from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

VERSION = 1


class StateStore:
    """JSON file: {"version": 1, "devices": {id: {"enabled", "name", "path"}}}.

    Write errors are logged, never raised: losing the file costs a reseed
    from enabled_devices, not the daemon.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self._devices: dict[str, dict[str, Any]] = {}

    def load(self) -> None:
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
        logger.info(
            "Loaded state for %d device(s) from '%s'", len(self._devices), self.path
        )

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
