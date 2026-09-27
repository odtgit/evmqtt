"""Scenario tests for Config: defaults, source precedence, HA mapping, validation."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from evmqtt.config import Config
from evmqtt.core import KeyState


def base_dict(**overrides: object) -> dict[str, object]:
    data: dict[str, object] = {
        "serverip": "192.168.1.100",
        "port": 1883,
        "username": "user",
        "password": "pass",
        "name": "Test Gateway",
        "topic": "homeassistant/sensor/evmqtt",
        "devices": ["/dev/input/event0"],
    }
    data.update(overrides)
    return data


def test_from_dict_defaults() -> None:
    config = Config.from_dict(
        {
            "serverip": "192.168.1.100",
            "name": "Gateway",
            "topic": "evmqtt",
            "devices": ["/dev/input/event0"],
        }
    )
    assert config.port == 1883
    assert config.username == ""
    assert config.password == ""
    assert config.auto_discover is False
    assert config.enabled_devices == []
    assert config.filter_keys_only is True
    assert config.tls is False
    assert config.tls_ca == ""


def test_source_precedence(
    tmp_path: Path,
    isolated_ha_options_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("EVMQTT_CONFIG", raising=False)

    (tmp_path / "config.json").write_text(
        json.dumps(base_dict(name="from-config-json"))
    )
    assert Config.load().name == "from-config-json"

    (tmp_path / "config.local.json").write_text(
        json.dumps(base_dict(name="from-config-local"))
    )
    assert Config.load().name == "from-config-local"

    isolated_ha_options_path.write_text(
        json.dumps(
            {
                "mqtt_host": "ha.local",
                "name": "from-ha-options",
                "topic": "evmqtt",
                "devices": ["/dev/input/event0"],
            }
        )
    )
    assert Config.load().name == "from-ha-options"

    env_file = tmp_path / "env_config.json"
    env_file.write_text(json.dumps(base_dict(name="from-env")))
    monkeypatch.setenv("EVMQTT_CONFIG", str(env_file))
    assert Config.load().name == "from-env"

    explicit_file = tmp_path / "explicit.json"
    explicit_file.write_text(json.dumps(base_dict(name="from-explicit")))
    assert Config.load(explicit_file).name == "from-explicit"


def test_load_not_found(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("EVMQTT_CONFIG", raising=False)
    with pytest.raises(FileNotFoundError):
        Config.load()


def test_load_bad_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("EVMQTT_CONFIG", raising=False)
    (tmp_path / "config.json").write_text("{not json")
    with pytest.raises(json.JSONDecodeError):
        Config.load()


@pytest.mark.parametrize(
    ("options", "expected_name", "expected_topic"),
    [
        (
            {
                "mqtt_host": "ha.local",
                "mqtt_port": 8884,
                "mqtt_username": "hauser",
                "mqtt_password": "hapass",
                "name": "Input Events",
                "topic": "custom/topic",
                "devices": ["/dev/input/event0"],
            },
            "Input Events",
            "custom/topic",
        ),
        # legacy fallback keys (no mqtt_ prefix) and defaults
        (
            {"serverip": "legacy.local", "devices": ["/dev/input/event0"]},
            "evmqtt",
            "homeassistant/sensor/evmqtt",
        ),
    ],
)
def test_from_ha_options_mapping(
    options: dict[str, object], expected_name: str, expected_topic: str
) -> None:
    config = Config.from_ha_options(options)
    assert config.name == expected_name
    assert config.topic == expected_topic


@pytest.mark.parametrize(
    "data",
    [
        {"tls": True, "serverip": "s", "name": "n", "topic": "t", "devices": ["/x"]},
        {
            "tls_ca": "/ca.pem",
            "serverip": "s",
            "name": "n",
            "topic": "t",
            "devices": ["/x"],
        },
    ],
)
def test_from_dict_tls_implies_8883_when_port_absent(data: dict[str, object]) -> None:
    assert Config.from_dict(data).port == 8883


@pytest.mark.parametrize(
    "options",
    [
        {"mqtt_tls": True, "mqtt_host": "s", "devices": ["/x"]},
        {"tls_ca": "/ca.pem", "mqtt_host": "s", "devices": ["/x"]},
    ],
)
def test_from_ha_options_tls_implies_8883_when_port_absent(
    options: dict[str, object],
) -> None:
    assert Config.from_ha_options(options).port == 8883


@pytest.mark.parametrize(
    ("overrides", "exc_type"),
    [
        ({"serverip": ""}, ValueError),
        ({"port": 0}, ValueError),
        ({"port": 65536}, ValueError),
        ({"topic": ""}, ValueError),
        ({"devices": []}, ValueError),
    ],
)
def test_from_dict_rejects_invalid_values(
    overrides: dict[str, object], exc_type: type[Exception]
) -> None:
    with pytest.raises(exc_type):
        Config.from_dict(base_dict(**overrides))


def test_from_dict_missing_required_key_raises_key_error() -> None:
    data = base_dict()
    del data["name"]
    with pytest.raises(KeyError):
        Config.from_dict(data)


def test_auto_discover_allows_empty_devices() -> None:
    config = Config.from_dict(base_dict(devices=[], auto_discover=True))
    assert config.devices == []
    assert config.auto_discover is True


def test_keystates_default_is_press_only() -> None:
    config = Config.from_dict(base_dict())
    assert config.keystates == frozenset({KeyState.PRESS})


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (["RELEASE"], frozenset({KeyState.RELEASE})),
        (["press", "repeat"], frozenset({KeyState.PRESS, KeyState.REPEAT})),
        ([0], frozenset({KeyState.RELEASE})),
        ([KeyState.PRESS], frozenset({KeyState.PRESS})),
    ],
)
def test_keystates_normalized(value: list[object], expected: frozenset) -> None:
    config = Config.from_dict(base_dict(keystates=value))
    assert config.keystates == expected


@pytest.mark.parametrize(
    ("value", "exc_type", "match"),
    [
        (["INVALID"], ValueError, "Invalid keystate: INVALID"),
        ([3], ValueError, "Invalid keystate: 3"),
        ([None], TypeError, "Invalid keystate type: NoneType"),
        ([], ValueError, "keystates cannot be empty"),
    ],
)
def test_keystates_rejects_invalid_values(
    value: list[object], exc_type: type[Exception], match: str
) -> None:
    with pytest.raises(exc_type, match=match):
        Config.from_dict(base_dict(keystates=value))


def test_keystates_from_ha_options_default_is_press_only() -> None:
    config = Config.from_ha_options(
        {"mqtt_host": "ha.local", "devices": ["/dev/input/event0"]}
    )
    assert config.keystates == frozenset({KeyState.PRESS})
