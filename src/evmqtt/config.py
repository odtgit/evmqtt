"""Configuration handling for evmqtt."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from evmqtt.core import KeyState

logger = logging.getLogger(__name__)

HA_OPTIONS_PATH = Path("/data/options.json")

DEFAULT_KEYSTATES: frozenset[KeyState] = frozenset({KeyState.PRESS})

_KEYSTATE_BY_NAME: dict[str, KeyState] = {s.name: s for s in KeyState}
_KEYSTATE_BY_VALUE: dict[int, KeyState] = {s.value: s for s in KeyState}


def _parse_keystates(values: Any) -> frozenset[KeyState]:
    """Normalise config keystates to core KeyState members.

    Accepts KeyState, case-insensitive names ("press") or raw evdev values
    (1). None means "not configured": PRESS only.
    """
    if values is None:
        return DEFAULT_KEYSTATES

    normalized: set[KeyState] = set()
    for v in values:
        if isinstance(v, KeyState):
            normalized.add(v)
        elif isinstance(v, str):
            key = v.upper()
            if key not in _KEYSTATE_BY_NAME:
                raise ValueError(f"Invalid keystate: {v}")
            normalized.add(_KEYSTATE_BY_NAME[key])
        elif isinstance(v, int):
            if v not in _KEYSTATE_BY_VALUE:
                raise ValueError(f"Invalid keystate: {v}")
            normalized.add(_KEYSTATE_BY_VALUE[v])
        else:
            raise TypeError(f"Invalid keystate type: {type(v).__name__}")

    if not normalized:
        raise ValueError("keystates cannot be empty")
    return frozenset(normalized)


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
        keystates: Key states to publish (default: PRESS only).
    """

    serverip: str
    port: int
    username: str
    password: str
    name: str
    topic: str
    keystates: frozenset[KeyState] = field(default_factory=lambda: DEFAULT_KEYSTATES)
    devices: list[str] = field(default_factory=list)
    auto_discover: bool = False
    enabled_devices: list[str] = field(default_factory=list)
    filter_keys_only: bool = True
    tls: bool = False
    tls_ca: str = ""

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
        tls = data.get("tls", False)
        tls_ca = data.get("tls_ca", "")
        return cls(
            serverip=data["serverip"],
            port=data.get("port", 8883 if tls or tls_ca else 1883),
            username=data.get("username", ""),
            password=data.get("password", ""),
            tls=tls,
            tls_ca=tls_ca,
            name=data["name"],
            topic=data["topic"],
            devices=data.get("devices", []),
            auto_discover=data.get("auto_discover", False),
            enabled_devices=data.get("enabled_devices", []),
            filter_keys_only=data.get("filter_keys_only", True),
            keystates=_parse_keystates(data.get("keystates")),
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
        tls = options.get("mqtt_tls", options.get("tls", False))
        tls_ca = options.get("mqtt_tls_ca", options.get("tls_ca", ""))
        return cls(
            serverip=options.get("mqtt_host", options.get("serverip", "")),
            port=options.get(
                "mqtt_port", options.get("port", 8883 if tls or tls_ca else 1883)
            ),
            username=options.get("mqtt_username", options.get("username", "")),
            password=options.get("mqtt_password", options.get("password", "")),
            tls=tls,
            tls_ca=tls_ca,
            name=options.get("name", "evmqtt"),
            topic=options.get("topic", "homeassistant/sensor/evmqtt"),
            devices=options.get("devices", []),
            auto_discover=options.get("auto_discover", False),
            enabled_devices=options.get("enabled_devices", []),
            filter_keys_only=options.get("filter_keys_only", True),
            keystates=_parse_keystates(options.get("keystates")),
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
        is_ha_addon = False

        if config_path is not None:
            path = Path(config_path)
        elif env_path := os.environ.get("EVMQTT_CONFIG"):
            path = Path(env_path)
        elif HA_OPTIONS_PATH.is_file():
            # Running as Home Assistant add-on
            path = HA_OPTIONS_PATH
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
