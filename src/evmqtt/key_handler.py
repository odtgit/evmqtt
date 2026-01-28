"""Key event handling and modifier tracking for evmqtt."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field


@dataclass
class KeyHandler:
    """Handles keyboard modifier key state tracking.

    This class maintains the state of modifier keys (Shift, Ctrl, etc.)
    and provides methods to check and format key combinations.

    Attributes:
        modifiers: Set of key codes considered modifier keys.
        ignored_keys: Set of key codes to ignore (e.g., NUMLOCK).
    """

    modifiers: set[str] = field(
        default_factory=lambda: {
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
    ignored_keys: set[str] = field(default_factory=lambda: {"KEY_NUMLOCK"})
    _key_state: dict[str, int] = field(default_factory=dict, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def update_modifier_state(self, keycode: str, keystate: int) -> None:
        """Update the state of a modifier key.

        Thread-safe method to track which modifier keys are currently pressed.

        Args:
            keycode: The key code string (e.g., "KEY_LEFTSHIFT").
            keystate: The key state (1 = pressed, 0 = released).
        """
        if keycode in self.modifiers:
            with self._lock:
                self._key_state[keycode] = keystate

    def get_active_modifiers(self) -> list[str]:
        """Get a sorted list of currently pressed modifier keys.

        Returns:
            Sorted list of modifier key codes that are currently pressed.
        """
        with self._lock:
            return sorted(key for key, state in self._key_state.items() if state == 1)

    def get_modifier_suffix(self) -> str:
        """Get a string suffix representing active modifiers.

        Returns:
            String like "_KEY_LEFTSHIFT_KEY_RIGHTCTRL" or empty string
            if no modifiers are active.
        """
        active = self.get_active_modifiers()
        if not active:
            return ""
        return "_" + "_".join(active)

    def is_modifier(self, keycode: str) -> bool:
        """Check if a key code is a modifier key.

        Args:
            keycode: The key code to check.

        Returns:
            True if the key is a modifier key.
        """
        return keycode in self.modifiers

    def is_ignored(self, keycode: str) -> bool:
        """Check if a key code should be ignored.

        Args:
            keycode: The key code to check.

        Returns:
            True if the key should be ignored.
        """
        return keycode in self.ignored_keys

    def should_publish(self, keycode: str | list[str], keystate: int) -> bool:
        """Determine if a key event should be published.

        Only key press events (keystate=1) for non-modifier, non-ignored
        keys should be published.

        Args:
            keycode: The key code (string or list of strings).
            keystate: The key state (1 = pressed, 0 = released).

        Returns:
            True if the event should be published.
        """
        if keystate != 1:  # Only publish key press, not release or repeat
            return False

        # Handle case where keycode is a list (multiple keys reported)
        primary_key = keycode[0] if isinstance(keycode, list) else keycode

        return not self.is_modifier(primary_key) and not self.is_ignored(primary_key)

    @staticmethod
    def format_keycode(keycode: str | list[str]) -> str:
        """Format a key code for publishing.

        Handles cases where the input device reports multiple key codes
        for a single key press.

        Args:
            keycode: Single key code string or list of key codes.

        Returns:
            Formatted key code string.
        """
        if isinstance(keycode, list):
            return "|".join(keycode)
        return keycode
