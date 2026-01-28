"""
evmqtt - Linux input event to MQTT gateway.

This package captures Linux input device events (keyboards, IR remotes)
and publishes them to an MQTT broker for home automation integration.

https://github.com/odtgit/evmqtt
"""

__version__ = "1.1.0"
__author__ = "odtgit"

from evmqtt.config import Config
from evmqtt.device_discovery import DiscoveredDevice, discover_devices, slugify
from evmqtt.input_monitor import InputMonitor
from evmqtt.key_handler import KeyHandler
from evmqtt.mqtt_client import MQTTClientWrapper

__all__ = [
    "Config",
    "DiscoveredDevice",
    "InputMonitor",
    "KeyHandler",
    "MQTTClientWrapper",
    "discover_devices",
    "slugify",
]
