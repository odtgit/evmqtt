"""Shared pytest fixtures for evmqtt tests."""

from __future__ import annotations

import pytest

from evmqtt.config import Config


@pytest.fixture
def valid_config_dict() -> dict[str, object]:
    """Return a valid configuration dictionary."""
    return {
        "serverip": "192.168.1.100",
        "port": 1883,
        "username": "test_user",
        "password": "test_pass",
        "name": "Test Gateway",
        "topic": "homeassistant/sensor/evmqtt",
        "devices": ["/dev/input/event0"],
    }


@pytest.fixture
def valid_ha_options() -> dict[str, object]:
    """Return valid Home Assistant add-on options."""
    return {
        "mqtt_host": "homeassistant.local",
        "mqtt_port": 1883,
        "mqtt_username": "ha_user",
        "mqtt_password": "ha_pass",
        "name": "Input Events",
        "topic": "homeassistant/sensor/evmqtt",
        "devices": ["/dev/input/event0", "/dev/input/event1"],
        "log_level": "info",
    }


@pytest.fixture
def config_from_dict(valid_config_dict: dict[str, object]) -> Config:
    """Create a Config instance from the valid config dictionary."""
    return Config.from_dict(valid_config_dict)
