"""Configuration for the evmqtt MQTT daemon."""

from __future__ import annotations

import json
import logging
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from platform import node as hostname
from typing import Any

from evmqtt.core import KeyState, slugify

logger = logging.getLogger(__name__)

HA_OPTIONS_PATH = Path("/data/options.json")
HA_STATE_FILE = Path("/data/evmqtt-state.json")

DEFAULT_KEYSTATES: frozenset[KeyState] = frozenset({KeyState.PRESS})
DEFAULT_DISCOVERY_PREFIX = "homeassistant"
DEFAULT_RESCAN_INTERVAL = 5.0
LEGACY_TOPIC = "homeassistant/sensor/evmqtt"
LOG_LEVELS = ("debug", "info", "warning", "error")

_KEYSTATE_BY_NAME: dict[str, KeyState] = {s.name: s for s in KeyState}
_KEYSTATE_BY_VALUE: dict[int, KeyState] = {s.value: s for s in KeyState}

# 1.x key -> 2.0 key
_ALIASES = {
    "serverip": "mqtt_host",
    "port": "mqtt_port",
    "username": "mqtt_username",
    "password": "mqtt_password",
    "tls": "mqtt_tls",
    "tls_ca": "mqtt_tls_ca",
}
_KNOWN = {
    "mqtt_host",
    "mqtt_port",
    "mqtt_username",
    "mqtt_password",
    "mqtt_tls",
    "mqtt_tls_ca",
    "name",
    "discovery_prefix",
    "base_topic",
    "topic",
    "auto_discover",
    "filter_keys_only",
    "keystates",
    "devices",
    "enabled_devices",
    "state_file",
    "rescan_interval",
    "cleanup_legacy",
    "log_level",
}
_TOPIC_SEGMENT = re.compile(r"^[^#+\0]+$")


class ConfigError(ValueError):
    pass


def parse_keystates(values: Any) -> frozenset[KeyState]:
    """KeyState members, case-insensitive names or evdev values. None: PRESS."""
    if values is None:
        return DEFAULT_KEYSTATES
    if isinstance(values, (str, bytes)) or not hasattr(values, "__iter__"):
        raise ConfigError(f"keystates must be a list, got {values!r}")
    normalized: set[KeyState] = set()
    for v in values:
        if isinstance(v, KeyState):
            normalized.add(v)
        elif isinstance(v, str) and v.upper() in _KEYSTATE_BY_NAME:
            normalized.add(_KEYSTATE_BY_NAME[v.upper()])
        elif isinstance(v, int) and not isinstance(v, bool) and v in _KEYSTATE_BY_VALUE:
            normalized.add(_KEYSTATE_BY_VALUE[v])
        else:
            raise ConfigError(f"Invalid keystate: {v!r}")
    if not normalized:
        raise ConfigError("keystates cannot be empty")
    return frozenset(normalized)


def host_slug() -> str:
    return slugify(hostname() or "host")


def default_base_topic() -> str:
    return f"evmqtt/{host_slug()}"


def node_id(base_topic: str) -> str:
    """Gateway id from base_topic: 'evmqtt/my-host' -> 'my-host'."""
    slug = slugify(base_topic.replace("/", "-"))
    if slug.startswith("evmqtt-") and len(slug) > len("evmqtt-"):
        slug = slug[len("evmqtt-") :]
    return slug


def _check_topic(name: str, value: str) -> str:
    value = value.strip().strip("/")
    if not value or not all(_TOPIC_SEGMENT.match(p) for p in value.split("/")):
        raise ConfigError(f"{name} is not a valid MQTT topic: {value!r}")
    return value


def _str_list(name: str, value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str) or not isinstance(value, list):
        raise ConfigError(f"{name} must be a list of strings")
    return tuple(str(v) for v in value if v not in (None, ""))


def _bool(name: str, value: Any) -> bool:
    if not isinstance(value, bool):
        raise ConfigError(f"{name} must be true or false, got {value!r}")
    return value


def _opt_str(value: Any) -> str | None:
    if value is None or value == "":
        return None
    return str(value)


