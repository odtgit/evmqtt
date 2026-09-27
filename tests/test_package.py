"""Top-level package names load lazily, so evmqtt.core works without paho."""

from __future__ import annotations

import pytest

import evmqtt
from evmqtt.gateway import Gateway
from evmqtt.mqtt_client import MQTTClientWrapper


def test_lazy_exports_resolve_to_daemon_classes() -> None:
    assert evmqtt.Gateway is Gateway
    assert evmqtt.MQTTClientWrapper is MQTTClientWrapper
    assert set(evmqtt.__all__) == {
        "Config",
        "ConfigError",
        "Gateway",
        "MQTTClientWrapper",
    }


def test_unknown_attribute_raises() -> None:
    with pytest.raises(AttributeError):
        _ = evmqtt.does_not_exist
