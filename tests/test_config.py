"""Config: defaults, source precedence, 1.x aliases, validation."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from evmqtt.config import Config, ConfigError, host_slug, node_id
from evmqtt.core import KeyState


def test_defaults() -> None:
    config = Config.from_dict({"mqtt_host": "broker"})
    assert config.mqtt_host == "broker"
    assert config.mqtt_port is None
    assert (config.mqtt_username, config.mqtt_password) == (None, None)
    assert config.mqtt_tls is None
    assert config.discovery_prefix == "homeassistant"
    assert config.base_topic == f"evmqtt/{host_slug()}"
    assert config.node_id == host_slug()
    assert config.legacy_topic == "homeassistant/sensor/evmqtt"
    assert config.auto_discover is True
    assert config.keystates == frozenset({KeyState.PRESS})
    assert config.devices == ()
    assert config.enabled_devices == ()
    assert config.rescan_interval == 5.0
    assert config.cleanup_legacy is True
    assert config.log_level == "info"
    assert config.warnings == ()
    assert config.gateway_name.startswith("evmqtt ")


def test_full_2x_config() -> None:
    config = Config.from_dict(
        {
            "mqtt_host": "b",
            "mqtt_port": 8884,
            "mqtt_username": "u",
            "mqtt_password": "p",
            "mqtt_tls": True,
            "mqtt_tls_ca": "/ca.pem",
            "name": "Living room",
            "discovery_prefix": "ha/",
            "base_topic": "/home/remotes/",
            "keystates": ["press", "Repeat"],
            "devices": ["kbd-1234abcd", ""],
            "enabled_devices": ["/dev/input/event3"],
            "state_file": "/var/lib/x.json",
            "rescan_interval": 0,
            "cleanup_legacy": False,
            "log_level": "DEBUG",
        }
    )
    assert config.mqtt_port == 8884
    assert config.mqtt_tls is True
    assert config.discovery_prefix == "ha"
    assert config.base_topic == "home/remotes"
    assert config.node_id == "home-remotes"
    assert config.legacy_topic == "ha/sensor/evmqtt"
    assert config.keystates == {KeyState.PRESS, KeyState.REPEAT}
    assert config.devices == ("kbd-1234abcd",)
    assert config.state_path == Path("/var/lib/x.json")
    assert config.rescan_interval == 0
    assert config.log_level == "debug"
    assert config.gateway_name == "Living room"
    assert config.warnings == ()


def test_1x_keys_are_aliases_with_warnings() -> None:
    config = Config.from_dict(
        {
            "serverip": "old",
            "port": 1884,
            "username": "u",
            "password": "p",
            "tls": True,
            "tls_ca": "/ca",
            "mqtt_host": "new",
            "filter_keys_only": False,
            "bogus": 1,
        }
    )
    assert config.mqtt_host == "new"
    assert (config.mqtt_port, config.mqtt_username, config.mqtt_password) == (
        1884,
        "u",
        "p",
    )
    assert (config.mqtt_tls, config.mqtt_tls_ca) == (True, "/ca")
    text = "\n".join(config.warnings)
    assert "'serverip' is ignored" in text
    assert "'port' is deprecated, use 'mqtt_port'" in text
    assert "filter_keys_only" in text
    assert "unknown option 'bogus'" in text


@pytest.mark.parametrize(
    ("data", "base", "legacy", "warning"),
    [
        (
            {"topic": "homeassistant/sensor/evmqtt"},
            None,
            "homeassistant/sensor/evmqtt",
            "only used to clean up",
        ),
        ({"topic": "home/remote"}, "home/remote", "home/remote", "use 'base_topic'"),
        (
            {"topic": "home/remote", "base_topic": "evmqtt/x"},
            "evmqtt/x",
            "home/remote",
            "only used to clean up",
        ),
    ],
)
def test_legacy_topic_mapping(data, base, legacy, warning) -> None:
    config = Config.from_dict({"mqtt_host": "b", **data})
    assert config.base_topic == (base or f"evmqtt/{host_slug()}")
    assert config.legacy_topic == legacy
    assert any(warning in w for w in config.warnings)


@pytest.mark.parametrize(
    ("base", "expected"),
    [("evmqtt/my-host", "my-host"), ("evmqtt", "evmqtt"), ("A/B_c", "a-b-c")],
)
def test_node_id(base: str, expected: str) -> None:
    assert node_id(base) == expected


@pytest.mark.parametrize(
    "data",
    [
        {"mqtt_port": 0},
        {"mqtt_port": 70000},
        {"mqtt_port": "1883"},
        {"mqtt_port": True},
        {"mqtt_tls": "yes"},
        {"base_topic": "homeassistant/evmqtt"},
        {"base_topic": "a/+/b"},
        {"discovery_prefix": "#"},
        {"auto_discover": False},
        {"auto_discover": False, "devices": []},
        {"devices": "/dev/input/event0"},
        {"rescan_interval": -1},
        {"rescan_interval": "5"},
        {"log_level": "verbose"},
        {"keystates": []},
        {"keystates": "PRESS"},
        {"keystates": ["HOLD"]},
        {"keystates": [7]},
        {"keystates": [1.5]},
        {"cleanup_legacy": "no"},
        {"filter_keys_only": "yes"},
    ],
)
def test_invalid_values_raise_config_error(data: dict[str, object]) -> None:
    with pytest.raises(ConfigError):
        Config.from_dict({"mqtt_host": "b", **data})


def test_non_object_raises() -> None:
    with pytest.raises(ConfigError):
        Config.from_dict([1])  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (["PRESS", "RELEASE"], {KeyState.PRESS, KeyState.RELEASE}),
        ([0, 2], {KeyState.RELEASE, KeyState.REPEAT}),
        ([KeyState.REPEAT], {KeyState.REPEAT}),
    ],
)
def test_keystates_normalized(value: list[object], expected: set[KeyState]) -> None:
    assert Config.from_dict({"keystates": value}).keystates == expected


def test_state_path_defaults(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    assert Config.from_dict({}).state_path == tmp_path / "xdg-state/evmqtt/state.json"
    monkeypatch.setenv("STATE_DIRECTORY", "/var/lib/evmqtt:/other")
    assert Config.from_dict({}).state_path == Path("/var/lib/evmqtt/state.json")
    assert Config.from_dict({}, addon=True).state_path == tmp_path / "addon-state.json"


def test_source_precedence(
    tmp_path: Path,
    isolated_ha_options_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config.json").write_text(json.dumps({"name": "config-json"}))
    assert Config.load().name == "config-json"
    assert Config.load().addon is False

    (tmp_path / "config.local.json").write_text(json.dumps({"name": "local"}))
    assert Config.load().name == "local"

    isolated_ha_options_path.write_text(json.dumps({"name": "addon"}))
    assert Config.load().name == "addon"
    assert Config.load().addon is True

    env_file = tmp_path / "env.json"
    env_file.write_text(json.dumps({"name": "env"}))
    monkeypatch.setenv("EVMQTT_CONFIG", str(env_file))
    assert Config.load().name == "env"

    explicit = tmp_path / "explicit.json"
    explicit.write_text(json.dumps({"name": "explicit"}))
    assert Config.load(explicit).name == "explicit"


def test_load_not_found(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    with pytest.raises(FileNotFoundError):
        Config.load()


def test_load_bad_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config.json").write_text("{not json")
    with pytest.raises(json.JSONDecodeError):
        Config.load()


def test_example_config_is_valid() -> None:
    root = Path(__file__).resolve().parent.parent
    config = Config.load(root / "config.example.json")
    assert config.warnings == ()
