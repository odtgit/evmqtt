"""The gateway against real kernel input devices created through uinput.

uinput devices are virtual, so every test lists its device explicitly and
nothing else on the machine is selected. Skipped unless /dev/uinput is
writable; EVMQTT_REQUIRE_UINPUT=1 turns the skip into a failure (CI).
"""

from __future__ import annotations

import errno
import json
import select
import uuid
from pathlib import Path

import evdev
import pytest
from evdev import UInput, ecodes

import evmqtt.sysinfo as sysinfo
from evmqtt.config import Config
from evmqtt.core import describe, open_device
from evmqtt.gateway import Gateway
from tests.fakes import published, until
from tests.integration.conftest import UInputFactory

pytestmark = pytest.mark.uinput

BASE = "evmqtt/uinput"


@pytest.fixture(autouse=True)
def real_sysfs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sysinfo, "SYSFS_ROOT", Path("/sys"))


@pytest.fixture
def name() -> str:
    return f"evmqtt-test-kbd-{uuid.uuid4().hex[:8]}"


def make_config(tmp_path: Path, name: str, **overrides: object) -> Config:
    data: dict[str, object] = {
        "mqtt_host": "broker.local",
        "base_topic": BASE,
        "state_file": str(tmp_path / "state.json"),
        "rescan_interval": 0,
        "auto_discover": False,
        "devices": [name],
    }
    data.update(overrides)
    return Config.from_dict(data)


def tap(ui: UInput, code: int, *states: int) -> None:
    for state in states:
        ui.write(ecodes.EV_KEY, code, state)
        ui.syn()


def other_can_grab(path: str) -> bool:
    other = evdev.InputDevice(path)
    try:
        other.grab()
    except OSError as err:
        assert err.errno == errno.EBUSY
        return False
    else:
        other.ungrab()
        return True
    finally:
        other.close()


async def start(config: Config) -> Gateway:
    gateway = Gateway(config, cleanup_window=0.01)
    await gateway.start()
    assert await until(
        lambda: (
            gateway.mqtt is not None
            and any(r.topic == f"{BASE}/status" for r in gateway.mqtt.client.published)
        )
    )
    return gateway


async def test_uinput_device_is_virtual_and_not_selected_by_default(
    make_uinput: UInputFactory, name: str, tmp_path: Path
) -> None:
    ui = make_uinput(name=name)
    device = open_device(ui.device.path)
    try:
        info = describe(device)
    finally:
        device.close()
    assert info.is_keyboard_like
    assert sysinfo.is_virtual(info)
    auto = Gateway(Config.from_dict({"state_file": str(tmp_path / "s.json")}))
    assert not auto.selected(info)
    assert Gateway(make_config(tmp_path, name)).selected(info)


async def test_switch_off_releases_grab_on_real_device(
    make_uinput: UInputFactory, name: str, fake_mqtt, tmp_path: Path
) -> None:
    ui = make_uinput(name=name)
    path = ui.device.path
    gateway = await start(make_config(tmp_path, name))
    try:
        client = fake_mqtt.last()
        (dev_id,) = gateway.devices
        assert not other_can_grab(path)

        tap(ui, ecodes.KEY_LEFTSHIFT, 1)
        tap(ui, ecodes.KEY_MUTE, 1, 0)
        tap(ui, ecodes.KEY_LEFTSHIFT, 0)
        assert await until(lambda: published(client, f"{BASE}/{dev_id}/event"))
        event = json.loads(published(client, f"{BASE}/{dev_id}/event")[-1].payload)
        assert (event["key"], event["modifiers"]) == ("KEY_MUTE", ["KEY_LEFTSHIFT"])

        client.inject(f"{BASE}/{dev_id}/switch/set", "OFF")
        assert await until(lambda: not gateway.devices[dev_id].enabled)
        assert other_can_grab(path)
        tap(ui, ecodes.KEY_A, 1, 0)
        fd = gateway.devices[dev_id].reader.device.fd
        assert await until(lambda: not select.select([fd], [], [], 0)[0])

        client.inject(f"{BASE}/{dev_id}/switch/set", "ON")
        assert await until(lambda: gateway.devices[dev_id].enabled)
        assert not other_can_grab(path)
        tap(ui, ecodes.KEY_B, 1, 0)
        assert await until(
            lambda: len(published(client, f"{BASE}/{dev_id}/event")) == 2
        )
        keys = [
            json.loads(r.payload)["key"]
            for r in published(client, f"{BASE}/{dev_id}/event")
        ]
        assert keys == ["KEY_MUTE", "KEY_B"]
    finally:
        await gateway.stop()
    assert other_can_grab(path)


async def test_disabled_at_start_is_not_grabbed(
    make_uinput: UInputFactory, name: str, fake_mqtt, tmp_path: Path
) -> None:
    """A device that was disabled (via the switch, persisted to the state
    file) stays disabled and ungrabbed on the next start, even though it is
    listed and would otherwise start enabled."""
    ui = make_uinput(name=name)
    device = open_device(ui.device.path)
    try:
        dev_id = describe(device).id
    finally:
        device.close()
    state_path = tmp_path / "state.json"
    state_path.write_text(
        json.dumps(
            {
                "version": 2,
                "devices": {
                    dev_id: {
                        "enabled": False,
                        "name": name,
                        "path": ui.device.path,
                    }
                },
            }
        )
    )
    gateway = await start(make_config(tmp_path, name))
    try:
        assert other_can_grab(ui.device.path)
    finally:
        await gateway.stop()


async def test_unplug_goes_offline_and_replug_comes_back(
    make_uinput: UInputFactory, name: str, fake_mqtt, tmp_path: Path
) -> None:
    ui = make_uinput(name=name)
    gateway = await start(make_config(tmp_path, name))
    try:
        client = fake_mqtt.last()
        (dev_id,) = gateway.devices
        avail = f"{BASE}/{dev_id}/availability"
        assert published(client, avail)[-1].payload == "online"

        ui.close()
        assert await until(lambda: published(client, avail)[-1].payload == "offline")
        await gateway.rescan()
        assert list(gateway.devices) == [dev_id]

        ui2 = make_uinput(name=name)
        await gateway.rescan()
        assert list(gateway.devices) == [dev_id]
        assert published(client, avail)[-1].payload == "online"
        assert not other_can_grab(ui2.device.path)
        tap(ui2, ecodes.KEY_B, 1, 0)
        assert await until(lambda: published(client, f"{BASE}/{dev_id}/event"))
        event = json.loads(published(client, f"{BASE}/{dev_id}/event")[-1].payload)
        assert event["devicePath"] == ui2.device.path
    finally:
        await gateway.stop()
