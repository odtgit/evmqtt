"""Fixtures for the Home Assistant integration tests."""

from __future__ import annotations

import threading
from collections.abc import Generator
from pathlib import Path
from typing import Any

import evdev
import pytest
from homeassistant.core import HomeAssistant

import evmqtt.sysinfo as sysinfo_module
from custom_components.evmqtt import scan
from tests.fakes import FakeEvdevRegistry, FakeSysfs
from tests_ha.common import BASE_TOPIC


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    return


@pytest.fixture(autouse=True)
def sysfs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FakeSysfs:
    root = tmp_path / "sys"
    root.mkdir()
    monkeypatch.setattr(sysinfo_module, "SYSFS_ROOT", root)
    return FakeSysfs(root)


class LoopGuard:
    """Records blocking device and sysfs calls made on the event loop thread."""

    def __init__(self) -> None:
        self.loop_thread: int | None = None
        self.violations: list[str] = []

    def wrap(self, name: str, func: Any) -> Any:
        def guarded(*args: Any, **kwargs: Any) -> Any:
            if threading.get_ident() == self.loop_thread:
                self.violations.append(name)
            return func(*args, **kwargs)

        return guarded


@pytest.fixture(autouse=True)
def fake_evdev(
    monkeypatch: pytest.MonkeyPatch, loop_guard: LoopGuard
) -> Generator[FakeEvdevRegistry]:
    registry = FakeEvdevRegistry()
    monkeypatch.setattr(evdev, "InputDevice", loop_guard.wrap("open", registry.open))
    monkeypatch.setattr(
        evdev, "list_devices", loop_guard.wrap("list_devices", registry.list_devices)
    )
    monkeypatch.setattr(scan, "input_nodes", registry.list_devices)
    monkeypatch.setattr(
        sysinfo_module,
        "_input_node",
        loop_guard.wrap("sysfs", sysinfo_module._input_node),
    )
    yield registry
    registry.close_all()


@pytest.fixture(autouse=True)
async def loop_guard_thread(hass: HomeAssistant, loop_guard: LoopGuard) -> None:
    loop_guard.loop_thread = threading.get_ident()


@pytest.fixture
def loop_guard() -> Generator[LoopGuard]:
    guard = LoopGuard()
    yield guard
    assert not guard.violations, f"blocking calls on the event loop: {guard.violations}"


@pytest.fixture(autouse=True)
def base_topic(monkeypatch: pytest.MonkeyPatch) -> str:
    import evmqtt.config as config_module

    monkeypatch.setattr(config_module, "host_slug", lambda: "test-host")
    return BASE_TOPIC
