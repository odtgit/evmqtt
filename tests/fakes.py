"""Fakes for evdev and paho-mqtt used by scenario tests.

Real evdev InputEvent/categorize/ecodes are used throughout so the
categorize() quirks (e.g. tuple keycodes for aliased keys) surface
naturally instead of being mocked away.
"""

from __future__ import annotations

import errno
import queue
import socket
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar

import evdev
from evdev import ecodes
from paho.mqtt.client import ConnectFlags, DisconnectFlags, MQTTMessage
from paho.mqtt.packettypes import PacketTypes
from paho.mqtt.reasoncodes import ReasonCode

_UNPLUG = object()
_CLOSE = object()


class FakeInputDevice:
    """Stand-in for evdev.InputDevice backed by a queue of events."""

    def __init__(
        self,
        path: str,
        name: str = "Fake Device",
        capabilities: dict[int, list[int]] | None = None,
    ) -> None:
        self.path = path
        self.name = name
        self._capabilities = capabilities or {ecodes.EV_KEY: list(range(1, 250))}
        self._queue: queue.Queue[Any] = queue.Queue()
        self.grabbed = False
        self.closed = False
        self.grab_error: OSError | None = None

    # -- evdev.InputDevice surface -----------------------------------
    def capabilities(
        self, verbose: bool = False, absinfo: bool = True
    ) -> dict[int, list[int]]:
        return self._capabilities

    def grab(self) -> None:
        if self.grab_error is not None:
            raise self.grab_error
        self.grabbed = True

    def ungrab(self) -> None:
        self.grabbed = False

    def close(self) -> None:
        self.closed = True
        self._queue.put(_CLOSE)

    def read_loop(self):
        while True:
            item = self._queue.get()
            if item is _UNPLUG:
                raise OSError(errno.ENODEV, "No such device")
            if item is _CLOSE:
                raise OSError(errno.EBADF, "Bad file descriptor")
            yield item

    # -- test helpers --------------------------------------------------
    def push(self, event: evdev.InputEvent) -> None:
        self._queue.put(event)

    def push_all(self, events: list[evdev.InputEvent]) -> None:
        for event in events:
            self._queue.put(event)

    def unplug(self) -> None:
        self._queue.put(_UNPLUG)

    def fail_grab(self, exc: OSError | None = None) -> None:
        self.grab_error = exc or OSError(errno.EACCES, "Permission denied")


class FakeEvdevRegistry:
    """Registry mapping device paths to FakeInputDevice (or error) for evdev patches."""

    def __init__(self) -> None:
        self._devices: dict[str, FakeInputDevice] = {}
        self._errors: dict[str, type[OSError]] = {}

    def add(
        self,
        path: str,
        name: str = "Fake Device",
        capabilities: dict[int, list[int]] | None = None,
    ) -> FakeInputDevice:
        device = FakeInputDevice(path, name, capabilities)
        self._devices[path] = device
        return device

    def deny(self, path: str) -> None:
        self._errors[path] = PermissionError

    def open(self, path: str) -> FakeInputDevice:
        if path in self._errors:
            raise self._errors[path](path)
        if path not in self._devices:
            raise FileNotFoundError(path)
        return self._devices[path]

    def list_devices(self) -> list[str]:
        return sorted({*self._devices.keys(), *self._errors.keys()})

    def close_all(self) -> None:
        for device in self._devices.values():
            if not device.closed:
                device.close()


def keyboard_capabilities(*extra_codes: int) -> dict[int, list[int]]:
    """Capabilities dict for a device that supports key events."""
    codes = sorted({*range(ecodes.KEY_A, ecodes.KEY_Z + 1), *extra_codes})
    return {ecodes.EV_KEY: codes, ecodes.EV_SYN: [ecodes.SYN_REPORT]}


def mouse_capabilities() -> dict[int, list[int]]:
    """Capabilities dict for a device with no key events (e.g. accelerometer)."""
    return {
        ecodes.EV_REL: [ecodes.REL_X, ecodes.REL_Y],
        ecodes.EV_SYN: [ecodes.SYN_REPORT],
    }


# -- evdev event builders --------------------------------------------------

_seq = 0


def _next_ts() -> tuple[int, int]:
    global _seq
    _seq += 1
    return (0, _seq)


def key_event(name: str, state: int) -> evdev.InputEvent:
    """Build a real EV_KEY InputEvent for a named key at the given keystate."""
    code = getattr(ecodes, name)
    sec, usec = _next_ts()
    return evdev.InputEvent(sec, usec, ecodes.EV_KEY, code, state)


def press(name: str) -> evdev.InputEvent:
    return key_event(name, 1)


def release(name: str) -> evdev.InputEvent:
    return key_event(name, 0)


def hold(name: str) -> evdev.InputEvent:
    return key_event(name, 2)


def tap(name: str) -> list[evdev.InputEvent]:
    return [press(name), release(name)]


def syn_event() -> evdev.InputEvent:
    sec, usec = _next_ts()
    return evdev.InputEvent(sec, usec, ecodes.EV_SYN, ecodes.SYN_REPORT, 0)


