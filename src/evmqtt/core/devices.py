"""Device descriptors, stable device ids and discovery."""

from __future__ import annotations

import hashlib
import logging
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any, Protocol

import evdev
from evdev import ecodes

from evmqtt.core.keys import key_name

logger = logging.getLogger(__name__)

_SLUG_ID_MAX = 32

# Codes that on their own do not make a device a keyboard or remote:
# ACPI power/sleep buttons, the video bus, and placeholders.
_SYSTEM_KEY_NAMES = (
    "KEY_RESERVED",
    "KEY_UNKNOWN",
    "KEY_POWER",
    "KEY_POWER2",
    "KEY_SLEEP",
    "KEY_WAKEUP",
    "KEY_SUSPEND",
    "KEY_BATTERY",
    "KEY_BRIGHTNESSDOWN",
    "KEY_BRIGHTNESSUP",
    "KEY_BRIGHTNESS_CYCLE",
    "KEY_BRIGHTNESS_AUTO",
    "KEY_BRIGHTNESS_MIN",
    "KEY_BRIGHTNESS_MAX",
    "KEY_DISPLAY_OFF",
    "KEY_DISPLAYTOGGLE",
    "KEY_SWITCHVIDEOMODE",
    "KEY_VIDEO_NEXT",
    "KEY_VIDEO_PREV",
)
SYSTEM_KEYS: frozenset[int] = frozenset(
    ecodes.ecodes[name] for name in _SYSTEM_KEY_NAMES if name in ecodes.ecodes
)
_BUTTON_RANGES = (
    (ecodes.BTN_MISC, ecodes.KEY_OK - 1),
    (ecodes.BTN_TRIGGER_HAPPY, ecodes.BTN_TRIGGER_HAPPY40),
)


class InputDeviceLike(Protocol):
    """The subset of evdev.InputDevice the core uses."""

    path: str
    name: str
    phys: str
    uniq: str

    @property
    def fd(self) -> int: ...

    @property
    def info(self) -> Any: ...

    def capabilities(self) -> Mapping[int, Sequence[Any]]: ...

    def read(self) -> Any: ...

    def grab(self) -> None: ...

    def ungrab(self) -> None: ...

    def close(self) -> None: ...


@dataclass(frozen=True, slots=True)
class DeviceInfo:
    """Snapshot of an input device. id is stable across reboots, path is not."""

    path: str
    name: str
    id: str
    phys: str
    uniq: str
    bustype: int
    vendor: int
    product: int
    version: int
    event_types: frozenset[int]
    key_codes: frozenset[int]

    @property
    def has_key_events(self) -> bool:
        return has_key_events(self)

    @property
    def is_keyboard_like(self) -> bool:
        return is_keyboard_like(self)

    def key_names(self) -> list[str]:
        return [key_name(code) for code in sorted(self.key_codes)]


def slugify(text: str) -> str:
    """Lowercase, hyphen-separated, [a-z0-9-] only."""
    text = text.lower()
    text = re.sub(r"[_\s]+", "-", text)
    text = re.sub(r"[^a-z0-9-]", "", text)
    text = re.sub(r"-+", "-", text)
    text = text.strip("-")
    return text or "unknown-device"


def is_placeholder_serial(uniq: str) -> bool:
    """Empty, or only zeros and separators ("0", "000000", "00:00:00:00:00:00")."""
    return not uniq.strip().strip("0:-. ")


def phys_interface(phys: str) -> str:
    """Port-independent tail of phys: 'input1' in 'usb-0000:00:14.0-3/input1'."""
    return phys.rsplit("/", 1)[1] if "/" in phys else ""


def make_device_id(
    name: str, phys: str, uniq: str, bustype: int, vendor: int, product: int
) -> str:
    """Stable id: name slug plus a hash, serial first.

    With a real serial (uniq, e.g. a USB serial or the Bluetooth peer MAC)
    the hash covers bus/vendor/product/uniq/name plus the interface tail of
    phys (input1), so the id survives a port move and interfaces sharing a
    serial stay apart. Without one it covers bus/vendor/product/phys/name;
    phys carries the port path (usb-0000:00:14.0-3.2/input1). version and
    eventN are left out.
    """
    serial = uniq.strip()
    anchor: tuple[str, ...]
    if is_placeholder_serial(serial):
        anchor = ("phys", phys)
    else:
        anchor = ("uniq", serial, phys_interface(phys))
    material = "\0".join(
        (f"{bustype:04x}", f"{vendor:04x}", f"{product:04x}", *anchor, name)
    )
    digest = hashlib.sha256(material.encode()).hexdigest()[:8]
    prefix = slugify(name)[:_SLUG_ID_MAX].rstrip("-")
    return f"{prefix}-{digest}"


def describe(device: InputDeviceLike, device_id: str | None = None) -> DeviceInfo:
    """Build a DeviceInfo from an open device."""
    info = device.info
    phys = device.phys or ""
    uniq = device.uniq or ""
    caps = device.capabilities()
    return DeviceInfo(
        path=device.path,
        name=device.name,
        id=device_id
        or make_device_id(
            device.name, phys, uniq, info.bustype, info.vendor, info.product
        ),
        phys=phys,
        uniq=uniq,
        bustype=info.bustype,
        vendor=info.vendor,
        product=info.product,
        version=info.version,
        event_types=frozenset(caps),
        key_codes=frozenset(int(c) for c in caps.get(ecodes.EV_KEY, ())),
    )


def open_device(path: str) -> InputDeviceLike:
    """Open a device node. Blocking; async callers use an executor."""
    device: InputDeviceLike = evdev.InputDevice(path)
    return device


def has_key_events(info: DeviceInfo) -> bool:
    """1.x filter_keys_only: any EV_KEY capability."""
    return ecodes.EV_KEY in info.event_types


def is_keyboard_code(code: int) -> bool:
    if code in SYSTEM_KEYS or code > ecodes.KEY_MAX:
        return False
    return not any(low <= code <= high for low, high in _BUTTON_RANGES)


def is_keyboard_like(info: DeviceInfo) -> bool:
    """Has a KEY_* code outside the button ranges and the system keys.

    Excludes mice (BTN_* only), ACPI power buttons and the video bus.
    """
    return any(is_keyboard_code(code) for code in info.key_codes)


def _path_order(path: str) -> tuple[str, int]:
    match = re.match(r"^(.*?)(\d+)$", path)
    if match is None:
        return (path, -1)
    return (match.group(1), int(match.group(2)))


def list_devices(
    predicate: Callable[[DeviceInfo], bool] | None = None,
) -> list[DeviceInfo]:
    """Describe every readable input device, in eventN order.

    Blocking; async callers use an executor. Ids that still collide after
    hashing (identical virtual devices) get -2, -3 in path order, before
    the predicate is applied.
    """
    result: list[DeviceInfo] = []
    seen: dict[str, int] = {}
    for path in sorted(evdev.list_devices(), key=_path_order):
        try:
            device = open_device(path)
        except OSError as err:
            logger.warning("Could not access device %s: %s", path, err)
            continue
        try:
            info = describe(device)
        except OSError as err:
            logger.warning("Could not query device %s: %s", path, err)
            continue
        finally:
            device.close()
        count = seen.get(info.id, 0) + 1
        seen[info.id] = count
        if count > 1:
            info = replace(info, id=f"{info.id}-{count}")
        if predicate is None or predicate(info):
            result.append(info)
    return result
