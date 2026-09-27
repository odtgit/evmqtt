"""Event model emitted by the core."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, IntEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from evmqtt.core.devices import DeviceInfo


class KeyState(IntEnum):
    """evdev EV_KEY value."""

    RELEASE = 0
    PRESS = 1
    REPEAT = 2


@dataclass(frozen=True, slots=True)
class KeyEvent:
    """One EV_KEY event from one device.

    key is the canonical kernel name for the code. names holds every name
    evdev reports for it (aliased codes have several), in evdev order.
    modifiers are the modifiers held on this device, sorted.
    """

    device_id: str
    device_name: str
    device_path: str
    key: str
    names: tuple[str, ...]
    code: int
    state: KeyState
    modifiers: tuple[str, ...]
    is_modifier: bool
    timestamp: float

    @property
    def joined_key(self) -> str:
        """1.x key form: aliased names joined with '|'."""
        return "|".join(self.names)


class StopReason(Enum):
    STOPPED = "stopped"
    UNPLUGGED = "unplugged"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class ReaderStopped:
    """Lifecycle signal: a reader has closed its device."""

    device: DeviceInfo
    reason: StopReason
    error: OSError | None = None