def msc_event() -> evdev.InputEvent:
    sec, usec = _next_ts()
    return evdev.InputEvent(sec, usec, ecodes.EV_MSC, ecodes.MSC_SCAN, 0)


def rel_event() -> evdev.InputEvent:
    sec, usec = _next_ts()
    return evdev.InputEvent(sec, usec, ecodes.EV_REL, ecodes.REL_X, 1)


def wait_for(
    predicate: Callable[[], bool], timeout: float = 2.0, interval: float = 0.01
) -> bool:
    """Poll predicate() until it is truthy or timeout elapses."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


# -- fake paho-mqtt client ---------------------------------------------------


@dataclass
class PublishRecord:
    topic: str
    payload: Any
    qos: int
    retain: bool


class FakePahoClient:
    """Stand-in for paho.mqtt.client.Client.

    Each test gets a fresh subclass (see conftest.fake_mqtt) so class-level
    state (created instances, auto_connect) does not leak between tests.
    """

    created: ClassVar[list[FakePahoClient]] = []
    auto_connect: ClassVar[bool] = True
    connect_should_fail: ClassVar[bool] = False
    fail_hosts: ClassVar[set[str]] = set()

    def __init__(
        self,
        callback_api_version: Any = None,
        client_id: str = "",
        protocol: Any = None,
        **kwargs: Any,
    ) -> None:
        self.client_id = client_id
        self.on_connect: Callable[..., None] | None = None
        self.on_disconnect: Callable[..., None] | None = None
        self.on_message: Callable[..., None] | None = None
        self.published: list[PublishRecord] = []
        self.subscriptions: list[str] = []
        self.tls_ca: str | None = None
        self.tls_context = None
        self.tls_insecure = None
        self.host: str | None = None
        self.port: int | None = None
        self.connected = False
        self.username = None
        self.password = None
        type(self).created.append(self)

    def username_pw_set(self, username: str, password: str | None = None) -> None:
        self.username = username
        self.password = password

    def tls_set(self, ca_certs: str | None = None, **kwargs: Any) -> None:
        if ca_certs and not Path(ca_certs).is_file():
            raise FileNotFoundError(ca_certs)
        self.tls_ca = ca_certs

    def tls_set_context(self, context: Any = None) -> None:
        self.tls_context = context

    def tls_insecure_set(self, value: bool) -> None:
        self.tls_insecure = value

    def connect(self, host: str, port: int = 1883, *args: Any, **kwargs: Any) -> None:
        if host in type(self).fail_hosts:
            raise socket.gaierror(-2, "Name or service not known")
        self.host = host
        self.port = port

    def loop_start(self) -> None:
        if type(self).auto_connect:
            self.fire_connect(success=not type(self).connect_should_fail)

    def loop_stop(self) -> None:
        pass

    def disconnect(self) -> None:
        self.fire_disconnect(normal=True)

    def publish(
        self,
        topic: str,
        payload: Any = None,
        qos: int = 0,
        retain: bool = False,
    ) -> None:
        self.published.append(PublishRecord(topic, payload, qos, retain))

    def subscribe(self, topic: str, qos: int = 0) -> tuple[int, int]:
        self.subscriptions.append(topic)
        return (0, 0)

    def unsubscribe(self, topic: str) -> tuple[int, int]:
        if topic in self.subscriptions:
            self.subscriptions.remove(topic)
        return (0, 0)

    # -- test-driven callback triggers ---------------------------------
    def fire_connect(self, success: bool = True) -> None:
        reason = ReasonCode(PacketTypes.CONNACK, identifier=0 if success else 128)
        flags = ConnectFlags(session_present=False)
        self.connected = success
        if self.on_connect:
            self.on_connect(self, None, flags, reason, None)

    def fire_disconnect(self, normal: bool = True) -> None:
        reason = ReasonCode(PacketTypes.DISCONNECT, identifier=0 if normal else 137)
        flags = DisconnectFlags(is_disconnect_packet_from_server=not normal)
        self.connected = False
        if self.on_disconnect:
            self.on_disconnect(self, None, flags, reason, None)

    def drop(self) -> None:
        self.fire_disconnect(normal=False)

    def reconnect(self) -> None:
        self.fire_connect(success=True)

    def inject(self, topic: str, payload: str | bytes) -> None:
        message = MQTTMessage()
        message.topic = topic.encode()
        message.payload = payload.encode() if isinstance(payload, str) else payload
        if self.on_message:
            self.on_message(self, None, message)

    def published_to(self, topic: str) -> list[PublishRecord]:
        return [p for p in self.published if p.topic == topic]

    @classmethod
    def last(cls) -> FakePahoClient:
        return cls.created[-1]


def published(client: FakePahoClient, topic: str) -> list[PublishRecord]:
    """Return all publish records sent to `topic` on `client`."""
    return client.published_to(topic)


def make_fake_paho_class() -> type[FakePahoClient]:
    """Create a fresh FakePahoClient subclass with isolated class state."""

    class _Client(FakePahoClient):
        pass

    _Client.created = []
    _Client.auto_connect = True
    _Client.connect_should_fail = False
    _Client.fail_hosts = set()
    return _Client
