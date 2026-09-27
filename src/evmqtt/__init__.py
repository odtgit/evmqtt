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
from typing import Any

__version__ = "1.1.0"
__author__ = "odtgit"

_LAZY = {
    "Config": "evmqtt.config",
    "DiscoveredDevice": "evmqtt.device_discovery",
    "discover_devices": "evmqtt.device_discovery",
    "slugify": "evmqtt.device_discovery",
    "InputMonitor": "evmqtt.input_monitor",
    "KeyHandler": "evmqtt.key_handler",
    "MQTTClientWrapper": "evmqtt.mqtt_client",
}

__all__ = sorted(_LAZY)


def __getattr__(name: str) -> Any:
    module = _LAZY.get(name)
    if module is None:
        raise AttributeError(f"module 'evmqtt' has no attribute {name!r}")
    return getattr(importlib.import_module(module), name)
