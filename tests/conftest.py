"""Shared pytest fixtures for evmqtt scenario tests."""

from __future__ import annotations

from pathlib import Path

import evdev
import pytest

import evmqtt.config as config_module
import evmqtt.mqtt_client as mqtt_client_module
from tests.fakes import FakeEvdevRegistry, make_fake_paho_class


@pytest.fixture(autouse=True)
def isolated_ha_options_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point Config.load's HA options search at a tmp path, not /data/options.json."""
    fake_path = tmp_path / "options.json"
    monkeypatch.setattr(config_module, "HA_OPTIONS_PATH", fake_path)
    return fake_path


@pytest.fixture
def fake_evdev(monkeypatch: pytest.MonkeyPatch) -> FakeEvdevRegistry:
    """Patch evdev.InputDevice / evdev.list_devices with an in-memory registry."""
    registry = FakeEvdevRegistry()
    monkeypatch.setattr(evdev, "InputDevice", registry.open)
    monkeypatch.setattr(evdev, "list_devices", registry.list_devices)
    yield registry
    # Unblock any monitor threads left mid-read_loop so daemon threads exit.
    registry.close_all()


@pytest.fixture
def fake_mqtt(monkeypatch: pytest.MonkeyPatch):
    """Patch evmqtt.mqtt_client.mqtt.Client with an isolated FakePahoClient."""
    cls = make_fake_paho_class()
    monkeypatch.setattr(mqtt_client_module.mqtt, "Client", cls)
    return cls
