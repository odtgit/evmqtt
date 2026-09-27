"""
evmqtt - Linux input event to MQTT gateway.

This package captures Linux input device events (keyboards, IR remotes)
and publishes them to an MQTT broker for home automation integration.
evmqtt.core has no MQTT dependency; the daemon names below load lazily
so `import evmqtt.core` works without paho-mqtt.

https://github.com/odtgit/evmqtt
"""

from __future__ import annotations

import importlib
from importlib.metadata import PackageNotFoundError, version
from typing import Any

try:
    __version__ = version("evmqtt")
except PackageNotFoundError:
    __version__ = "0.0.0+unknown"

__author__ = "odtgit"

_LAZY = {
    "Config": "evmqtt.config",
    "ConfigError": "evmqtt.config",
    "Gateway": "evmqtt.gateway",
    "MQTTClientWrapper": "evmqtt.mqtt_client",
}

__all__ = sorted(_LAZY)


def __getattr__(name: str) -> Any:
    module = _LAZY.get(name)
    if module is None:
        raise AttributeError(f"module 'evmqtt' has no attribute {name!r}")
    return getattr(importlib.import_module(module), name)
