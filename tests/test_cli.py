"""The evmqtt CLI entry point (main)."""

from __future__ import annotations

import json
import logging
import os
import signal
import sys
from pathlib import Path

import pytest

import evmqtt.gateway as gateway_module
from evmqtt.__main__ import cli_log_level, main, parse_args
from tests.fakes import keyboard_capabilities


def write_config(tmp_path: Path, **overrides: object) -> Path:
    data: dict[str, object] = {
        "mqtt_host": "broker.local",
        "base_topic": "evmqtt/cli",
        "state_file": str(tmp_path / "state.json"),
        "rescan_interval": 0,
    }
    data.update(overrides)
    path = tmp_path / "config.json"
    path.write_text(json.dumps(data))
    return path


class SigtermWhenConnected(gateway_module.Gateway):
    def _on_connected(self) -> None:
        super()._on_connected()
        os.kill(os.getpid(), signal.SIGTERM)


class SigtermAfterStart(gateway_module.Gateway):
    async def start(self) -> None:
        await super().start()
        os.kill(os.getpid(), signal.SIGTERM)


def test_list_devices(fake_evdev, sysfs, capsys) -> None:
    fake_evdev.add(
        "/dev/input/event0", name="Kbd", capabilities=keyboard_capabilities()
    )
    fake_evdev.add(
        "/dev/input/event1", name="Virt", capabilities=keyboard_capabilities()
    )
    sysfs.add("event1", virtual=True)
    assert main(["--list-devices"]) == 0
    out = capsys.readouterr().out
    assert "Found 2 input device(s)" in out
    kbd, virt = [line for line in out.splitlines() if "/dev/input" in line]
    assert '"Kbd"' in kbd and "[keyboard] (default)" in kbd
    assert "[keyboard, virtual]" in virt and "(default)" not in virt


def test_list_devices_none_found_exits_one(fake_evdev, capsys) -> None:
    assert main(["--list-devices"]) == 1


def test_bad_config_exits_one(tmp_path: Path, capsys) -> None:
    assert main(["--config", str(write_config(tmp_path, mqtt_port=0))]) == 1
    assert "Configuration error" in capsys.readouterr().err


def test_missing_config_exits_one(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    assert main([]) == 1


def test_missing_paho_exits_one_with_hint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    monkeypatch.setitem(sys.modules, "paho.mqtt.client", None)
    assert main(["--config", str(write_config(tmp_path))]) == 1
    assert "evmqtt[mqtt]" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("flags", "expected"),
    [
        ([], None),
        (["-v"], "info"),
        (["-d"], "debug"),
        (["--log-level", "error"], "error"),
    ],
)
def test_log_level_flags(flags: list[str], expected: str | None) -> None:
    assert cli_log_level(parse_args(flags)) == expected


def test_config_log_level_applies(fake_evdev, fake_mqtt, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(gateway_module, "Gateway", SigtermWhenConnected)
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    try:
        assert main(["-c", str(write_config(tmp_path, log_level="warning"))]) == 0
        assert root.level == logging.WARNING
        assert main(["-c", str(write_config(tmp_path, log_level="warning")), "-d"]) == 0
        assert root.level == logging.DEBUG
    finally:
        root.handlers, root.level = handlers, level


def test_sigterm_shuts_down_cleanly_and_exits_zero(
    fake_evdev, fake_mqtt, tmp_path, monkeypatch, capsys
) -> None:
    device = fake_evdev.add(
        "/dev/input/event0", name="Kbd", capabilities=keyboard_capabilities()
    )
    monkeypatch.setattr(gateway_module, "Gateway", SigtermWhenConnected)
    config = write_config(tmp_path, serverip="old", port=1883)
    assert main(["-c", str(config), "--auto-discover"]) == 0
    assert device.grab_calls == 1
    assert not device.grabbed
    assert device.closed
    client = fake_mqtt.last()
    assert client.connected is False
    assert fake_mqtt.retained["evmqtt/cli/status"] == "offline"
    assert "'port' is deprecated" in capsys.readouterr().err


def test_broker_down_keeps_running_until_signal(
    fake_evdev, fake_mqtt, tmp_path, monkeypatch
) -> None:
    fake_mqtt.auto_connect = False
    monkeypatch.setattr(gateway_module, "Gateway", SigtermAfterStart)
    assert main(["-c", str(write_config(tmp_path))]) == 0
    assert fake_mqtt.last().published == []


def test_no_devices_keeps_running_until_signal(
    fake_evdev, fake_mqtt, tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(gateway_module, "Gateway", SigtermWhenConnected)
    assert main(["-c", str(write_config(tmp_path))]) == 0


def test_missing_tls_ca_exits_one(fake_evdev, fake_mqtt, tmp_path, capsys) -> None:
    config = write_config(tmp_path, mqtt_tls_ca=str(tmp_path / "missing.pem"))
    assert main(["-c", str(config)]) == 1
    assert "Configuration error" in capsys.readouterr().err


def test_unexpected_fatal_error_exits_one(
    fake_evdev, fake_mqtt, tmp_path, monkeypatch, capsys
) -> None:
    async def broken(config):
        raise RuntimeError("boom")

    class Broken(gateway_module.Gateway):
        def __init__(self, config) -> None:
            super().__init__(config, broker_resolver=broken)

    monkeypatch.setattr(gateway_module, "Gateway", Broken)
    assert main(["-c", str(write_config(tmp_path))]) == 1
    assert "Fatal error" in capsys.readouterr().err
