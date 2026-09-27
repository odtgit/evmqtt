"""Scenario tests for the evmqtt CLI entry point (main)."""

from __future__ import annotations

import json
import logging
import os
import signal
import sys
from pathlib import Path

import pytest

import evmqtt.__main__ as main_module
from evmqtt.__main__ import Application, main, parse_args, setup_logging
from tests.fakes import keyboard_capabilities

BASE_CONFIG: dict[str, object] = {
    "serverip": "broker.local",
    "name": "Gateway",
    "topic": "homeassistant/sensor/evmqtt",
    "devices": ["/dev/input/event0"],
}


def write_config(tmp_path: Path, **overrides: object) -> Path:
    data = dict(BASE_CONFIG)
    data.update(overrides)
    path = tmp_path / "config.json"
    path.write_text(json.dumps(data))
    return path


class RecordingApplication:
    """Stand-in for Application that records the config it was built with."""

    captured_config = None

    def __init__(self, config, connect_timeout: float = 30.0) -> None:
        RecordingApplication.captured_config = config
        self.shutdown_requested = True

    async def start(self) -> None:
        pass

    async def wait(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    def _handle_signal(self, signum: int) -> None:
        pass


class FastApplication(Application):
    """Application with a short connect timeout, for CLI-level broker tests."""

    def __init__(self, config) -> None:
        super().__init__(config, connect_timeout=0.1)


class SigtermAfterStart(Application):
    """Application that sends itself SIGTERM once running."""

    async def start(self) -> None:
        await super().start()
        os.kill(os.getpid(), signal.SIGTERM)


def test_list_devices_found_exits_zero(fake_evdev, capsys) -> None:
    fake_evdev.add(
        "/dev/input/event0", name="Kbd", capabilities=keyboard_capabilities()
    )
    with pytest.raises(SystemExit) as exc_info:
        main(["--list-devices"])
    assert exc_info.value.code == 0
    assert "Kbd" in capsys.readouterr().out


def test_list_devices_none_found_exits_one(fake_evdev, capsys) -> None:
    with pytest.raises(SystemExit) as exc_info:
        main(["--list-devices"])
    assert exc_info.value.code == 1


def test_bad_config_exits_one(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("EVMQTT_CONFIG", raising=False)
    (tmp_path / "config.json").write_text(json.dumps({**BASE_CONFIG, "serverip": ""}))
    assert main([]) == 1


def test_broker_connect_timeout_exits_one(
    fake_evdev, fake_mqtt, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_evdev.add(
        "/dev/input/event0", name="Kbd", capabilities=keyboard_capabilities()
    )
    fake_mqtt.auto_connect = False
    monkeypatch.setattr(main_module, "Application", FastApplication)
    config_path = write_config(tmp_path)
    assert main(["--config", str(config_path)]) == 1


@pytest.mark.parametrize(
    ("flags", "expected_level"),
    [
        ([], logging.WARNING),
        (["-v"], logging.INFO),
        (["-d"], logging.DEBUG),
    ],
)
def test_log_level_flags(flags: list[str], expected_level: int) -> None:
    root = logging.getLogger()
    original_level = root.level
    original_handlers = list(root.handlers)
    try:
        args = parse_args(flags)
        setup_logging(verbose=args.verbose, debug=args.debug)
        assert root.level == expected_level
    finally:
        root.handlers = original_handlers
        root.setLevel(original_level)


def test_auto_discover_flag_preserves_tls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(main_module, "Application", RecordingApplication)
    config_path = write_config(
        tmp_path,
        auto_discover=False,
        tls=True,
        tls_ca="/etc/evmqtt/ca.pem",
        port=8883,
    )
    exit_code = main(["--config", str(config_path), "--auto-discover"])
    assert exit_code == 0
    assert RecordingApplication.captured_config.tls is True
    assert RecordingApplication.captured_config.tls_ca == "/etc/evmqtt/ca.pem"


def test_all_monitors_dead_exits_nonzero(fake_evdev, fake_mqtt, tmp_path: Path) -> None:
    device = fake_evdev.add(
        "/dev/input/event0", name="Kbd", capabilities=keyboard_capabilities()
    )
    device.fail_grab()
    config_path = write_config(tmp_path)
    exit_code = main(["--config", str(config_path)])
    assert exit_code != 0


@pytest.mark.parametrize(
    "overrides",
    [
        {"tls_ca": "/no/such/ca.pem", "port": 8883},
    ],
)
def test_non_connection_startup_error_returns_exit_one(
    fake_evdev, fake_mqtt, tmp_path: Path, overrides: dict[str, object]
) -> None:
    fake_evdev.add(
        "/dev/input/event0", name="Kbd", capabilities=keyboard_capabilities()
    )
    config_path = write_config(tmp_path, **overrides)
    assert main(["--config", str(config_path)]) == 1


def test_sigterm_shuts_down_cleanly_and_exits_zero(
    fake_evdev, fake_mqtt, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    device = fake_evdev.add(
        "/dev/input/event0", name="Kbd", capabilities=keyboard_capabilities()
    )
    monkeypatch.setattr(main_module, "Application", SigtermAfterStart)
    config_path = write_config(tmp_path)
    assert main(["--config", str(config_path)]) == 0
    assert device.grab_calls == 1
    assert not device.grabbed
    assert device.closed
    assert fake_mqtt.last().connected is False


def test_unplug_of_only_device_exits_one(
    fake_evdev, fake_mqtt, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    device = fake_evdev.add(
        "/dev/input/event0", name="Kbd", capabilities=keyboard_capabilities()
    )

    class UnplugAfterStart(Application):
        async def start(self) -> None:
            await super().start()
            device.unplug()

    monkeypatch.setattr(main_module, "Application", UnplugAfterStart)
    config_path = write_config(tmp_path)
    assert main(["--config", str(config_path)]) == 1
    assert device.closed


def test_missing_paho_exits_one_with_hint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    monkeypatch.setitem(sys.modules, "paho.mqtt.client", None)
    config_path = write_config(tmp_path)
    assert main(["--config", str(config_path)]) == 1
    assert "evmqtt[mqtt]" in capsys.readouterr().err
