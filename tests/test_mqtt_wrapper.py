"""Scenario tests for MQTTClientWrapper: TLS setup, inbound routing, robustness."""

from __future__ import annotations

from pathlib import Path

from evmqtt.config import Config
from evmqtt.mqtt_client import MQTTClientWrapper


def make_config(**overrides) -> Config:
    data: dict[str, object] = {
        "serverip": "broker.local",
        "name": "Gateway",
        "topic": "homeassistant/sensor/evmqtt",
        "devices": ["/dev/input/event0"],
    }
    data.update(overrides)
    return Config.from_dict(data)


def test_empty_username_skips_username_pw_set(fake_mqtt) -> None:
    config = make_config()
    wrapper = MQTTClientWrapper("c1", config)
    assert wrapper.client.username is None
    assert wrapper.client.password is None


def test_tls_ca_path_is_passed_to_client(fake_mqtt, tmp_path: Path) -> None:
    ca_path = tmp_path / "ca.pem"
    ca_path.write_text("fake-ca")
    config = make_config(tls_ca=str(ca_path), port=8883)
    wrapper = MQTTClientWrapper("c1", config)
    wrapper.connect()
    client = wrapper.client
    assert client.tls_ca == str(ca_path)
    assert client.tls_insecure is False


def test_tls_without_ca_uses_default_ssl_context(fake_mqtt) -> None:
    config = make_config(tls=True, port=8883)
    wrapper = MQTTClientWrapper("c1", config)
    wrapper.connect()
    client = wrapper.client
    assert client.tls_ca is None
    assert client.tls_context is not None


def test_no_tls_does_not_touch_tls_settings(fake_mqtt) -> None:
    config = make_config()
    wrapper = MQTTClientWrapper("c1", config)
    wrapper.connect()
    client = wrapper.client
    assert client.tls_ca is None
    assert client.tls_context is None
    assert client.tls_insecure is None


def test_wildcard_subscription_matches_and_dispatches(fake_mqtt) -> None:
    config = make_config()
    wrapper = MQTTClientWrapper("c1", config)
    received: list[tuple[str, str]] = []
    wrapper.subscribe(
        "homeassistant/sensor/evmqtt/+/switch/set",
        lambda topic, payload: received.append((topic, payload)),
    )
    wrapper.client.inject("homeassistant/sensor/evmqtt/kbd-a/switch/set", "ON")
    assert received == [("homeassistant/sensor/evmqtt/kbd-a/switch/set", "ON")]

    received.clear()
    wrapper.client.inject("homeassistant/sensor/evmqtt/kbd-a/switch/state", "ON")
    assert received == []


def test_hash_wildcard_subscription_matches_any_depth(fake_mqtt) -> None:
    config = make_config()
    wrapper = MQTTClientWrapper("c1", config)
    received: list[str] = []
    wrapper.subscribe(
        "homeassistant/sensor/evmqtt/#",
        lambda topic, payload: received.append(topic),
    )
    wrapper.client.inject("homeassistant/sensor/evmqtt/kbd-a/switch/set", "ON")
    assert received == ["homeassistant/sensor/evmqtt/kbd-a/switch/set"]


def test_malformed_utf8_payload_is_dropped_without_crash(fake_mqtt) -> None:
    config = make_config()
    wrapper = MQTTClientWrapper("c1", config)
    received: list[str] = []
    wrapper.subscribe("some/topic", lambda topic, payload: received.append(payload))
    wrapper.client.inject("some/topic", b"\xff\xfe\x00")
    assert received == []


def test_callback_exception_does_not_break_dispatch_loop(fake_mqtt) -> None:
    config = make_config()
    wrapper = MQTTClientWrapper("c1", config)
    calls: list[str] = []

    def boom(topic: str, payload: str) -> None:
        calls.append(payload)
        raise RuntimeError("boom")

    wrapper.subscribe("some/topic", boom)
    wrapper.client.inject("some/topic", "first")
    wrapper.client.inject("some/topic", "second")
    assert calls == ["first", "second"]
