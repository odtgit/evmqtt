"""1.x publish rules applied to core KeyEvents."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from evmqtt.core import DEFAULT_IGNORED, DEFAULT_MODIFIERS, KeyConfig, KeyState


@dataclass
class KeyHandler:
    """Decides which key events the daemon publishes and how the key reads.

    Modifier state lives in the core reader, per device. This class only
    holds the modifier/ignored sets (handed to the core via key_config),
    the states to publish, and the 1.x key string format.

    Attributes:
        modifiers: Key names considered modifier keys.
        ignored_keys: Key names to ignore (e.g., NUMLOCK).
        publish_states: Key states to publish. 1.x publishes PRESS only.
    """

    modifiers: set[str] = field(default_factory=lambda: set(DEFAULT_MODIFIERS))
    ignored_keys: set[str] = field(default_factory=lambda: set(DEFAULT_IGNORED))
    publish_states: frozenset[KeyState] = frozenset({KeyState.PRESS})

    @property
    def key_config(self) -> KeyConfig:
        return KeyConfig(
            modifiers=frozenset(self.modifiers), ignored=frozenset(self.ignored_keys)
        )

    def is_modifier(self, keycode: str) -> bool:
        return keycode in self.modifiers

    def is_ignored(self, keycode: str) -> bool:
        return keycode in self.ignored_keys

    def should_publish(
        self, keycode: str | list[str] | tuple[str, ...], keystate: int
    ) -> bool:
        """Publish configured states of non-modifier, non-ignored keys.

        Args:
            keycode: A key name, or all names of an aliased code
                (e.g. KEY_MUTE -> ('KEY_MIN_INTERESTING', 'KEY_MUTE')).
            keystate: 0 release, 1 press, 2 repeat.
        """
        if keystate not in self.publish_states:
            return False
        primary_key = keycode[0] if isinstance(keycode, (list, tuple)) else keycode
        return not self.is_modifier(primary_key) and not self.is_ignored(primary_key)

    @staticmethod
    def format_keycode(keycode: str | list[str] | tuple[str, ...]) -> str:
        """Aliased names joined with '|', as 1.x published them."""
        if isinstance(keycode, (list, tuple)):
            return "|".join(keycode)
        return keycode

    @staticmethod
    def modifier_suffix(modifiers: Sequence[str]) -> str:
        """'_KEY_LEFTCTRL_KEY_LEFTSHIFT' for sorted held modifiers, or ''."""
        if not modifiers:
            return ""
        return "_" + "_".join(modifiers)
