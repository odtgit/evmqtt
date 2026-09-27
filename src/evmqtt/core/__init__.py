"""evmqtt core: evdev discovery, asyncio reader, key/modifier handling.

No MQTT dependency. Runtime dependency: evdev.
"""

from evmqtt.core.devices import (
    SYSTEM_KEYS,
    DeviceInfo,
    InputDeviceLike,
    describe,
    has_key_events,
    is_keyboard_code,
    is_keyboard_like,
    is_placeholder_serial,
    list_devices,
    make_device_id,
    open_device,
    phys_interface,
    slugify,
)
from evmqtt.core.events import KeyEvent, KeyState, ReaderStopped, StopReason
from evmqtt.core.keys import (
    DEFAULT_IGNORED,
    DEFAULT_MODIFIERS,
    KeyConfig,
    ModifierTracker,
    canonical_name,
    key_name,
    key_names,
)
from evmqtt.core.reader import DeviceReader, EventCallback, GrabMode, StoppedCallback
from evmqtt.core.watcher import DeviceCallback, DeviceWatcher

__all__ = [
    "DEFAULT_IGNORED",
    "DEFAULT_MODIFIERS",
    "SYSTEM_KEYS",
    "DeviceCallback",
    "DeviceInfo",
    "DeviceReader",
    "DeviceWatcher",
    "EventCallback",
    "GrabMode",
    "InputDeviceLike",
    "KeyConfig",
    "KeyEvent",
    "KeyState",
    "ModifierTracker",
    "ReaderStopped",
    "StopReason",
    "StoppedCallback",
    "canonical_name",
    "describe",
    "has_key_events",
    "is_keyboard_code",
    "is_keyboard_like",
    "is_placeholder_serial",
    "key_name",
    "key_names",
    "list_devices",
    "make_device_id",
    "open_device",
    "phys_interface",
    "slugify",
]
