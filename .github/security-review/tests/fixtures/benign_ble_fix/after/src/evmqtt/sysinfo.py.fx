"""sysfs lookups for input devices: virtual devices, manufacturer and model."""

from __future__ import annotations

import os
from pathlib import Path

from evdev import ecodes

from evmqtt.core import DeviceInfo

SYSFS_ROOT = Path("/sys")
BUS_VIRTUAL = getattr(ecodes, "BUS_VIRTUAL", 0x06)
_MAX_PARENTS = 6


def _input_node(info: DeviceInfo) -> Path | None:
    name = os.path.basename(info.path)
    node = SYSFS_ROOT / "class" / "input" / name / "device"
    try:
        return node.resolve(strict=True)
    except OSError:
        return None


def is_virtual(info: DeviceInfo) -> bool:
    """BUS_VIRTUAL, or a node under /sys/devices/virtual (uinput, keyd, ydotool).

    uhid is the exception: BlueZ creates Bluetooth LE (HoG) keyboards and
    remotes through /dev/uhid, so they sit under virtual/misc/uhid too.
    """
    if info.bustype == BUS_VIRTUAL:
        return True
    node = _input_node(info)
    if node is None:
        return False
    virtual = (SYSFS_ROOT / "devices" / "virtual").resolve()
    if node != virtual and virtual not in node.parents:
        return False
    return virtual / "misc" / "uhid" not in node.parents


def _read(path: Path) -> str | None:
    try:
        value = path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return None
    return value or None


def vendor_model(info: DeviceInfo) -> tuple[str | None, str | None]:
    """USB manufacturer/product strings from the nearest parent that has them."""
    node = _input_node(info)
    if node is None:
        return None, None
    for directory in [node, *node.parents][:_MAX_PARENTS]:
        manufacturer = _read(directory / "manufacturer")
        product = _read(directory / "product")
        if manufacturer or product:
            return manufacturer, product
    return None, None


def model_id(info: DeviceInfo) -> str | None:
    if not info.vendor and not info.product:
        return None
    return f"{info.vendor:04x}:{info.product:04x}"
