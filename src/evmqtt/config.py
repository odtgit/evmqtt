"""Configuration handling for evmqtt."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class Config:
    """Configuration for the MQTT gateway.

    Attributes:
        serverip: MQTT broker IP address or hostname.
        port: MQTT broker port number.
        username: MQTT authentication username.
        password: MQTT authentication password.
        name: Display name for the gateway in Home Assistant.
        topic: Base MQTT topic for publishing events.
        devices: List of input device paths to monitor (used when auto_discover=False).
        auto_discover: If True, automatically discover all input devices.
        enabled_devices: List of device paths that are enabled when auto-discovering.
            If empty and auto_discover is True, all devices start enabled.
        filter_keys_only: When auto-discovering, only include devices with key capabilities.
    """

    serverip: str
    port: int
    username: str
    password: str
    name: str
    topic: str
    devices: list[str] = field(default_factory=list)
    auto_discover: bool = False
    enabled_devices: list[str] = field(default_factory=list)
    filter_keys_only: bool = True

    def __post_init__(self) -> None:
        """Validate configuration after initialization."""
        if not self.serverip:
            raise ValueError("serverip cannot be empty")
        if not 1 <= self.port <= 65535:
            raise ValueError(f"port must be between 1 and 65535, got {self.port}")
        if not self.topic:
            raise ValueError("topic cannot be empty")
        # When auto_discover is False, require at least one device
        if not self.auto_discover and not self.devices:
            raise ValueError(
                "at least one device must be specified when auto_discover is disabled"
            )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Config:
        """Create a Config instance from a dictionary.

        Args:
            data: Dictionary containing configuration values.

        Returns:
            Config instance with validated values.

        Raises:
            KeyError: If required fields are missing.
            ValueError: If field values are invalid.
        """
        return cls(
            serverip=data["serverip"],
            port=data["port"],
            username=data["username"],
            password=data["password"],
            name=data["name"],
            topic=data["topic"],
            devices=data.get("devices", []),
            auto_discover=data.get("auto_discover", False),
            enabled_devices=data.get("enabled_devices", []),
            filter_keys_only=data.get("filter_keys_only", True),
        )

    @classmethod
    def from_ha_options(cls, options: dict[str, Any]) -> Config:
        """Create a Config instance from Home Assistant add-on options.

        Transforms HA add-on options format to internal config format.

        Args:
            options: Home Assistant add-on options dictionary.

        Returns:
            Config instance with validated values.
        """
        # HA add-on uses mqtt_host instead of serverip, etc.
        return cls(
            serverip=options.get("mqtt_host", options.get("serverip", "")),
            port=options.get("mqtt_port", options.get("port", 1883)),
            username=options.get("mqtt_username", options.get("username", "")),
            password=options.get("mqtt_password", options.get("password", "")),
            name=options.get("name", "evmqtt"),
            topic=options.get("topic", "homeassistant/sensor/evmqtt"),
            devices=options.get("devices", []),
            auto_discover=options.get("auto_discover", False),
            enabled_devices=options.get("enabled_devices", []),
            filter_keys_only=options.get("filter_keys_only", True),
        )

    @classmethod
    def load(cls, config_path: str | Path | None = None) -> Config:
        """Load configuration from a JSON file.

        Searches for configuration in the following order:
        1. Provided config_path
        2. EVMQTT_CONFIG environment variable
        3. Home Assistant add-on options (/data/options.json)
        4. config.local.json in current directory
        5. config.json in current directory

        Args:
            config_path: Optional explicit path to configuration file.

        Returns:
            Config instance loaded from file.

        Raises:
            FileNotFoundError: If no configuration file is found.
            json.JSONDecodeError: If configuration file is invalid JSON.
            KeyError: If required fields are missing.
            ValueError: If field values are invalid.
        """
        ha_options_path = Path("/data/options.json")
        is_ha_addon = False

        if config_path is not None:
            path = Path(config_path)
        elif env_path := os.environ.get("EVMQTT_CONFIG"):
            path = Path(env_path)
        elif ha_options_path.is_file():
            # Running as Home Assistant add-on
            path = ha_options_path
            is_ha_addon = True
        elif Path("config.local.json").is_file():
            path = Path("config.local.json")
        elif Path("config.json").is_file():
            path = Path("config.json")
        else:
            raise FileNotFoundError(
                "No configuration file found. "
                "Create config.json or set EVMQTT_CONFIG environment variable."
            )

        logger.info("Loading configuration from '%s'", path)

        with open(path, encoding="utf-8") as f:
            data = json.load(f)

        if is_ha_addon:
            return cls.from_ha_options(data)
        return cls.from_dict(data)
