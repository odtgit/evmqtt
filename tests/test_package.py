"""The top-level package keeps its 1.x names, loaded lazily."""

from __future__ import annotations

import pytest

import evmqtt
from evmqtt.key_handler import KeyHandler
from evmqtt.mqtt_client import MQTTClientWrapper


def test_lazy_exports_resolve_to_daemon_classes() -> None:
    assert evmqtt.KeyHandler is KeyHandler
    assert evmqtt.MQTTClientWrapper is MQTTClientWrapper
    assert set(evmqtt.__all__) >= {"Config", "InputMonitor", "discover_devices"}


def test_unknown_attribute_raises() -> None:
    with pytest.raises(AttributeError):
        _ = evmqtt.does_not_exist
