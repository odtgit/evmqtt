"""Tests for evmqtt.config module."""

import json
import tempfile
from pathlib import Path

import pytest

from evmqtt.config import Config


class TestConfig:
    """Tests for the Config class."""

    def test_from_dict_valid(self) -> None:
        """Test creating Config from a valid dictionary."""
        data = {
            "serverip": "192.168.1.100",
            "port": 1883,
            "username": "user",
            "password": "pass",
            "name": "Test Gateway",
            "topic": "homeassistant/sensor/test",
            "devices": ["/dev/input/event0"],
        }
        config = Config.from_dict(data)

        assert config.serverip == "192.168.1.100"
        assert config.port == 1883
        assert config.username == "user"
        assert config.password == "pass"
        assert config.name == "Test Gateway"
        assert config.topic == "homeassistant/sensor/test"
        assert config.devices == ["/dev/input/event0"]
        assert config.auto_discover is False  # Default

    def test_from_dict_with_auto_discover(self) -> None:
        """Test creating Config with auto_discover enabled."""
        data = {
            "serverip": "192.168.1.100",
            "port": 1883,
            "username": "user",
            "password": "pass",
            "name": "Test Gateway",
            "topic": "homeassistant/sensor/test",
            "auto_discover": True,
            "filter_keys_only": True,
            "devices": [],
            "enabled_devices": ["/dev/input/event0"],
        }
        config = Config.from_dict(data)

        assert config.auto_discover is True
        assert config.filter_keys_only is True
        assert config.devices == []
        assert config.enabled_devices == ["/dev/input/event0"]

    def test_from_dict_missing_field(self) -> None:
        """Test that missing required fields raise KeyError."""
        data = {
            "serverip": "192.168.1.100",
            "port": 1883,
            # Missing other required fields
        }
        with pytest.raises(KeyError):
            Config.from_dict(data)

    def test_validation_empty_serverip(self) -> None:
        """Test that empty serverip raises ValueError."""
        with pytest.raises(ValueError, match="serverip cannot be empty"):
            Config(
                serverip="",
                port=1883,
                username="user",
                password="pass",
                name="Test",
                topic="test/topic",
                devices=["/dev/input/event0"],
            )

    def test_validation_invalid_port(self) -> None:
        """Test that invalid port raises ValueError."""
        with pytest.raises(ValueError, match="port must be between"):
            Config(
                serverip="localhost",
                port=0,
                username="user",
                password="pass",
                name="Test",
                topic="test/topic",
                devices=["/dev/input/event0"],
            )

        with pytest.raises(ValueError, match="port must be between"):
            Config(
                serverip="localhost",
                port=70000,
                username="user",
                password="pass",
                name="Test",
                topic="test/topic",
                devices=["/dev/input/event0"],
            )

    def test_validation_empty_topic(self) -> None:
        """Test that empty topic raises ValueError."""
        with pytest.raises(ValueError, match="topic cannot be empty"):
            Config(
                serverip="localhost",
                port=1883,
                username="user",
                password="pass",
                name="Test",
                topic="",
                devices=["/dev/input/event0"],
            )

    def test_validation_empty_devices_without_auto_discover(self) -> None:
        """Test that empty devices list raises ValueError when auto_discover is False."""
        with pytest.raises(ValueError, match="at least one device"):
            Config(
                serverip="localhost",
                port=1883,
                username="user",
                password="pass",
                name="Test",
                topic="test/topic",
                devices=[],
                auto_discover=False,
            )

    def test_validation_empty_devices_with_auto_discover(self) -> None:
        """Test that empty devices list is OK when auto_discover is True."""
        config = Config(
            serverip="localhost",
            port=1883,
            username="user",
            password="pass",
            name="Test",
            topic="test/topic",
            devices=[],
            auto_discover=True,
        )
        assert config.devices == []
        assert config.auto_discover is True

    def test_load_from_file(self) -> None:
        """Test loading configuration from a file."""
        data = {
            "serverip": "192.168.1.100",
            "port": 1883,
            "username": "user",
            "password": "pass",
            "name": "Test Gateway",
            "topic": "homeassistant/sensor/test",
            "devices": ["/dev/input/event0"],
        }

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as f:
            json.dump(data, f)
            temp_path = f.name

        try:
            config = Config.load(temp_path)
            assert config.serverip == "192.168.1.100"
            assert config.port == 1883
        finally:
            Path(temp_path).unlink()

    def test_load_file_not_found(self) -> None:
        """Test that FileNotFoundError is raised for missing file."""
        with pytest.raises(FileNotFoundError):
            Config.load("/nonexistent/path/config.json")

    def test_from_ha_options_standard_format(self) -> None:
        """Test creating Config from Home Assistant add-on options."""
        options = {
            "mqtt_host": "homeassistant.local",
            "mqtt_port": 1883,
            "mqtt_username": "ha_user",
            "mqtt_password": "ha_pass",
            "name": "Input Events",
            "topic": "homeassistant/sensor/evmqtt",
            "devices": ["/dev/input/event0", "/dev/input/event1"],
            "log_level": "info",
        }
        config = Config.from_ha_options(options)

        assert config.serverip == "homeassistant.local"
        assert config.port == 1883
        assert config.username == "ha_user"
        assert config.password == "ha_pass"
        assert config.name == "Input Events"
        assert config.topic == "homeassistant/sensor/evmqtt"
        assert config.devices == ["/dev/input/event0", "/dev/input/event1"]

    def test_from_ha_options_with_auto_discover(self) -> None:
        """Test creating Config from HA options with auto_discover."""
        options = {
            "mqtt_host": "homeassistant.local",
            "mqtt_port": 1883,
            "mqtt_username": "ha_user",
            "mqtt_password": "ha_pass",
            "name": "Input Events",
            "topic": "homeassistant/sensor/evmqtt",
            "auto_discover": True,
            "filter_keys_only": True,
            "enabled_devices": ["/dev/input/event0"],
            "log_level": "info",
        }
        config = Config.from_ha_options(options)

        assert config.auto_discover is True
        assert config.filter_keys_only is True
        assert config.enabled_devices == ["/dev/input/event0"]

    def test_from_ha_options_fallback_to_legacy_keys(self) -> None:
        """Test that HA options fallback to legacy config keys."""
        options = {
            "serverip": "192.168.1.100",
            "port": 1884,
            "username": "legacy_user",
            "password": "legacy_pass",
            "name": "Legacy Gateway",
            "topic": "legacy/topic",
            "devices": ["/dev/input/event2"],
        }
        config = Config.from_ha_options(options)

        assert config.serverip == "192.168.1.100"
        assert config.port == 1884
        assert config.username == "legacy_user"
        assert config.password == "legacy_pass"

    def test_from_ha_options_defaults(self) -> None:
        """Test that HA options use defaults for missing values."""
        options = {
            "mqtt_host": "localhost",
            "devices": ["/dev/input/event0"],
        }
        config = Config.from_ha_options(options)

        assert config.serverip == "localhost"
        assert config.port == 1883  # default
        assert config.username == ""  # default
        assert config.password == ""  # default
        assert config.name == "evmqtt"  # default
        assert config.topic == "homeassistant/sensor/evmqtt"  # default
        assert config.auto_discover is False  # default
        assert config.filter_keys_only is True  # default

    def test_load_from_env_variable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test loading configuration from EVMQTT_CONFIG environment variable."""
        data = {
            "serverip": "env-server.local",
            "port": 1883,
            "username": "env_user",
            "password": "env_pass",
            "name": "Env Gateway",
            "topic": "env/topic",
            "devices": ["/dev/input/event0"],
        }

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as f:
            json.dump(data, f)
            temp_path = f.name

        try:
            monkeypatch.setenv("EVMQTT_CONFIG", temp_path)
            config = Config.load()
            assert config.serverip == "env-server.local"
            assert config.name == "Env Gateway"
        finally:
            Path(temp_path).unlink()

    def test_valid_port_range(self) -> None:
        """Test that valid port numbers are accepted."""
        # Minimum valid port
        config = Config(
            serverip="localhost",
            port=1,
            username="",
            password="",
            name="Test",
            topic="test/topic",
            devices=["/dev/input/event0"],
        )
        assert config.port == 1

        # Maximum valid port
        config = Config(
            serverip="localhost",
            port=65535,
            username="",
            password="",
            name="Test",
            topic="test/topic",
            devices=["/dev/input/event0"],
        )
        assert config.port == 65535

    def test_multiple_devices(self) -> None:
        """Test configuration with multiple input devices."""
        config = Config(
            serverip="localhost",
            port=1883,
            username="",
            password="",
            name="Multi-device",
            topic="test/topic",
            devices=[
                "/dev/input/event0",
                "/dev/input/event1",
                "/dev/input/event2",
            ],
        )
        assert len(config.devices) == 3

    def test_enabled_devices_list(self) -> None:
        """Test configuration with enabled_devices list."""
        config = Config(
            serverip="localhost",
            port=1883,
            username="",
            password="",
            name="Auto-discover",
            topic="test/topic",
            devices=[],
            auto_discover=True,
            enabled_devices=["/dev/input/event0", "/dev/input/event2"],
        )
        assert config.enabled_devices == ["/dev/input/event0", "/dev/input/event2"]
        assert config.auto_discover is True
