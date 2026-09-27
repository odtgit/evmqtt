"""Blocking device discovery. Run in the executor."""

from __future__ import annotations

import glob
import os
from dataclasses import dataclass

from evmqtt.core import DeviceInfo, list_devices
from evmqtt.sysinfo import is_virtual, model_id, vendor_model

INPUT_DIR = "/dev/input"


class InputAccessError(Exception):
    """Nothing readable under /dev/input. reason is a config flow error key."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True, slots=True)
class FoundDevice:
    info: DeviceInfo
    virtual: bool
    manufacturer: str | None
    model: str | None
    model_id: str | None


def input_nodes() -> list[str]:
    return sorted(glob.glob(os.path.join(INPUT_DIR, "event*")))


def _found(info: DeviceInfo) -> FoundDevice:
    manufacturer, model = vendor_model(info)
    return FoundDevice(info, is_virtual(info), manufacturer, model, model_id(info))


def scan_devices(strict: bool = False) -> list[FoundDevice]:
    """Keyboard-like devices with sysfs metadata.

    strict raises InputAccessError when no device at all could be opened.
    """
    readable = list_devices()
    if strict and not readable:
        raise InputAccessError("permission_denied" if input_nodes() else "no_input")
    return [_found(info) for info in readable if info.is_keyboard_like]
