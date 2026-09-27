"""Key naming and per-device modifier tracking."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from evdev import ecodes

DEFAULT_MODIFIERS: frozenset[str] = frozenset(
    {
        "KEY_LEFTSHIFT",
        "KEY_RIGHTSHIFT",
        "KEY_LEFTCTRL",
        "KEY_RIGHTCTRL",
        "KEY_LEFTALT",
        "KEY_RIGHTALT",
        "KEY_LEFTMETA",
        "KEY_RIGHTMETA",
    }
)
DEFAULT_IGNORED: frozenset[str] = frozenset({"KEY_NUMLOCK"})

# Names that input-event-codes.h defines as aliases of another name, plus
# range markers (BTN_MISC, BTN_MOUSE, ...). Never canonical.
_NON_CANONICAL: frozenset[str] = frozenset(
    {
        "KEY_MIN_INTERESTING",
        "KEY_HANGUEL",
        "KEY_SCREENLOCK",
        "KEY_DIRECTION",
        "KEY_DASHBOARD",
        "KEY_BRIGHTNESS_ZERO",
        "KEY_WIMAX",
        "KEY_ZOOM",
        "KEY_SCREEN",
        "KEY_BRIGHTNESS_TOGGLE",
        "BTN_A",
        "BTN_B",
        "BTN_X",
        "BTN_Y",
        "BTN_MISC",
        "BTN_MOUSE",
        "BTN_JOYSTICK",
        "BTN_GAMEPAD",
        "BTN_DIGI",
        "BTN_WHEEL",
        "BTN_TRIGGER_HAPPY",
    }
)


@dataclass(frozen=True, slots=True)
class KeyConfig:
    modifiers: frozenset[str] = DEFAULT_MODIFIERS
    ignored: frozenset[str] = DEFAULT_IGNORED


def key_names(code: int) -> tuple[str, ...]:
    """All names evdev knows for an EV_KEY code, in evdev order."""
    name = ecodes.keys.get(code)
    if name is None:
        return (f"KEY_{code}",)
    if isinstance(name, str):
        return (name,)
    return tuple(name)


def canonical_name(names: tuple[str, ...]) -> str:
    for name in names:
        if name not in _NON_CANONICAL:
            return name
    return names[0]


def key_name(code: int) -> str:
    return canonical_name(key_names(code))


class ModifierTracker:
    """Modifiers held on one device. Held while state != 0."""

    def __init__(self, modifiers: Iterable[str] = DEFAULT_MODIFIERS) -> None:
        self._modifiers = frozenset(modifiers)
        self._held: set[str] = set()

    def is_modifier(self, names: Iterable[str]) -> bool:
        return any(name in self._modifiers for name in names)

    def update(self, names: Iterable[str], state: int) -> None:
        for name in names:
            if name not in self._modifiers:
                continue
            if state != 0:
                self._held.add(name)
            else:
                self._held.discard(name)

    @property
    def active(self) -> tuple[str, ...]:
        return tuple(sorted(self._held))

    def clear(self) -> None:
        self._held.clear()
