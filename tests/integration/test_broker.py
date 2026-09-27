"""Integration scenario tests against a real mosquitto broker.

Skipped by default when the mosquitto binary is not available. Run
explicitly with `pytest -m broker`.
"""

from __future__ import annotations

import shutil
import socket
import subprocess
import time
from pathlib import Path

import pytest

from evmqtt.config import Config
from evmqtt.mqtt_client import MQTTClientWrapper

pytestmark = pytest.mark.broker

MOSQUITTO = shutil.which("mosquitto")


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class Broker:
    """Controls a real mosquitto process for the duration of a test session."""

    def __init__(self, conf_path: Path, port: int) -> None:
        self.conf_path = conf_path
        self.port = port
        self.process: subprocess.Popen | None = None

    def start(self) -> None:
        self.process = subprocess.Popen(
            ["mosquitto", "-c", str(self.conf_path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self._wait_until_listening()

    def _wait_until_listening(self, timeout: float = 5.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                with socket.create_connection(("127.0.0.1", self.port), timeout=0.2):
                    return
            except OSError:
                time.sleep(0.05)
        raise RuntimeError(f"mosquitto did not start listening on {self.port} in time")

    def stop(self) -> None:
        if self.process is None:
            return
        self.process.terminate()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=5)
        self.process = None

    def restart(self) -> None:
        self.stop()
        self.start()

    def is_running(self) -> bool:
        return self.process is not None and self.process.poll() is None


@pytest.fixture(scope="session")
def broker(tmp_path_factory: pytest.TempPathFactory):
    if MOSQUITTO is None:
        pytest.skip("mosquitto binary not found")

    conf_dir = tmp_path_factory.mktemp("mosquitto")
    port = free_port()
    conf_path = conf_dir / "mosquitto.conf"
    conf_path.write_text(f"listener {port} 127.0.0.1\nallow_anonymous true\n")

    instance = Broker(conf_path, port)
    instance.start()
    yield instance
    instance.stop()


@pytest.fixture(autouse=True)
def _ensure_broker_running(broker: Broker) -> None:
    if not broker.is_running():
        broker.start()


def make_config(port: int, **overrides: object) -> Config:
    data: dict[str, object] = {
        "serverip": "127.0.0.1",
        "port": port,
        "name": "Gateway",
        "topic": "evmqtt-test",
        "devices": ["/dev/input/event0"],
    }
    data.update(overrides)
    return Config.from_dict(data)


def wait_for(predicate, timeout: float = 5.0, interval: float = 0.05) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


def test_discovery_retained_for_late_subscriber(broker: Broker) -> None:
    publisher = MQTTClientWrapper("pub", make_config(broker.port))
    publisher.connect()
    assert publisher.wait_for_connection(timeout=5.0)
    try:
        publisher.publish("evmqtt-test/discovery", '{"name": "kbd"}', retain=True)
        time.sleep(0.2)  # give the broker a moment to persist the retained message

        received: list[str] = []
        subscriber = MQTTClientWrapper("sub", make_config(broker.port))
        subscriber.connect()
        assert subscriber.wait_for_connection(timeout=5.0)
        try:
            subscriber.subscribe(
                "evmqtt-test/discovery", lambda topic, payload: received.append(payload)
            )
            assert wait_for(lambda: received)
            assert received == ['{"name": "kbd"}']
        finally:
            subscriber.disconnect()
    finally:
        publisher.disconnect()


def test_switch_roundtrip_via_broker(broker: Broker) -> None:
    app_side = MQTTClientWrapper("app", make_config(broker.port))
    app_side.connect()
    assert app_side.wait_for_connection(timeout=5.0)
    try:
        received: list[str] = []
        app_side.subscribe(
            "evmqtt-test/switch/set", lambda topic, payload: received.append(payload)
        )

        ha_side = MQTTClientWrapper("ha", make_config(broker.port))
        ha_side.connect()
        assert ha_side.wait_for_connection(timeout=5.0)
        try:
            ha_side.publish("evmqtt-test/switch/set", "OFF")
            assert wait_for(lambda: received)
            assert received == ["OFF"]
        finally:
            ha_side.disconnect()
    finally:
        app_side.disconnect()


def test_reconnect_after_broker_restart(broker: Broker) -> None:
    client = MQTTClientWrapper("reconnect-test", make_config(broker.port))
    client.connect()
    assert client.wait_for_connection(timeout=5.0)
    try:
        broker.restart()
        assert wait_for(lambda: client.is_connected, timeout=15.0)
    finally:
        client.disconnect()


def test_tls_connects_with_generated_ca(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    if MOSQUITTO is None:
        pytest.skip("mosquitto binary not found")
    trustme = pytest.importorskip("trustme")

    conf_dir = tmp_path_factory.mktemp("mosquitto-tls")
    ca = trustme.CA()
    ca_path = conf_dir / "ca.pem"
    ca.cert_pem.write_to_path(str(ca_path))

    server_cert = ca.issue_cert("localhost", "127.0.0.1")
    cert_path = conf_dir / "server.pem"
    key_path = conf_dir / "server.key"
    server_cert.cert_chain_pems[0].write_to_path(str(cert_path))
    server_cert.private_key_pem.write_to_path(str(key_path))

    port = free_port()
    conf_path = conf_dir / "mosquitto.conf"
    conf_path.write_text(
        f"listener {port} 127.0.0.1\n"
        f"cafile {ca_path}\n"
        f"certfile {cert_path}\n"
        f"keyfile {key_path}\n"
        "allow_anonymous true\n"
    )

    tls_broker = Broker(conf_path, port)
    tls_broker.start()
    try:
        config = make_config(port, serverip="localhost", tls_ca=str(ca_path))
        client = MQTTClientWrapper("tls-test", config)
        client.connect()
        try:
            assert client.wait_for_connection(timeout=5.0)
        finally:
            client.disconnect()
    finally:
        tls_broker.stop()
