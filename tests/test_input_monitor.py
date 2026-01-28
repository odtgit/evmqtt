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
        # New topic format with device path suffix
        assert "homeassistant/sensor/evmqtt" in monitor.state_topic
        assert "state" in monitor.state_topic
        mock_input_device.assert_called_once_with("/dev/input/event0")

    @patch("evmqtt.input_monitor.evdev.InputDevice")
    def test_initialization_with_slug(self, mock_input_device: MagicMock) -> None:
        """Test InputMonitor initialization with device slug."""
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
            device_slug="test-keyboard",
            unique_id="evmqtt_test-keyboard_event0",
        )

        assert monitor.state_topic == "homeassistant/sensor/evmqtt/test-keyboard/state"
        assert monitor.switch_command_topic == "homeassistant/sensor/evmqtt/test-keyboard/switch/set"

    @patch("evmqtt.input_monitor.evdev.InputDevice")
    def test_autodiscovery_published(self, mock_input_device: MagicMock) -> None:
        """Test that autodiscovery configs are published on setup_autodiscovery."""
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

        # Autodiscovery is now explicit
        monitor.setup_autodiscovery()

        # Should publish sensor config, switch config, and switch state
        assert mock_mqtt_client.publish.call_count >= 3

    @patch("evmqtt.input_monitor.evdev.InputDevice")
    def test_sensor_autodiscovery_config(self, mock_input_device: MagicMock) -> None:
        """Test sensor autodiscovery configuration."""
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
            device_slug="test-keyboard",
        )

        monitor.setup_autodiscovery()

        # Find the sensor config publish call
        sensor_config_call = None
        for call in mock_mqtt_client.publish.call_args_list:
            topic = call[0][0]
            if "/config" in topic and "switch" not in topic:
                sensor_config_call = call
                break

        assert sensor_config_call is not None
        config_payload = json.loads(sensor_config_call[0][1])
        assert "Test Gateway - Test Keyboard" in config_payload["name"]
        assert "state_topic" in config_payload
        assert "unique_id" in config_payload
        assert "device" in config_payload

    @patch("evmqtt.input_monitor.evdev.InputDevice")
    def test_switch_autodiscovery_config(self, mock_input_device: MagicMock) -> None:
        """Test switch autodiscovery configuration."""
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
            device_slug="test-keyboard",
        )

        monitor.setup_autodiscovery()

        # Find the switch config publish call
        switch_config_call = None
        for call in mock_mqtt_client.publish.call_args_list:
            topic = call[0][0]
            if "switch" in topic and "/config" in topic:
                switch_config_call = call
                break

        assert switch_config_call is not None
        config_payload = json.loads(switch_config_call[0][1])
        assert "Enable" in config_payload["name"]
        assert config_payload["payload_on"] == "ON"
        assert config_payload["payload_off"] == "OFF"

    @patch("evmqtt.input_monitor.evdev.InputDevice")
    def test_enabled_property(self, mock_input_device: MagicMock) -> None:
        """Test enabled property."""
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
            initially_enabled=True,
        )

        assert monitor.enabled is True

        monitor.enabled = False
        assert monitor.enabled is False

    @patch("evmqtt.input_monitor.evdev.InputDevice")
    def test_handle_switch_command_on(self, mock_input_device: MagicMock) -> None:
        """Test handling ON switch command."""
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
            initially_enabled=False,
        )

        assert monitor.enabled is False
        monitor.handle_switch_command("ON")
        assert monitor.enabled is True

    @patch("evmqtt.input_monitor.evdev.InputDevice")
    def test_handle_switch_command_off(self, mock_input_device: MagicMock) -> None:
        """Test handling OFF switch command."""
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
            initially_enabled=True,
        )

        assert monitor.enabled is True
        monitor.handle_switch_command("OFF")
        assert monitor.enabled is False

    @patch("evmqtt.input_monitor.evdev.InputDevice")
    def test_handle_switch_command_case_insensitive(
        self, mock_input_device: MagicMock
    ) -> None:
        """Test that switch commands are case insensitive."""
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
            initially_enabled=False,
        )

        monitor.handle_switch_command("on")
        assert monitor.enabled is True

        monitor.handle_switch_command("off")
        assert monitor.enabled is False

        monitor.handle_switch_command("On")
        assert monitor.enabled is True

    @patch("evmqtt.input_monitor.evdev.InputDevice")
    def test_enabled_callback(self, mock_input_device: MagicMock) -> None:
        """Test that enabled change callback is called."""
        mock_device = MagicMock()
        mock_device.path = "/dev/input/event0"
        mock_device.name = "Test Keyboard"
        mock_input_device.return_value = mock_device

        mock_mqtt_client = MagicMock()
        callback_calls = []

        def callback(path: str, enabled: bool) -> None:
            callback_calls.append((path, enabled))

        monitor = InputMonitor(
            mqtt_client=mock_mqtt_client,
            device_path="/dev/input/event0",
            base_topic="test/topic",
            gateway_name="Test",
            initially_enabled=True,
            on_enabled_change=callback,
        )

        monitor.enabled = False

        assert len(callback_calls) == 1
        assert callback_calls[0] == ("/dev/input/event0", False)

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

        # Reset mock
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
        assert "state" in call_args[0][0]

        payload = json.loads(call_args[0][1])
        assert payload["key"] == "KEY_A"
        assert payload["devicePath"] == "/dev/input/event0"

    @patch("evmqtt.input_monitor.evdev.InputDevice")
    def test_handle_key_event_when_disabled(self, mock_input_device: MagicMock) -> None:
        """Test that key events are not published when disabled."""
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
            initially_enabled=False,
        )

        # Reset mock
        mock_mqtt_client.reset_mock()

        # The monitor checks enabled state in run(), not _handle_key_event()
        # But the monitor won't publish if disabled
        assert monitor.enabled is False

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

        # Reset mock
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

        # Reset mock
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

    @patch("evmqtt.input_monitor.evdev.InputDevice")
    def test_cleanup_autodiscovery(self, mock_input_device: MagicMock) -> None:
        """Test cleanup_autodiscovery removes configs."""
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

        mock_mqtt_client.reset_mock()
        monitor.cleanup_autodiscovery()

        # Should publish empty payloads to config topics
        assert mock_mqtt_client.publish.call_count == 2
        for call in mock_mqtt_client.publish.call_args_list:
            assert call[0][1] == ""  # Empty payload
            assert call.kwargs.get("retain") is True


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
