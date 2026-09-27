"""MQTT topic layout and Home Assistant discovery payloads."""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

import evmqtt
from evmqtt.core import DeviceInfo, KeyEvent, KeyState
from evmqtt.sysinfo import model_id, vendor_model

ONLINE = "online"
OFFLINE = "offline"
ON = "ON"
OFF = "OFF"
ORIGIN = {
    "name": "evmqtt",
    "sw_version": evmqtt.__version__,
    "support_url": "https://github.com/odtgit/evmqtt",
}
EVENT_TYPE_ORDER = (KeyState.PRESS, KeyState.REPEAT, KeyState.RELEASE)


def event_types(keystates: Iterable[KeyState]) -> list[str]:
    wanted = set(keystates)
    return [s.name.lower() for s in EVENT_TYPE_ORDER if s in wanted]


@dataclass(frozen=True)
class Topics:
    discovery_prefix: str
    base_topic: str
    node_id: str

    @property
    def status(self) -> str:
        return f"{self.base_topic}/status"

    @property
    def ha_status(self) -> str:
        return f"{self.discovery_prefix}/status"

    @property
    def switch_commands(self) -> str:
        return f"{self.base_topic}/+/switch/set"

    @property
    def gateway_identifier(self) -> str:
        return f"evmqtt_{self.node_id}"

    @property
    def gateway_discovery(self) -> str:
        return f"{self.discovery_prefix}/device/{self.gateway_identifier}/config"

    def device(self, device_id: str) -> str:
        return f"{self.base_topic}/{device_id}"

    def availability(self, device_id: str) -> str:
        return f"{self.device(device_id)}/availability"

    def event(self, device_id: str) -> str:
        return f"{self.device(device_id)}/event"

    def switch_state(self, device_id: str) -> str:
        return f"{self.device(device_id)}/switch/state"

    def switch_command(self, device_id: str) -> str:
        return f"{self.device(device_id)}/switch/set"

    def device_identifier(self, device_id: str) -> str:
        return f"evmqtt_{self.node_id}_{device_id}"

    def device_discovery(self, device_id: str) -> str:
        return (
            f"{self.discovery_prefix}/device/{self.device_identifier(device_id)}/config"
        )

    def device_id_from_command(self, topic: str) -> str | None:
        prefix = f"{self.base_topic}/"
        suffix = "/switch/set"
        if not topic.startswith(prefix) or not topic.endswith(suffix):
            return None
        device_id = topic[len(prefix) : -len(suffix)]
        return device_id if device_id and "/" not in device_id else None


def _availability(topic: str) -> dict[str, str]:
    return {
        "topic": topic,
        "payload_available": ONLINE,
        "payload_not_available": OFFLINE,
    }


def gateway_discovery(topics: Topics, name: str) -> dict[str, Any]:
    return {
        "device": {
            "identifiers": [topics.gateway_identifier],
            "name": name,
            "manufacturer": "evmqtt",
            "model": "evmqtt gateway",
            "sw_version": evmqtt.__version__,
        },
        "origin": ORIGIN,
        "components": {
            "status": {
                "platform": "binary_sensor",
                "unique_id": f"{topics.gateway_identifier}_status",
                "name": "Status",
                "device_class": "connectivity",
                "entity_category": "diagnostic",
                "state_topic": topics.status,
                "payload_on": ONLINE,
                "payload_off": OFFLINE,
            }
        },
    }


def device_block(topics: Topics, info: DeviceInfo) -> dict[str, Any]:
    block: dict[str, Any] = {
        "identifiers": [topics.device_identifier(info.id)],
        "name": info.name,
    }
    manufacturer, model = vendor_model(info)
    if manufacturer:
        block["manufacturer"] = manufacturer
    if model:
        block["model"] = model
    mid = model_id(info)
    if mid:
        block["model_id"] = mid
    block["via_device"] = topics.gateway_identifier
    return block


def device_discovery(
    topics: Topics, info: DeviceInfo, keystates: Iterable[KeyState]
) -> dict[str, Any]:
    uid = topics.device_identifier(info.id)
    return {
        "device": device_block(topics, info),
        "origin": ORIGIN,
        "availability": [
            _availability(topics.status),
            _availability(topics.availability(info.id)),
        ],
        "availability_mode": "all",
        "components": {
            "event": {
                "platform": "event",
                "unique_id": f"{uid}_event",
                "name": "Key",
                "icon": "mdi:keyboard",
                "device_class": "button",
                "state_topic": topics.event(info.id),
                "event_types": event_types(keystates),
            },
            "switch": {
                "platform": "switch",
                "unique_id": f"{uid}_switch",
                "name": "Enabled",
                "icon": "mdi:keyboard-settings",
                "entity_category": "config",
                "state_topic": topics.switch_state(info.id),
                "command_topic": topics.switch_command(info.id),
                "payload_on": ON,
                "payload_off": OFF,
                "state_on": ON,
                "state_off": OFF,
            },
        },
    }


def event_payload(event: KeyEvent) -> dict[str, Any]:
    return {
        "event_type": event.state.name.lower(),
        "key": event.key,
        "modifiers": list(event.modifiers),
        "state": event.state.name,
        "deviceId": event.device_id,
        "deviceName": event.device_name,
        "devicePath": event.device_path,
    }


def dumps(payload: dict[str, Any]) -> str:
    return json.dumps(payload, separators=(",", ":"))


def is_legacy_config(
    topic: str, payload: str, *, legacy_topic: str, discovery_prefix: str
) -> list[str]:
    """Retained 1.x discovery to clear: [config topic, 1.x switch state topic?].

    Only single-component configs (never {prefix}/device/...) whose
    unique_id starts with evmqtt_ and whose state_topic lives under the 1.x
    topic. Anything else, including unparseable payloads, is left alone.
    """
    parts = topic.split("/")
    prefix = discovery_prefix.split("/")
    legacy = legacy_topic.split("/")
    in_prefix = len(parts) == len(prefix) + 3 and parts[: len(prefix)] == prefix
    in_legacy = len(parts) == len(legacy) + 2 and parts[: len(legacy)] == legacy
    if not (in_prefix or in_legacy) or parts[-1] != "config":
        return []
    if in_prefix and not in_legacy and parts[len(prefix)] == "device":
        return []
    try:
        data = json.loads(payload)
    except ValueError:
        return []
    if not isinstance(data, dict):
        return []
    uid = data.get("unique_id", data.get("uniq_id"))
    state = data.get("state_topic", data.get("stat_t"))
    if not isinstance(uid, str) or not uid.startswith("evmqtt_"):
        return []
    if not isinstance(state, str) or not state.startswith(f"{legacy_topic}/"):
        return []
    clear = [topic]
    if "command_topic" in data or "cmd_t" in data:
        clear.append(state)
    return clear
