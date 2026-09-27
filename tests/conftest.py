"""Shared pytest fixtures for evmqtt scenario tests."""

from __future__ import annotations

import asyncio
import inspect
from pathlib import Path

import evdev
import pytest

import evmqtt.config as config_module
import evmqtt.mqtt_client as mqtt_client_module
import evmqtt.sysinfo as sysinfo_module
from tests.fakes import FakeEvdevRegistry, FakeSysfs, make_fake_paho_class

ASYNC_TEST_TIMEOUT = 10.0


@pytest.hookimpl(tryfirst=True)
def pytest_pyfunc_call(pyfuncitem: pytest.Function) -> bool | None:
    """Run `async def` tests on a fresh asyncio loop."""
    if not inspect.iscoroutinefunction(pyfuncitem.obj):
        return None
    names = pyfuncitem._fixtureinfo.argnames
    kwargs = {name: pyfuncitem.funcargs[name] for name in names}
    asyncio.run(asyncio.wait_for(pyfuncitem.obj(**kwargs), ASYNC_TEST_TIMEOUT))
    return True


@pytest.fixture(autouse=True)
def isolated_ha_options_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point Config.load's HA options search at a tmp path, not /data/options.json."""
    fake_path = tmp_path / "options.json"
    monkeypatch.setattr(config_module, "HA_OPTIONS_PATH", fake_path)
    monkeypatch.setattr(config_module, "HA_STATE_FILE", tmp_path / "addon-state.json")
    for var in ("EVMQTT_CONFIG", "SUPERVISOR_TOKEN", "STATE_DIRECTORY"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg-state"))
    return fake_path


@pytest.fixture(autouse=True)
def sysfs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FakeSysfs:
    """An empty fake /sys, so fake /dev/input paths never hit the real one."""
    root = tmp_path / "sys"
    root.mkdir()
    monkeypatch.setattr(sysinfo_module, "SYSFS_ROOT", root)
    return FakeSysfs(root)


@pytest.fixture
def fake_evdev(monkeypatch: pytest.MonkeyPatch):
    """Patch evdev.InputDevice / evdev.list_devices with an in-memory registry."""
    registry = FakeEvdevRegistry()
    monkeypatch.setattr(evdev, "InputDevice", registry.open)
    monkeypatch.setattr(evdev, "list_devices", registry.list_devices)
    yield registry
    registry.close_all()


@pytest.fixture
def fake_mqtt(monkeypatch: pytest.MonkeyPatch):
    """Patch evmqtt.mqtt_client.mqtt.Client with an isolated FakePahoClient."""
    cls = make_fake_paho_class()
    monkeypatch.setattr(mqtt_client_module.mqtt, "Client", cls)
    return cls
