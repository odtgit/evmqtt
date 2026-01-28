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

    def test_validation_empty_devices(self) -> None:
        """Test that empty devices list raises ValueError."""
        with pytest.raises(ValueError, match="at least one device"):
            Config(
                serverip="localhost",
                port=1883,
                username="user",
                password="pass",
                name="Test",
                topic="test/topic",
                devices=[],
            )

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
