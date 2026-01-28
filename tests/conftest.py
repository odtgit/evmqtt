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
def valid_auto_discover_config_dict() -> dict[str, object]:
    """Return a valid configuration dictionary with auto_discover enabled."""
    return {
        "serverip": "192.168.1.100",
        "port": 1883,
        "username": "test_user",
        "password": "test_pass",
        "name": "Test Gateway",
        "topic": "homeassistant/sensor/evmqtt",
        "auto_discover": True,
        "filter_keys_only": True,
        "devices": [],
        "enabled_devices": [],
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
        "auto_discover": False,
        "log_level": "info",
    }


@pytest.fixture
def valid_ha_options_auto_discover() -> dict[str, object]:
    """Return valid Home Assistant add-on options with auto_discover."""
    return {
        "mqtt_host": "homeassistant.local",
        "mqtt_port": 1883,
        "mqtt_username": "ha_user",
        "mqtt_password": "ha_pass",
        "name": "Input Events",
        "topic": "homeassistant/sensor/evmqtt",
        "auto_discover": True,
        "filter_keys_only": True,
        "enabled_devices": [],
        "log_level": "info",
    }


@pytest.fixture
def config_from_dict(valid_config_dict: dict[str, object]) -> Config:
    """Create a Config instance from the valid config dictionary."""
    return Config.from_dict(valid_config_dict)


@pytest.fixture
def config_auto_discover(valid_auto_discover_config_dict: dict[str, object]) -> Config:
    """Create a Config instance with auto_discover enabled."""
    return Config.from_dict(valid_auto_discover_config_dict)
