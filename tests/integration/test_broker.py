"""The 2.0 MQTT contract against a real mosquitto broker.

Skipped when the mosquitto binary is not available. Run with `pytest -m broker`.
"""

from __future__ import annotations

import json
import shutil
import socket
import subprocess
import threading
import time
import uuid
from pathlib import Path

import paho.mqtt.client as mqtt
import pytest

from evmqtt.config import Config
from evmqtt.core import make_device_id
from evmqtt.gateway import Gateway
from evmqtt.mqtt_client import BrokerSettings, MQTTClientWrapper
from tests.fakes import drained, keyboard_capabilities, press, until

pytestmark = pytest.mark.broker

MOSQUITTO = shutil.which("mosquitto")
PHYS = "usb-0000:00:14.0-1/input0"


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class Broker:
    """Controls a real mosquitto process."""

    def __init__(self, conf_path: Path, port: int) -> None:
        self.conf_path = conf_path
        self.port = port
        self.process: subprocess.Popen | None = None

    def start(self) -> None:
        self.process = subprocess.Popen(
            ["mosquitto", "-c", str(self.conf_path)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            try:
                with socket.create_connection(("127.0.0.1", self.port), timeout=0.2):
                    return
            except OSError:
                time.sleep(0.05)
        raise RuntimeError(f"mosquitto did not start listening on {self.port}")

    def stop(self) -> None:
        if self.process is None:
            return
        self.process.terminate()
        try:
            self.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=5)
        self.process = None

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


class Observer:
    """A plain paho client recording (topic, payload, retain)."""

    def __init__(self, port: int, *topics: str) -> None:
        self.messages: list[tuple[str, str, bool]] = []
        self._lock = threading.Lock()
        self._topics = topics
        self.client = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            client_id=f"observer-{uuid.uuid4().hex[:8]}",
        )
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        self.subscribed = threading.Event()
        self.client.on_subscribe = lambda *a: self.subscribed.set()
        self.client.connect("127.0.0.1", port)
        self.client.loop_start()

    def _on_connect(self, client, userdata, flags, reason, props) -> None:
        for topic in self._topics:
            client.subscribe(topic, qos=1)

    def _on_message(self, client, userdata, message) -> None:
        with self._lock:
            self.messages.append(
                (message.topic, message.payload.decode(), bool(message.retain))
            )

    def payloads(self, topic: str) -> list[str]:
        with self._lock:
            return [p for t, p, _ in self.messages if t == topic]

    def last(self, topic: str) -> str | None:
        values = self.payloads(topic)
        return values[-1] if values else None

    def publish(self, topic: str, payload: str, retain: bool = False) -> None:
        self.client.publish(topic, payload, qos=1, retain=retain).wait_for_publish(5)

    def close(self) -> None:
        self.client.disconnect()
        self.client.loop_stop()


@pytest.fixture
def names() -> tuple[str, str]:
    tag = uuid.uuid4().hex[:8]
    return f"ha-{tag}", f"evmqtt-it/{tag}"


def make_config(
    broker: Broker, names: tuple[str, str], tmp_path: Path, **overrides: object
) -> Config:
    prefix, base = names
    data: dict[str, object] = {
        "mqtt_host": "127.0.0.1",
        "mqtt_port": broker.port,
        "discovery_prefix": prefix,
        "base_topic": base,
        "state_file": str(tmp_path / "state.json"),
        "rescan_interval": 0,
        "name": "IT Gateway",
    }
    data.update(overrides)
    return Config.from_dict(data)


def add_kbd(fake_evdev):
    return fake_evdev.add(
        "/dev/input/event0",
        name="Kbd A",
        capabilities=keyboard_capabilities(),
        phys=PHYS,
    )


DEV_ID = make_device_id("Kbd A", PHYS, "", 3, 0, 0)


async def started(config: Config) -> Gateway:
    gateway = Gateway(config, cleanup_window=0.5)
    await gateway.start()
    assert await until(
        lambda: gateway.mqtt is not None and gateway.mqtt.is_connected, 10
    )
    return gateway


