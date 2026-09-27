"""MQTTClientWrapper: TLS setup, will, inbound routing onto the loop, robustness."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from evmqtt.mqtt_client import BrokerSettings, MQTTClientWrapper, Will
from tests.fakes import until


def make(settings: BrokerSettings | None = None, **kw) -> MQTTClientWrapper:
    return MQTTClientWrapper(
        "c1",
        settings or BrokerSettings("broker.local"),
        asyncio.get_running_loop(),
        **kw,
    )


async def test_empty_username_skips_username_pw_set(fake_mqtt) -> None:
    wrapper = make()
    assert wrapper.client.username is None
    assert wrapper.client.reconnect_delay == (1, 60)


async def test_credentials_and_will(fake_mqtt) -> None:
    wrapper = make(
        BrokerSettings("b", 1884, "u", "p"), will=Will("gw/status", "offline")
    )
    assert (wrapper.client.username, wrapper.client.password) == ("u", "p")
    will = wrapper.client.will
    assert (will.topic, will.payload, will.qos, will.retain) == (
        "gw/status",
        "offline",
        1,
        True,
    )
    wrapper.start()
    wrapper.start()
    assert (wrapper.client.host, wrapper.client.port) == ("b", 1884)
    assert len(fake_mqtt.created) == 1


async def test_tls_ca_path_is_passed_to_client(fake_mqtt, tmp_path: Path) -> None:
    ca_path = tmp_path / "ca.pem"
    ca_path.write_text("fake-ca")
    wrapper = make(BrokerSettings("b", 8883, tls_ca=str(ca_path)))
    assert wrapper.client.tls_ca == str(ca_path)
    assert wrapper.client.tls_insecure is False


async def test_missing_tls_ca_raises(fake_mqtt, tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        make(BrokerSettings("b", 8883, tls_ca=str(tmp_path / "nope.pem")))


async def test_tls_without_ca_uses_default_ssl_context(fake_mqtt) -> None:
    wrapper = make(BrokerSettings("b", 8883, tls=True))
    assert wrapper.client.tls_ca is None
    assert wrapper.client.tls_context is not None


async def test_no_tls_does_not_touch_tls_settings(fake_mqtt) -> None:
    wrapper = make()
    assert wrapper.client.tls_ca is None
    assert wrapper.client.tls_context is None
    assert wrapper.client.tls_insecure is None


async def test_publish_while_disconnected_is_dropped(fake_mqtt) -> None:
    fake_mqtt.auto_connect = False
    wrapper = make()
    wrapper.start()
    assert wrapper.publish("t", "x") is None
    assert wrapper.client.published == []


async def test_callbacks_run_on_the_loop(fake_mqtt) -> None:
    connects: list[int] = []
    disconnects: list[int] = []
    wrapper = make(
        on_connect=lambda: connects.append(1),
        on_disconnect=lambda: disconnects.append(1),
    )
    received: list[tuple[str, str, bool]] = []
    wrapper.subscribe("a/+/set", lambda *args: received.append(args))
    wrapper.start()
    assert await until(lambda: connects == [1])
    assert wrapper.client.subscriptions == ["a/+/set"]
    wrapper.client.inject("a/x/set", "ON")
    wrapper.client.inject("a/x/state", "ON")
    wrapper.client.inject("a/y/set", b"\xff\xfe")
    assert await until(lambda: received)
    await asyncio.sleep(0)
    assert received == [("a/x/set", "ON", False)]

    wrapper.client.drop()
    assert await until(lambda: disconnects == [1])
    wrapper.client.fire_disconnect()
    await asyncio.sleep(0)
    assert disconnects == [1]
    wrapper.unsubscribe("a/+/set")
    wrapper.unsubscribe("a/+/set")
    wrapper.client.reconnect()
    assert wrapper.client.subscriptions == ["a/+/set"]


async def test_callback_exception_does_not_break_dispatch(fake_mqtt) -> None:
    wrapper = make()
    calls: list[str] = []

    def boom(topic: str, payload: str, retain: bool) -> None:
        calls.append(payload)
        raise RuntimeError("boom")

    wrapper.subscribe("some/topic", boom)
    wrapper.start()
    wrapper.client.inject("some/topic", "first")
    wrapper.client.inject("some/topic", "second", retain=True)
    assert await until(lambda: calls == ["first", "second"])


async def test_stop_disconnects_cleanly(fake_mqtt) -> None:
    wrapper = make(will=Will("s", "offline"))
    wrapper.start()
    wrapper.stop()
    assert not wrapper.is_connected
    assert not wrapper.client.loop_running
    assert "s" not in fake_mqtt.retained
