"""Tests for evmqtt.mqtt_client module."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from evmqtt.config import Config
from evmqtt.mqtt_client import MQTTClientWrapper


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


class TestMQTTClientWrapper:
    """Tests for the MQTTClientWrapper class."""

    @patch("evmqtt.mqtt_client.mqtt.Client")
    def test_initialization(
        self, mock_mqtt_client: MagicMock, mock_config: Config
    ) -> None:
        """Test that MQTT client initializes correctly."""
        wrapper = MQTTClientWrapper("test_client_id", mock_config)

        assert wrapper.client_id == "test_client_id"
        mock_mqtt_client.assert_called_once()
        wrapper.client.username_pw_set.assert_called_once_with(
            "test_user", "test_pass"
        )

    @patch("evmqtt.mqtt_client.mqtt.Client")
    def test_connect(
        self, mock_mqtt_client: MagicMock, mock_config: Config
    ) -> None:
        """Test connecting to MQTT broker."""
        wrapper = MQTTClientWrapper("test_client_id", mock_config)
        wrapper.connect()

        wrapper.client.connect.assert_called_once_with("test.mqtt.local", 1883)
        wrapper.client.loop_start.assert_called_once()

    @patch("evmqtt.mqtt_client.mqtt.Client")
    def test_disconnect(
        self, mock_mqtt_client: MagicMock, mock_config: Config
    ) -> None:
        """Test disconnecting from MQTT broker."""
        wrapper = MQTTClientWrapper("test_client_id", mock_config)
        wrapper.disconnect()

        wrapper.client.loop_stop.assert_called_once()
        wrapper.client.disconnect.assert_called_once()

    @patch("evmqtt.mqtt_client.mqtt.Client")
    def test_publish(
        self, mock_mqtt_client: MagicMock, mock_config: Config
    ) -> None:
        """Test publishing messages."""
        wrapper = MQTTClientWrapper("test_client_id", mock_config)
        wrapper.publish("test/topic", '{"key": "value"}', qos=1, retain=True)

        wrapper.client.publish.assert_called_once_with(
            "test/topic", '{"key": "value"}', qos=1, retain=True
        )

    @patch("evmqtt.mqtt_client.mqtt.Client")
    def test_publish_defaults(
        self, mock_mqtt_client: MagicMock, mock_config: Config
    ) -> None:
        """Test publishing with default QoS and retain."""
        wrapper = MQTTClientWrapper("test_client_id", mock_config)
        wrapper.publish("test/topic", "payload")

        wrapper.client.publish.assert_called_once_with(
            "test/topic", "payload", qos=0, retain=False
        )

    @patch("evmqtt.mqtt_client.mqtt.Client")
    def test_is_connected_initial_state(
        self, mock_mqtt_client: MagicMock, mock_config: Config
    ) -> None:
        """Test that client is not connected initially."""
        wrapper = MQTTClientWrapper("test_client_id", mock_config)

        assert wrapper.is_connected is False

    @patch("evmqtt.mqtt_client.mqtt.Client")
    def test_context_manager(
        self, mock_mqtt_client: MagicMock, mock_config: Config
    ) -> None:
        """Test using MQTTClientWrapper as context manager."""
        with MQTTClientWrapper("test_client_id", mock_config) as wrapper:
            wrapper.client.connect.assert_called_once()
            wrapper.client.loop_start.assert_called_once()

        wrapper.client.loop_stop.assert_called_once()
        wrapper.client.disconnect.assert_called_once()

    @patch("evmqtt.mqtt_client.mqtt.Client")
    def test_on_connect_callback(
        self, mock_mqtt_client: MagicMock, mock_config: Config
    ) -> None:
        """Test that custom on_connect callback is called."""
        callback_called = []

        def custom_callback(*args: object) -> None:
            callback_called.append(True)

        wrapper = MQTTClientWrapper(
            "test_client_id", mock_config, on_connect_callback=custom_callback
        )

        # Simulate successful connection
        mock_reason_code = MagicMock()
        mock_reason_code.is_failure = False
        mock_flags = MagicMock()

        wrapper._on_connect(
            wrapper.client, None, mock_flags, mock_reason_code, None
        )

        assert len(callback_called) == 1
        assert wrapper.is_connected is True

    @patch("evmqtt.mqtt_client.mqtt.Client")
    def test_on_connect_failure(
        self, mock_mqtt_client: MagicMock, mock_config: Config
    ) -> None:
        """Test handling connection failure."""
        wrapper = MQTTClientWrapper("test_client_id", mock_config)

        # Simulate failed connection
        mock_reason_code = MagicMock()
        mock_reason_code.is_failure = True
        mock_flags = MagicMock()

        wrapper._on_connect(
            wrapper.client, None, mock_flags, mock_reason_code, None
        )

        assert wrapper.is_connected is False

    @patch("evmqtt.mqtt_client.mqtt.Client")
    def test_on_disconnect_callback(
        self, mock_mqtt_client: MagicMock, mock_config: Config
    ) -> None:
        """Test that custom on_disconnect callback is called."""
        callback_called = []

        def custom_callback(*args: object) -> None:
            callback_called.append(True)

        wrapper = MQTTClientWrapper(
            "test_client_id", mock_config, on_disconnect_callback=custom_callback
        )

        # First simulate connection
        mock_reason_code = MagicMock()
        mock_reason_code.is_failure = False
        mock_flags = MagicMock()
        wrapper._on_connect(wrapper.client, None, mock_flags, mock_reason_code, None)
        assert wrapper.is_connected is True

        # Then simulate disconnection
        wrapper._on_disconnect(
            wrapper.client, None, mock_flags, mock_reason_code, None
        )

        assert len(callback_called) == 1
        assert wrapper.is_connected is False

    @patch("evmqtt.mqtt_client.mqtt.Client")
    def test_wait_for_connection_timeout(
        self, mock_mqtt_client: MagicMock, mock_config: Config
    ) -> None:
        """Test wait_for_connection timeout."""
        wrapper = MQTTClientWrapper("test_client_id", mock_config)

        # Should return False immediately since not connected
        result = wrapper.wait_for_connection(timeout=0.1)
        assert result is False

    @patch("evmqtt.mqtt_client.mqtt.Client")
    def test_wait_for_connection_success(
        self, mock_mqtt_client: MagicMock, mock_config: Config
    ) -> None:
        """Test wait_for_connection when already connected."""
        wrapper = MQTTClientWrapper("test_client_id", mock_config)

        # Simulate successful connection
        mock_reason_code = MagicMock()
        mock_reason_code.is_failure = False
        mock_flags = MagicMock()
        wrapper._on_connect(wrapper.client, None, mock_flags, mock_reason_code, None)

        result = wrapper.wait_for_connection(timeout=0.1)
        assert result is True
