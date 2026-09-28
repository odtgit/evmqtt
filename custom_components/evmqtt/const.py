"""Constants for the evmqtt integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "evmqtt"

CONF_ENABLED_DEVICES: Final = "enabled_devices"
CONF_INCLUDE_VIRTUAL: Final = "include_virtual"
CONF_KEYSTATES: Final = "keystates"
CONF_RESCAN_INTERVAL: Final = "rescan_interval"
CONF_MQTT_MIRROR: Final = "mqtt_mirror"
CONF_MQTT_BASE_TOPIC: Final = "mqtt_base_topic"

KEYSTATES: Final = ["press", "repeat", "release"]
DEFAULT_KEYSTATES: Final = ["press"]
DEFAULT_RESCAN_INTERVAL: Final = 5
MAX_RESCAN_INTERVAL: Final = 3600