async def test_lwt_on_unclean_disconnect_then_back_online(
    broker, names, fake_evdev, tmp_path
) -> None:
    add_kbd(fake_evdev)
    prefix, base = names
    observer = Observer(broker.port, f"{base}/status")
    gateway = await started(make_config(broker, names, tmp_path))
    try:
        assert await until(lambda: observer.last(f"{base}/status") == "online", 10)
        count = len(observer.payloads(f"{base}/status"))
        gateway.mqtt.client.socket().shutdown(socket.SHUT_RDWR)
        assert await until(
            lambda: "offline" in observer.payloads(f"{base}/status")[count:], 10
        )
        assert await until(
            lambda: (
                observer.payloads(f"{base}/status")[count:] == ["offline", "online"]
            ),
            15,
        )
    finally:
        await gateway.stop()
        assert await until(lambda: observer.last(f"{base}/status") == "offline", 10)
        observer.close()


async def test_retained_discovery_for_late_subscriber(
    broker, names, fake_evdev, tmp_path
) -> None:
    add_kbd(fake_evdev)
    prefix, base = names
    config = make_config(broker, names, tmp_path)
    gateway = await started(config)
    try:
        dev_topic = f"{prefix}/device/evmqtt_{config.node_id}_{DEV_ID}/config"
        gw_topic = f"{prefix}/device/evmqtt_{config.node_id}/config"
        probe = Observer(broker.port, f"{base}/{DEV_ID}/availability")
        assert await until(lambda: probe.last(f"{base}/{DEV_ID}/availability"), 10)
        probe.close()

        late = Observer(broker.port, f"{prefix}/device/+/config", f"{base}/#")
        try:
            expected = {
                dev_topic,
                gw_topic,
                f"{base}/status",
                f"{base}/{DEV_ID}/switch/state",
                f"{base}/{DEV_ID}/availability",
            }
            assert await until(
                lambda: expected <= {t for t, _, r in late.messages if r}, 10
            )
            payload = json.loads(late.last(dev_topic))
            assert payload["components"]["event"]["state_topic"] == (
                f"{base}/{DEV_ID}/event"
            )
            assert (
                payload["device"]["via_device"]
                == json.loads(late.last(gw_topic))["device"]["identifiers"][0]
            )
        finally:
            late.close()
    finally:
        await gateway.stop()


async def test_switch_roundtrip_and_events(broker, names, fake_evdev, tmp_path) -> None:
    device = add_kbd(fake_evdev)
    prefix, base = names
    dev = f"{base}/{DEV_ID}"
    observer = Observer(broker.port, f"{base}/#")
    gateway = await started(make_config(broker, names, tmp_path))
    try:
        assert await until(lambda: observer.last(f"{dev}/switch/state") == "ON", 10)
        device.push(press("KEY_A"))
        assert await until(lambda: observer.payloads(f"{dev}/event"), 10)
        event = json.loads(observer.last(f"{dev}/event"))
        assert (event["event_type"], event["key"]) == ("press", "KEY_A")

        observer.publish(f"{dev}/switch/set", "OFF")
        assert await until(lambda: observer.last(f"{dev}/switch/state") == "OFF", 10)
        assert not device.grabbed
        device.push(press("KEY_B"))
        await drained(device)

        observer.publish(f"{dev}/switch/set", "ON")
        assert await until(lambda: observer.last(f"{dev}/switch/state") == "ON", 10)
        assert device.grabbed
        device.push(press("KEY_C"))
        assert await until(lambda: len(observer.payloads(f"{dev}/event")) == 2, 10)
        keys = [json.loads(p)["key"] for p in observer.payloads(f"{dev}/event")]
        assert keys == ["KEY_A", "KEY_C"]
    finally:
        await gateway.stop()
        observer.close()
    assert not device.grabbed


