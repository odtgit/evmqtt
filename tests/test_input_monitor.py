"""Tests for evmqtt.input_monitor module."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from evmqtt.input_monitor import InputMonitor, list_available_devices
from evmqtt.key_handler import KeyHandler


class TestInputMonitor:
    """Tests for the InputMonitor class."""

    @patch("evmqtt.input_monitor.evdev.InputDevice")
    def test_initialization(self, mock_input_device: MagicMock) -> None:
        """Test InputMonitor initialization."""
        mock_device = MagicMock()
        mock_device.path = "/dev/input/event0"
        mock_device.name = "Test Keyboard"
        mock_input_device.return_value = mock_device

        mock_mqtt_client = MagicMock()

        monitor = InputMonitor(
            mqtt_client=mock_mqtt_client,
            device_path="/dev/input/event0",
            base_topic="homeassistant/sensor/evmqtt",
            gateway_name="Test Gateway",
        )

        assert monitor.device == mock_device
        assert monitor.state_topic == "homeassistant/sensor/evmqtt/state"
        assert monitor.config_topic == "homeassistant/sensor/evmqtt/config"
        mock_input_device.assert_called_once_with("/dev/input/event0")

    @patch("evmqtt.input_monitor.evdev.InputDevice")
    def test_autodiscovery_published(self, mock_input_device: MagicMock) -> None:
        """Test that autodiscovery config is published on init."""
        mock_device = MagicMock()
        mock_device.path = "/dev/input/event0"
        mock_device.name = "Test Keyboard"
        mock_input_device.return_value = mock_device

        mock_mqtt_client = MagicMock()

        InputMonitor(
            mqtt_client=mock_mqtt_client,
            device_path="/dev/input/event0",
            base_topic="homeassistant/sensor/evmqtt",
            gateway_name="Test Gateway",
        )

        # Verify autodiscovery was published
        mock_mqtt_client.publish.assert_called_once()
        call_args = mock_mqtt_client.publish.call_args
        assert call_args[0][0] == "homeassistant/sensor/evmqtt/config"

        # Verify config payload structure
        config_payload = json.loads(call_args[0][1])
        assert config_payload["name"] == "Test Gateway"
        assert config_payload["state_topic"] == "homeassistant/sensor/evmqtt/state"
        assert "unique_id" in config_payload

    @patch("evmqtt.input_monitor.evdev.InputDevice")
    def test_shared_key_handler(self, mock_input_device: MagicMock) -> None:
        """Test that a shared KeyHandler can be used."""
        mock_device = MagicMock()
        mock_device.path = "/dev/input/event0"
        mock_device.name = "Test Keyboard"
        mock_input_device.return_value = mock_device

        mock_mqtt_client = MagicMock()
        shared_handler = KeyHandler()

        monitor = InputMonitor(
            mqtt_client=mock_mqtt_client,
            device_path="/dev/input/event0",
            base_topic="test/topic",
            gateway_name="Test",
            key_handler=shared_handler,
        )

        assert monitor._key_handler is shared_handler

    @patch("evmqtt.input_monitor.evdev.InputDevice")
    def test_stop_method(self, mock_input_device: MagicMock) -> None:
        """Test that stop() signals the monitor to stop."""
        mock_device = MagicMock()
        mock_device.path = "/dev/input/event0"
        mock_device.name = "Test Keyboard"
        mock_input_device.return_value = mock_device

        mock_mqtt_client = MagicMock()

        monitor = InputMonitor(
            mqtt_client=mock_mqtt_client,
            device_path="/dev/input/event0",
            base_topic="test/topic",
            gateway_name="Test",
        )

        assert monitor._stop_event.is_set() is False
        monitor.stop()
        assert monitor._stop_event.is_set() is True

    @patch("evmqtt.input_monitor.evdev.InputDevice")
    def test_daemon_thread(self, mock_input_device: MagicMock) -> None:
        """Test that InputMonitor runs as a daemon thread."""
        mock_device = MagicMock()
        mock_device.path = "/dev/input/event0"
        mock_device.name = "Test Keyboard"
        mock_input_device.return_value = mock_device

        mock_mqtt_client = MagicMock()

        monitor = InputMonitor(
            mqtt_client=mock_mqtt_client,
            device_path="/dev/input/event0",
            base_topic="test/topic",
            gateway_name="Test",
        )

        assert monitor.daemon is True

    @patch("evmqtt.input_monitor.evdev.InputDevice")
    def test_handle_key_event_regular_key(self, mock_input_device: MagicMock) -> None:
        """Test handling a regular key press event."""
        mock_device = MagicMock()
        mock_device.path = "/dev/input/event0"
        mock_device.name = "Test Keyboard"
        mock_input_device.return_value = mock_device

        mock_mqtt_client = MagicMock()

        monitor = InputMonitor(
            mqtt_client=mock_mqtt_client,
            device_path="/dev/input/event0",
            base_topic="test/topic",
            gateway_name="Test",
        )

        # Reset mock to clear autodiscovery publish
        mock_mqtt_client.reset_mock()

        # Create mock event
        mock_event = MagicMock()

        with patch("evmqtt.input_monitor.evdev.categorize") as mock_categorize:
            mock_key_event = MagicMock()
            mock_key_event.keycode = "KEY_A"
            mock_key_event.keystate = 1  # Key press
            mock_categorize.return_value = mock_key_event

            monitor._handle_key_event(mock_event)

        # Verify message was published
        mock_mqtt_client.publish.assert_called_once()
        call_args = mock_mqtt_client.publish.call_args
        assert call_args[0][0] == "test/topic/state"

        payload = json.loads(call_args[0][1])
        assert payload["key"] == "KEY_A"
        assert payload["devicePath"] == "/dev/input/event0"

    @patch("evmqtt.input_monitor.evdev.InputDevice")
    def test_handle_key_event_modifier_not_published(
        self, mock_input_device: MagicMock
    ) -> None:
        """Test that modifier key presses are not published."""
        mock_device = MagicMock()
        mock_device.path = "/dev/input/event0"
        mock_device.name = "Test Keyboard"
        mock_input_device.return_value = mock_device

        mock_mqtt_client = MagicMock()

        monitor = InputMonitor(
            mqtt_client=mock_mqtt_client,
            device_path="/dev/input/event0",
            base_topic="test/topic",
            gateway_name="Test",
        )

        # Reset mock to clear autodiscovery publish
        mock_mqtt_client.reset_mock()

        mock_event = MagicMock()

        with patch("evmqtt.input_monitor.evdev.categorize") as mock_categorize:
            mock_key_event = MagicMock()
            mock_key_event.keycode = "KEY_LEFTSHIFT"
            mock_key_event.keystate = 1
            mock_categorize.return_value = mock_key_event

            monitor._handle_key_event(mock_event)

        # Modifier should not trigger publish
        mock_mqtt_client.publish.assert_not_called()

    @patch("evmqtt.input_monitor.evdev.InputDevice")
    def test_handle_key_event_with_modifier(
        self, mock_input_device: MagicMock
    ) -> None:
        """Test that key presses with modifiers include modifier suffix."""
        mock_device = MagicMock()
        mock_device.path = "/dev/input/event0"
        mock_device.name = "Test Keyboard"
        mock_input_device.return_value = mock_device

        mock_mqtt_client = MagicMock()

        monitor = InputMonitor(
            mqtt_client=mock_mqtt_client,
            device_path="/dev/input/event0",
            base_topic="test/topic",
            gateway_name="Test",
        )

        # Reset mock to clear autodiscovery publish
        mock_mqtt_client.reset_mock()

        # First press shift
        mock_event1 = MagicMock()
        with patch("evmqtt.input_monitor.evdev.categorize") as mock_categorize:
            mock_key_event = MagicMock()
            mock_key_event.keycode = "KEY_LEFTSHIFT"
            mock_key_event.keystate = 1
            mock_categorize.return_value = mock_key_event
            monitor._handle_key_event(mock_event1)

        # Then press A (with shift held)
        mock_event2 = MagicMock()
        with patch("evmqtt.input_monitor.evdev.categorize") as mock_categorize:
            mock_key_event = MagicMock()
            mock_key_event.keycode = "KEY_A"
            mock_key_event.keystate = 1
            mock_categorize.return_value = mock_key_event
            monitor._handle_key_event(mock_event2)

        # Verify the published key includes modifier
        mock_mqtt_client.publish.assert_called_once()
        call_args = mock_mqtt_client.publish.call_args
        payload = json.loads(call_args[0][1])
        assert "KEY_A_KEY_LEFTSHIFT" == payload["key"]

    @patch("evmqtt.input_monitor.evdev.InputDevice")
    def test_handle_key_event_release_not_published(
        self, mock_input_device: MagicMock
    ) -> None:
        """Test that key release events are not published."""
        mock_device = MagicMock()
        mock_device.path = "/dev/input/event0"
        mock_device.name = "Test Keyboard"
        mock_input_device.return_value = mock_device

        mock_mqtt_client = MagicMock()

        monitor = InputMonitor(
            mqtt_client=mock_mqtt_client,
            device_path="/dev/input/event0",
            base_topic="test/topic",
            gateway_name="Test",
        )

        # Reset mock
        mock_mqtt_client.reset_mock()

        mock_event = MagicMock()

        with patch("evmqtt.input_monitor.evdev.categorize") as mock_categorize:
            mock_key_event = MagicMock()
            mock_key_event.keycode = "KEY_A"
            mock_key_event.keystate = 0  # Key release
            mock_categorize.return_value = mock_key_event

            monitor._handle_key_event(mock_event)

        mock_mqtt_client.publish.assert_not_called()


class TestListAvailableDevices:
    """Tests for the list_available_devices function."""

    @patch("evmqtt.input_monitor.evdev.list_devices")
    @patch("evmqtt.input_monitor.evdev.InputDevice")
    def test_list_devices(
        self, mock_input_device: MagicMock, mock_list_devices: MagicMock
    ) -> None:
        """Test listing available input devices."""
        mock_list_devices.return_value = ["/dev/input/event0", "/dev/input/event1"]

        mock_device0 = MagicMock()
        mock_device0.path = "/dev/input/event0"
        mock_device0.name = "Keyboard"

        mock_device1 = MagicMock()
        mock_device1.path = "/dev/input/event1"
        mock_device1.name = "Mouse"

        mock_input_device.side_effect = [mock_device0, mock_device1]

        devices = list_available_devices()

        assert len(devices) == 2
        assert devices[0] == {"path": "/dev/input/event0", "name": "Keyboard"}
        assert devices[1] == {"path": "/dev/input/event1", "name": "Mouse"}

    @patch("evmqtt.input_monitor.evdev.list_devices")
    @patch("evmqtt.input_monitor.evdev.InputDevice")
    def test_list_devices_with_error(
        self, mock_input_device: MagicMock, mock_list_devices: MagicMock
    ) -> None:
        """Test that devices with errors are skipped."""
        mock_list_devices.return_value = ["/dev/input/event0", "/dev/input/event1"]

        mock_device0 = MagicMock()
        mock_device0.path = "/dev/input/event0"
        mock_device0.name = "Keyboard"

        # Second device raises OSError
        mock_input_device.side_effect = [mock_device0, OSError("Permission denied")]

        devices = list_available_devices()

        assert len(devices) == 1
        assert devices[0] == {"path": "/dev/input/event0", "name": "Keyboard"}

    @patch("evmqtt.input_monitor.evdev.list_devices")
    def test_list_devices_empty(self, mock_list_devices: MagicMock) -> None:
        """Test listing when no devices are available."""
        mock_list_devices.return_value = []

        devices = list_available_devices()

        assert devices == []
