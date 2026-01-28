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
        devices: List of input device paths to monitor.
    """

    serverip: str
    port: int
    username: str
    password: str
    name: str
    topic: str
    devices: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        """Validate configuration after initialization."""
        if not self.serverip:
            raise ValueError("serverip cannot be empty")
        if not 1 <= self.port <= 65535:
            raise ValueError(f"port must be between 1 and 65535, got {self.port}")
        if not self.topic:
            raise ValueError("topic cannot be empty")
        if not self.devices:
            raise ValueError("at least one device must be specified")

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
            devices=data["devices"],
        )

    @classmethod
    def load(cls, config_path: str | Path | None = None) -> Config:
        """Load configuration from a JSON file.

        Searches for configuration in the following order:
        1. Provided config_path
        2. EVMQTT_CONFIG environment variable
        3. config.local.json in current directory
        4. config.json in current directory

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
        if config_path is not None:
            path = Path(config_path)
        elif env_path := os.environ.get("EVMQTT_CONFIG"):
            path = Path(env_path)
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

        return cls.from_dict(data)