async def test_persistence_roundtrip(broker, names, fake_evdev, tmp_path) -> None:
    device = add_kbd(fake_evdev)
    prefix, base = names
    dev = f"{base}/{DEV_ID}"
    config = make_config(broker, names, tmp_path)
    observer = Observer(broker.port, f"{base}/#")
    gateway = await started(config)
    try:
        assert await until(lambda: observer.last(f"{dev}/switch/state") == "ON", 10)
        observer.publish(f"{dev}/switch/set", "OFF")
        assert await until(lambda: not device.grabbed, 10)
    finally:
        await gateway.stop()
        observer.close()

    gateway = await started(config)
    late = Observer(broker.port, f"{dev}/switch/state")
    try:
        assert not device.grabbed
        assert await until(lambda: late.last(f"{dev}/switch/state") == "OFF", 10)
        assert json.loads(config.state_path.read_text())["devices"][DEV_ID] == {
            "enabled": False,
            "name": "Kbd A",
            "path": "/dev/input/event0",
        }
    finally:
        late.close()
        await gateway.stop()


async def test_legacy_cleanup_on_real_broker(
    broker, names, fake_evdev, tmp_path
) -> None:
    prefix, base = names
    legacy = f"{prefix}/sensor/evmqtt"
    seed = Observer(broker.port)
    old_sensor = f"{legacy}/kbd-a/config"
    old_switch = f"{prefix}/switch/evmqtt_kbd-a_event0/config"
    foreign = f"{prefix}/switch/plug/config"
    try:
        seed.publish(
            old_sensor,
            json.dumps(
                {
                    "unique_id": "evmqtt_kbd-a_event0_sensor",
                    "state_topic": f"{legacy}/kbd-a/state",
                }
            ),
            retain=True,
        )
        seed.publish(
            old_switch,
            json.dumps(
                {
                    "unique_id": "evmqtt_kbd-a_event0_switch",
                    "state_topic": f"{legacy}/kbd-a/switch/state",
                    "command_topic": f"{legacy}/kbd-a/switch/set",
                }
            ),
            retain=True,
        )
        seed.publish(f"{legacy}/kbd-a/switch/state", "ON", retain=True)
        seed.publish(foreign, json.dumps({"unique_id": "plug"}), retain=True)
    finally:
        seed.close()

    gateway = await started(make_config(broker, names, tmp_path))
    try:
        watcher = Observer(broker.port, f"{prefix}/#", f"{legacy}/#")
        try:
            assert await until(lambda: watcher.last(foreign), 10)
            assert await until(
                lambda: (
                    watcher.last(old_sensor) == "" or watcher.last(old_sensor) is None
                ),
                10,
            )
        finally:
            watcher.close()
        check = Observer(broker.port, f"{prefix}/#", f"{legacy}/#")
        try:
            assert await until(lambda: check.last(foreign), 10)
            retained = {t for t, _, r in check.messages if r}
            assert foreign in retained
            assert old_sensor not in retained
            assert old_switch not in retained
            assert f"{legacy}/kbd-a/switch/state" not in retained
        finally:
            check.close()
    finally:
        await gateway.stop()
        cleanup = Observer(broker.port)
        cleanup.publish(foreign, "", retain=True)
        cleanup.close()


async def test_broker_down_at_startup_then_up(
    broker, names, fake_evdev, tmp_path
) -> None:
    device = add_kbd(fake_evdev)
    prefix, base = names
    broker.stop()
    gateway = Gateway(make_config(broker, names, tmp_path), cleanup_window=0.2)
    await gateway.start()
    try:
        assert await until(lambda: gateway.mqtt is not None, 5)
        assert device.grabbed
        assert gateway.fatal_error is None
        broker.start()
        assert await until(lambda: gateway.mqtt.is_connected, 20)
        observer = Observer(broker.port, f"{base}/status")
        try:
            assert await until(lambda: observer.last(f"{base}/status") == "online", 10)
        finally:
            observer.close()
    finally:
        await gateway.stop()


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
        import asyncio

        async def run() -> None:
            connected: list[int] = []
            client = MQTTClientWrapper(
                "tls-test",
                BrokerSettings("localhost", port, tls=True, tls_ca=str(ca_path)),
                asyncio.get_running_loop(),
                on_connect=lambda: connected.append(1),
            )
            client.start()
            try:
                assert await until(lambda: connected, 10)
            finally:
                await asyncio.to_thread(client.stop)

        asyncio.run(run())
    finally:
        tls_broker.stop()
