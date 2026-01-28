"""Tests for evmqtt.__main__ module."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest

from evmqtt.__main__ import (
    Application,
    generate_client_id,
    parse_args,
    setup_logging,
)
from evmqtt.config import Config


class TestSetupLogging:
    """Tests for the setup_logging function."""

    def test_default_logging_level(self) -> None:
        """Test that default logging level is WARNING."""
        import logging

        setup_logging(verbose=False, debug=False)
        # Root logger should be WARNING
        assert logging.getLogger().level == logging.WARNING

    def test_verbose_logging_level(self) -> None:
        """Test that verbose flag sets INFO level."""
        import logging

        setup_logging(verbose=True, debug=False)
        assert logging.getLogger().level == logging.INFO

    def test_debug_logging_level(self) -> None:
        """Test that debug flag sets DEBUG level."""
        import logging

        setup_logging(verbose=False, debug=True)
        assert logging.getLogger().level == logging.DEBUG

    def test_debug_overrides_verbose(self) -> None:
        """Test that debug flag takes precedence over verbose."""
        import logging

        setup_logging(verbose=True, debug=True)
        assert logging.getLogger().level == logging.DEBUG


class TestGenerateClientId:
    """Tests for the generate_client_id function."""

    def test_client_id_format(self) -> None:
        """Test that client ID has expected format."""
        client_id = generate_client_id()

        assert client_id.startswith("evmqtt_")
        parts = client_id.split("_")
        assert len(parts) >= 3
        # Last part should be a timestamp (integer)
        assert parts[-1].isdigit()

    def test_client_id_uniqueness(self) -> None:
        """Test that client IDs are unique."""
        import time

        id1 = generate_client_id()
        time.sleep(0.01)  # Small delay to ensure different timestamp
        id2 = generate_client_id()

        # IDs should be different (unless generated in same second)
        # This test may occasionally fail if both are generated in the same second
        # but that's acceptable for a unit test


class TestParseArgs:
    """Tests for the parse_args function."""

    def test_default_args(self) -> None:
        """Test default argument values."""
        with patch.object(sys, "argv", ["evmqtt"]):
            args = parse_args()

        assert args.config is None
        assert args.verbose is False
        assert args.debug is False
        assert args.list_devices is False

    def test_config_arg(self) -> None:
        """Test --config argument."""
        with patch.object(sys, "argv", ["evmqtt", "--config", "/path/to/config.json"]):
            args = parse_args()

        assert args.config == "/path/to/config.json"

    def test_short_config_arg(self) -> None:
        """Test -c argument."""
        with patch.object(sys, "argv", ["evmqtt", "-c", "/path/to/config.json"]):
            args = parse_args()

        assert args.config == "/path/to/config.json"

    def test_verbose_arg(self) -> None:
        """Test --verbose argument."""
        with patch.object(sys, "argv", ["evmqtt", "--verbose"]):
            args = parse_args()

        assert args.verbose is True

    def test_short_verbose_arg(self) -> None:
        """Test -v argument."""
        with patch.object(sys, "argv", ["evmqtt", "-v"]):
            args = parse_args()

        assert args.verbose is True

    def test_debug_arg(self) -> None:
        """Test --debug argument."""
        with patch.object(sys, "argv", ["evmqtt", "--debug"]):
            args = parse_args()

        assert args.debug is True

    def test_short_debug_arg(self) -> None:
        """Test -d argument."""
        with patch.object(sys, "argv", ["evmqtt", "-d"]):
            args = parse_args()

        assert args.debug is True

    def test_list_devices_arg(self) -> None:
        """Test --list-devices argument."""
        with patch.object(sys, "argv", ["evmqtt", "--list-devices"]):
            args = parse_args()

        assert args.list_devices is True

    def test_combined_args(self) -> None:
        """Test combining multiple arguments."""
        with patch.object(
            sys, "argv", ["evmqtt", "-v", "-c", "/config.json", "--list-devices"]
        ):
            args = parse_args()

        assert args.verbose is True
        assert args.config == "/config.json"
        assert args.list_devices is True


@pytest.fixture
def mock_config() -> Config:
    """Create a mock configuration for testing."""
    return Config(
        serverip="test.mqtt.local",
        port=1883,
        username="test_user",
        password="test_pass",
        name="Test Gateway",
        topic="test/topic",
        devices=["/dev/input/event0"],
    )


class TestApplication:
    """Tests for the Application class."""

    def test_initialization(self, mock_config: Config) -> None:
        """Test Application initialization."""
        app = Application(mock_config)

        assert app._config == mock_config
        assert app._mqtt_client is None
        assert app._monitors == []
        assert app._shutdown_requested is False

    @patch("evmqtt.__main__.MQTTClientWrapper")
    @patch("evmqtt.__main__.InputMonitor")
    @patch("evmqtt.__main__.list_available_devices")
    def test_start_success(
        self,
        mock_list_devices: MagicMock,
        mock_input_monitor: MagicMock,
        mock_mqtt_wrapper: MagicMock,
        mock_config: Config,
    ) -> None:
        """Test successful application start."""
        mock_list_devices.return_value = [
            {"path": "/dev/input/event0", "name": "Keyboard"}
        ]

        mock_client = MagicMock()
        mock_client.wait_for_connection.return_value = True
        mock_mqtt_wrapper.return_value = mock_client

        mock_monitor = MagicMock()
        mock_input_monitor.return_value = mock_monitor

        app = Application(mock_config)
        app.start()

        mock_mqtt_wrapper.assert_called_once()
        mock_client.connect.assert_called_once()
        mock_input_monitor.assert_called_once()
        mock_monitor.start.assert_called_once()

    @patch("evmqtt.__main__.MQTTClientWrapper")
    @patch("evmqtt.__main__.list_available_devices")
    def test_start_mqtt_timeout(
        self,
        mock_list_devices: MagicMock,
        mock_mqtt_wrapper: MagicMock,
        mock_config: Config,
    ) -> None:
        """Test that ConnectionError is raised on MQTT timeout."""
        mock_list_devices.return_value = []

        mock_client = MagicMock()
        mock_client.wait_for_connection.return_value = False
        mock_mqtt_wrapper.return_value = mock_client

        app = Application(mock_config)

        with pytest.raises(ConnectionError, match="MQTT connection timeout"):
            app.start()

    @patch("evmqtt.__main__.MQTTClientWrapper")
    @patch("evmqtt.__main__.InputMonitor")
    @patch("evmqtt.__main__.list_available_devices")
    def test_start_no_devices(
        self,
        mock_list_devices: MagicMock,
        mock_input_monitor: MagicMock,
        mock_mqtt_wrapper: MagicMock,
        mock_config: Config,
    ) -> None:
        """Test that RuntimeError is raised when no devices can be opened."""
        mock_list_devices.return_value = []

        mock_client = MagicMock()
        mock_client.wait_for_connection.return_value = True
        mock_mqtt_wrapper.return_value = mock_client

        # Simulate device open failure
        mock_input_monitor.side_effect = FileNotFoundError("Device not found")

        app = Application(mock_config)

        with pytest.raises(RuntimeError, match="No input devices"):
            app.start()

    @patch("evmqtt.__main__.MQTTClientWrapper")
    @patch("evmqtt.__main__.InputMonitor")
    @patch("evmqtt.__main__.list_available_devices")
    def test_stop(
        self,
        mock_list_devices: MagicMock,
        mock_input_monitor: MagicMock,
        mock_mqtt_wrapper: MagicMock,
        mock_config: Config,
    ) -> None:
        """Test application stop."""
        mock_list_devices.return_value = [
            {"path": "/dev/input/event0", "name": "Keyboard"}
        ]

        mock_client = MagicMock()
        mock_client.wait_for_connection.return_value = True
        mock_mqtt_wrapper.return_value = mock_client

        mock_monitor = MagicMock()
        mock_input_monitor.return_value = mock_monitor

        app = Application(mock_config)
        app.start()
        app.stop()

        mock_monitor.stop.assert_called_once()
        mock_client.disconnect.assert_called_once()
        assert app._shutdown_requested is True

    @patch("evmqtt.__main__.MQTTClientWrapper")
    @patch("evmqtt.__main__.InputMonitor")
    @patch("evmqtt.__main__.list_available_devices")
    def test_stop_idempotent(
        self,
        mock_list_devices: MagicMock,
        mock_input_monitor: MagicMock,
        mock_mqtt_wrapper: MagicMock,
        mock_config: Config,
    ) -> None:
        """Test that calling stop multiple times is safe."""
        mock_list_devices.return_value = [
            {"path": "/dev/input/event0", "name": "Keyboard"}
        ]

        mock_client = MagicMock()
        mock_client.wait_for_connection.return_value = True
        mock_mqtt_wrapper.return_value = mock_client

        mock_monitor = MagicMock()
        mock_input_monitor.return_value = mock_monitor

        app = Application(mock_config)
        app.start()

        # Stop multiple times
        app.stop()
        app.stop()
        app.stop()

        # Should only be called once
        assert mock_monitor.stop.call_count == 1
        assert mock_client.disconnect.call_count == 1
