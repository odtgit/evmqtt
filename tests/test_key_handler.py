"""Tests for evmqtt.key_handler module."""

import pytest

from evmqtt.key_handler import KeyHandler


class TestKeyHandler:
    """Tests for the KeyHandler class."""

    def test_default_modifiers(self) -> None:
        """Test that default modifiers are set correctly."""
        handler = KeyHandler()

        assert "KEY_LEFTSHIFT" in handler.modifiers
        assert "KEY_RIGHTSHIFT" in handler.modifiers
        assert "KEY_LEFTCTRL" in handler.modifiers
        assert "KEY_RIGHTCTRL" in handler.modifiers
        assert "KEY_LEFTALT" in handler.modifiers
        assert "KEY_RIGHTALT" in handler.modifiers

    def test_default_ignored_keys(self) -> None:
        """Test that default ignored keys are set correctly."""
        handler = KeyHandler()

        assert "KEY_NUMLOCK" in handler.ignored_keys

    def test_is_modifier(self) -> None:
        """Test is_modifier method."""
        handler = KeyHandler()

        assert handler.is_modifier("KEY_LEFTSHIFT") is True
        assert handler.is_modifier("KEY_A") is False
        assert handler.is_modifier("KEY_ENTER") is False

    def test_is_ignored(self) -> None:
        """Test is_ignored method."""
        handler = KeyHandler()

        assert handler.is_ignored("KEY_NUMLOCK") is True
        assert handler.is_ignored("KEY_A") is False

    def test_update_modifier_state(self) -> None:
        """Test updating modifier key state."""
        handler = KeyHandler()

        # Press left shift
        handler.update_modifier_state("KEY_LEFTSHIFT", 1)
        assert handler.get_active_modifiers() == ["KEY_LEFTSHIFT"]

        # Release left shift
        handler.update_modifier_state("KEY_LEFTSHIFT", 0)
        assert handler.get_active_modifiers() == []

    def test_multiple_modifiers(self) -> None:
        """Test tracking multiple modifier keys."""
        handler = KeyHandler()

        handler.update_modifier_state("KEY_LEFTSHIFT", 1)
        handler.update_modifier_state("KEY_LEFTCTRL", 1)

        active = handler.get_active_modifiers()
        assert len(active) == 2
        assert "KEY_LEFTSHIFT" in active
        assert "KEY_LEFTCTRL" in active

    def test_non_modifier_not_tracked(self) -> None:
        """Test that non-modifier keys are not tracked."""
        handler = KeyHandler()

        handler.update_modifier_state("KEY_A", 1)
        assert handler.get_active_modifiers() == []

    def test_get_modifier_suffix_empty(self) -> None:
        """Test modifier suffix when no modifiers are active."""
        handler = KeyHandler()

        assert handler.get_modifier_suffix() == ""

    def test_get_modifier_suffix_with_modifiers(self) -> None:
        """Test modifier suffix with active modifiers."""
        handler = KeyHandler()

        handler.update_modifier_state("KEY_LEFTSHIFT", 1)
        suffix = handler.get_modifier_suffix()
        assert suffix == "_KEY_LEFTSHIFT"

        handler.update_modifier_state("KEY_LEFTCTRL", 1)
        suffix = handler.get_modifier_suffix()
        # Should be sorted
        assert "_KEY_LEFTCTRL" in suffix
        assert "_KEY_LEFTSHIFT" in suffix

    def test_should_publish_key_press(self) -> None:
        """Test should_publish for regular key press."""
        handler = KeyHandler()

        # Key press (state=1) should publish
        assert handler.should_publish("KEY_A", 1) is True

        # Key release (state=0) should not publish
        assert handler.should_publish("KEY_A", 0) is False

        # Key repeat (state=2) should not publish
        assert handler.should_publish("KEY_A", 2) is False

    def test_should_publish_modifier(self) -> None:
        """Test that modifier keys are not published."""
        handler = KeyHandler()

        assert handler.should_publish("KEY_LEFTSHIFT", 1) is False
        assert handler.should_publish("KEY_LEFTCTRL", 1) is False

    def test_should_publish_ignored(self) -> None:
        """Test that ignored keys are not published."""
        handler = KeyHandler()

        assert handler.should_publish("KEY_NUMLOCK", 1) is False

    def test_should_publish_list_keycode(self) -> None:
        """Test should_publish with list of keycodes."""
        handler = KeyHandler()

        # List with regular key
        assert handler.should_publish(["KEY_A", "KEY_B"], 1) is True

        # List with modifier first
        assert handler.should_publish(["KEY_LEFTSHIFT", "KEY_A"], 1) is False

    def test_format_keycode_string(self) -> None:
        """Test formatting a single keycode string."""
        assert KeyHandler.format_keycode("KEY_A") == "KEY_A"

    def test_format_keycode_list(self) -> None:
        """Test formatting a list of keycodes."""
        assert KeyHandler.format_keycode(["KEY_A", "KEY_B"]) == "KEY_A|KEY_B"

    def test_custom_modifiers(self) -> None:
        """Test using custom modifier keys."""
        custom_modifiers = {"KEY_CUSTOM1", "KEY_CUSTOM2"}
        handler = KeyHandler(modifiers=custom_modifiers)

        assert handler.is_modifier("KEY_CUSTOM1") is True
        assert handler.is_modifier("KEY_LEFTSHIFT") is False

    def test_custom_ignored_keys(self) -> None:
        """Test using custom ignored keys."""
        custom_ignored = {"KEY_CAPSLOCK", "KEY_SCROLLLOCK"}
        handler = KeyHandler(ignored_keys=custom_ignored)

        assert handler.is_ignored("KEY_CAPSLOCK") is True
        assert handler.is_ignored("KEY_NUMLOCK") is False