@dataclass(frozen=True)
class Config:
    """Daemon configuration. mqtt_* None means unset (Supervisor may fill it)."""

    mqtt_host: str | None = None
    mqtt_port: int | None = None
    mqtt_username: str | None = None
    mqtt_password: str | None = None
    mqtt_tls: bool | None = None
    mqtt_tls_ca: str | None = None
    name: str = ""
    discovery_prefix: str = DEFAULT_DISCOVERY_PREFIX
    base_topic: str = field(default_factory=default_base_topic)
    legacy_topic: str = LEGACY_TOPIC
    auto_discover: bool = True
    keystates: frozenset[KeyState] = DEFAULT_KEYSTATES
    devices: tuple[str, ...] = ()
    enabled_devices: tuple[str, ...] = ()
    state_file: Path | None = None
    rescan_interval: float = DEFAULT_RESCAN_INTERVAL
    cleanup_legacy: bool = True
    log_level: str = "info"
    addon: bool = False
    warnings: tuple[str, ...] = ()

    @property
    def node_id(self) -> str:
        return node_id(self.base_topic)

    @property
    def gateway_name(self) -> str:
        return self.name or f"evmqtt {hostname() or 'gateway'}"

    @property
    def state_path(self) -> Path:
        if self.state_file is not None:
            return self.state_file
        if self.addon:
            return HA_STATE_FILE
        state_dir = os.environ.get("STATE_DIRECTORY")
        if state_dir:
            return Path(state_dir.split(":")[0]) / "state.json"
        xdg = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
        return Path(xdg) / "evmqtt" / "state.json"

    @classmethod
    def from_dict(cls, data: Mapping[str, Any], *, addon: bool = False) -> Config:
        """Build from config.json or add-on options. Raises ConfigError."""
        if not isinstance(data, Mapping):
            raise ConfigError("configuration must be a JSON object")
        warnings: list[str] = []
        values: dict[str, Any] = {}
        for key, value in data.items():
            if key in _ALIASES:
                new = _ALIASES[key]
                if new in data:
                    warnings.append(f"'{key}' is ignored, '{new}' is set")
                    continue
                warnings.append(f"'{key}' is deprecated, use '{new}'")
                values[new] = value
            elif key in _KNOWN:
                values[key] = value
            else:
                warnings.append(f"unknown option '{key}' ignored")

        tls = _bool("mqtt_tls", values["mqtt_tls"]) if "mqtt_tls" in values else None
        tls_ca = _opt_str(values.get("mqtt_tls_ca"))
        port = values.get("mqtt_port")
        if port is not None:
            if isinstance(port, bool) or not isinstance(port, int):
                raise ConfigError(f"mqtt_port must be an integer, got {port!r}")
            if not 1 <= port <= 65535:
                raise ConfigError(f"mqtt_port must be between 1 and 65535, got {port}")

        prefix = _check_topic(
            "discovery_prefix",
            str(values.get("discovery_prefix") or DEFAULT_DISCOVERY_PREFIX),
        )
        legacy = _opt_str(values.get("topic"))
        base = _opt_str(values.get("base_topic"))
        if legacy is not None:
            legacy = _check_topic("topic", legacy)
            if base is None and not (legacy + "/").startswith(prefix + "/"):
                base = legacy
                warnings.append(
                    f"'topic' is deprecated, use 'base_topic'; using '{legacy}'"
                )
            else:
                warnings.append(
                    "'topic' is deprecated and only used to clean up 1.x "
                    f"discovery under '{legacy}'"
                )
        base = _check_topic("base_topic", base) if base else default_base_topic()
        if (base + "/").startswith(prefix + "/"):
            raise ConfigError(
                f"base_topic '{base}' must not be under discovery_prefix '{prefix}'"
            )

        if "filter_keys_only" in values:
            _bool("filter_keys_only", values["filter_keys_only"])
            warnings.append(
                "'filter_keys_only' is deprecated and ignored; keyboard-like "
                "devices are selected by default, list others in 'devices'"
            )

        auto = values.get("auto_discover", True)
        devices = _str_list("devices", values.get("devices"))
        if not _bool("auto_discover", auto) and not devices:
            raise ConfigError(
                "'devices' must list at least one device when auto_discover is false"
            )

        rescan = values.get("rescan_interval", DEFAULT_RESCAN_INTERVAL)
        if (
            isinstance(rescan, bool)
            or not isinstance(rescan, (int, float))
            or rescan < 0
        ):
            raise ConfigError(f"rescan_interval must be >= 0, got {rescan!r}")

        level = str(values.get("log_level") or "info").lower()
        if level not in LOG_LEVELS:
            raise ConfigError(f"log_level must be one of {', '.join(LOG_LEVELS)}")

        state_file = _opt_str(values.get("state_file"))
        return cls(
            mqtt_host=_opt_str(values.get("mqtt_host")),
            mqtt_port=port,
            mqtt_username=_opt_str(values.get("mqtt_username")),
            mqtt_password=_opt_str(values.get("mqtt_password")),
            mqtt_tls=tls,
            mqtt_tls_ca=tls_ca,
            name=str(values.get("name") or ""),
            discovery_prefix=prefix,
            base_topic=base,
            legacy_topic=legacy or f"{prefix}/sensor/evmqtt",
            auto_discover=auto,
            keystates=parse_keystates(values.get("keystates")),
            devices=devices,
            enabled_devices=_str_list("enabled_devices", values.get("enabled_devices")),
            state_file=Path(state_file) if state_file else None,
            rescan_interval=float(rescan),
            cleanup_legacy=_bool("cleanup_legacy", values.get("cleanup_legacy", True)),
            log_level=level,
            addon=addon,
            warnings=tuple(warnings),
        )

    @classmethod
    def load(cls, config_path: str | Path | None = None) -> Config:
        """Load from, in order: config_path, $EVMQTT_CONFIG, /data/options.json
        (add-on), ./config.local.json, ./config.json."""
        addon = False
        if config_path is not None:
            path = Path(config_path)
        elif env_path := os.environ.get("EVMQTT_CONFIG"):
            path = Path(env_path)
        elif HA_OPTIONS_PATH.is_file():
            path = HA_OPTIONS_PATH
            addon = True
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
        return cls.from_dict(data, addon=addon)
