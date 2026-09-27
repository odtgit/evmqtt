"""Fakes for evdev and paho-mqtt used by scenario tests.

Real evdev InputEvent/categorize/ecodes are used throughout so the
categorize() quirks (e.g. tuple keycodes for aliased keys) surface
naturally instead of being mocked away.
"""

from __future__ import annotations

import asyncio
import errno
import os
import queue
import select
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar

import evdev
from evdev import ecodes
from paho.mqtt.client import (
    ConnectFlags,
    DisconnectFlags,
    MQTTMessage,
    topic_matches_sub,
)
from paho.mqtt.packettypes import PacketTypes
from paho.mqtt.reasoncodes import ReasonCode


class FakeInputDevice:
    """Stand-in for evdev.InputDevice backed by a real pipe fd and a queue.

    .fd is a real, selectable file descriptor (the read end of a pipe) so
    InputMonitor's select()-based read loop behaves the same way it does
    against a real device. push()/unplug() write a byte to make the fd
    readable; read() drains the queue (or raises the queued OSError for
    unplug). close() only releases the fds -- on Linux, closing an fd does
    NOT wake another thread blocked in select() on it, so close() must not
    (and does not) simulate a wakeup here.
    """

    def __init__(
        self,
        path: str,
        name: str = "Fake Device",
        capabilities: dict[int, list[int]] | None = None,
        phys: str = "",
        uniq: str = "",
        bustype: int = 0x03,
        vendor: int = 0,
        product: int = 0,
        version: int = 0,
    ) -> None:
        self.path = path
        self.name = name
        self.phys = phys
        self.uniq = uniq
        self.info = evdev.DeviceInfo(bustype, vendor, product, version)
        self._capabilities = capabilities or {ecodes.EV_KEY: list(range(1, 250))}
        self._queue: queue.Queue[Any] = queue.Queue()
        self.grabbed = False
        self.grab_calls = 0
        self.closed = False
        self.opens = 0
        self.grab_error: OSError | None = None
        self._pipe_r, self._pipe_w = os.pipe()
        self.fd = self._pipe_r

    # -- evdev.InputDevice surface -----------------------------------
    def capabilities(
        self, verbose: bool = False, absinfo: bool = True
    ) -> dict[int, list[int]]:
        return self._capabilities

    def grab(self) -> None:
        if self.grab_error is not None:
            raise self.grab_error
        if self.grabbed:
            raise OSError(errno.EBUSY, "Device or resource busy")
        self.grabbed = True
        self.grab_calls += 1

    def ungrab(self) -> None:
        self.grabbed = False

    def close(self) -> None:
        """Release the fds. Does not wake a thread blocked in select().

        Each registry open() is a separate handle; the fds go on the last close.
        """
        if self.closed:
            return
        self.opens -= 1
        if self.opens > 0:
            return
        self.closed = True
        for fd in (self._pipe_r, self._pipe_w):
            try:
                os.close(fd)
            except OSError:
                pass

    def read(self) -> list[evdev.InputEvent]:
        """Mimic evdev.InputDevice.read(): drain queued events or raise."""
        try:
            os.read(self._pipe_r, 4096)
        except OSError:
            pass

        events: list[evdev.InputEvent] = []
        pending_error: OSError | None = None
        while True:
            try:
                item = self._queue.get_nowait()
            except queue.Empty:
                break
            if isinstance(item, OSError):
                pending_error = item
                break
            events.append(item)

        if pending_error is not None:
            if events:
                # Deliver what we already have; raise on the next read().
                # A dead device node stays readable, so keep the fd hot.
                self._queue.put(pending_error)
                self._wake()
            else:
                raise pending_error

        if not events:
            raise BlockingIOError()
        return events

    # -- test helpers --------------------------------------------------
    def reopen(self) -> None:
        if self.closed:
            self._pipe_r, self._pipe_w = os.pipe()
            self.fd = self._pipe_r
            self.closed = False
        self.opens += 1

    @property
    def idle(self) -> bool:
        """No queued events and nothing left to read on the fd."""
        if not self._queue.empty():
            return False
        if self.closed:
            return True
        ready, _, _ = select.select([self._pipe_r], [], [], 0)
        return not ready

    def push(self, event: evdev.InputEvent) -> None:
        self._queue.put(event)
        self._wake()

    def push_all(self, events: list[evdev.InputEvent]) -> None:
        for event in events:
            self.push(event)

    def unplug(self) -> None:
        self._queue.put(OSError(errno.ENODEV, "No such device"))
        self._wake()

    def fail_grab(self, exc: OSError | None = None) -> None:
        self.grab_error = exc or OSError(errno.EACCES, "Permission denied")

    def _wake(self) -> None:
        try:
            os.write(self._pipe_w, b"x")
        except OSError:
            pass


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
        **attrs: Any,
    ) -> FakeInputDevice:
        device = FakeInputDevice(path, name, capabilities, **attrs)
        self._devices[path] = device
        return device

    def remove(self, path: str) -> FakeInputDevice:
        return self._devices.pop(path)

    def deny(self, path: str) -> None:
        self._errors[path] = PermissionError

    def open(self, path: str) -> FakeInputDevice:
        if path in self._errors:
            raise self._errors[path](path)
        if path not in self._devices:
            raise FileNotFoundError(path)
        device = self._devices[path]
        device.reopen()
        return device

    def list_devices(self) -> list[str]:
        return sorted({*self._devices.keys(), *self._errors.keys()})

    def close_all(self) -> None:
        for device in self._devices.values():
            device.opens = min(device.opens, 1)
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


async def until(predicate: Callable[[], object], timeout: float = 2.0) -> bool:
    """Yield to the loop until predicate() is truthy. timeout is a hang guard."""
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            return False
        await asyncio.sleep(0)
    return True


async def drained(*devices: FakeInputDevice) -> None:
    """Return once the loop has read everything pushed to these devices."""
    assert await until(lambda: all(d.idle for d in devices))
    await asyncio.sleep(0)


# -- fake paho-mqtt client ---------------------------------------------------


@dataclass
class PublishRecord:
    topic: str
    payload: Any
    qos: int
    retain: bool


class FakePublishInfo:
    rc = 0

    def wait_for_publish(self, timeout: float | None = None) -> None:
        pass

    def is_published(self) -> bool:
        return True


class FakePahoClient:
    """Stand-in for paho.mqtt.client.Client with a tiny in-memory broker.

    Each test gets a fresh subclass (see conftest.fake_mqtt); `retained` is
    class-level so it outlives one client, like a broker across restarts.
    subscribe() delivers matching retained messages synchronously; an unclean
    drop() stores the will, a clean disconnect() does not.
    """

    created: ClassVar[list[FakePahoClient]] = []
    auto_connect: ClassVar[bool] = True
    connect_should_fail: ClassVar[bool] = False
    retained: ClassVar[dict[str, Any]] = {}

    def __init__(
        self,
        callback_api_version: Any = None,
        client_id: str = "",
        protocol: Any = None,
        **kwargs: Any,
    ) -> None:
        self.client_id = client_id
        self.on_connect: Callable[..., None] | None = None
        self.on_connect_fail: Callable[..., None] | None = None
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
        self.will: PublishRecord | None = None
        self.reconnect_delay: tuple[int, int] | None = None
        self.loop_running = False
        type(self).created.append(self)

    def username_pw_set(self, username: str, password: str | None = None) -> None:
        self.username = username
        self.password = password

    def enable_logger(self, logger: Any = None) -> None:
        pass

    def reconnect_delay_set(self, min_delay: int = 1, max_delay: int = 120) -> None:
        self.reconnect_delay = (min_delay, max_delay)

    def will_set(
        self, topic: str, payload: Any = None, qos: int = 0, retain: bool = False
    ) -> None:
        self.will = PublishRecord(topic, payload, qos, retain)

    def tls_set(self, ca_certs: str | None = None, **kwargs: Any) -> None:
        if ca_certs and not Path(ca_certs).is_file():
            raise FileNotFoundError(ca_certs)
        self.tls_ca = ca_certs

    def tls_set_context(self, context: Any = None) -> None:
        self.tls_context = context

    def tls_insecure_set(self, value: bool) -> None:
        self.tls_insecure = value

    def connect_async(self, host: str, port: int = 1883, *args: Any, **kw: Any) -> None:
        self.host = host
        self.port = port

    def loop_start(self) -> None:
        self.loop_running = True
        if type(self).auto_connect:
            self.fire_connect(success=not type(self).connect_should_fail)

    def loop_stop(self) -> None:
        self.loop_running = False

    def disconnect(self) -> None:
        if self.connected:
            self.fire_disconnect(normal=True)

    def publish(
        self,
        topic: str,
        payload: Any = None,
        qos: int = 0,
        retain: bool = False,
    ) -> FakePublishInfo:
        self.published.append(PublishRecord(topic, payload, qos, retain))
        if retain:
            self._retain(topic, payload)
        return FakePublishInfo()

    def subscribe(self, topic: str, qos: int = 0) -> tuple[int, int]:
        self.subscriptions.append(topic)
        for retained_topic, payload in list(type(self).retained.items()):
            if topic_matches_sub(topic, retained_topic):
                self.inject(retained_topic, payload, retain=True)
        return (0, 0)

    def unsubscribe(self, topic: str) -> tuple[int, int]:
        if topic in self.subscriptions:
            self.subscriptions.remove(topic)
        return (0, 0)

    @classmethod
    def _retain(cls, topic: str, payload: Any) -> None:
        if payload in (None, "", b""):
            cls.retained.pop(topic, None)
        else:
            cls.retained[topic] = payload

    # -- test-driven callback triggers ---------------------------------
    def fire_connect(self, success: bool = True) -> None:
        reason = ReasonCode(PacketTypes.CONNACK, identifier=0 if success else 135)
        flags = ConnectFlags(session_present=False)
        self.connected = success
        if self.on_connect:
            self.on_connect(self, None, flags, reason, None)
        if not success:
            self.fire_disconnect(normal=False)

    def fail_connect(self) -> None:
        """One failed TCP attempt, as paho's retry loop reports it."""
        if self.on_connect_fail:
            self.on_connect_fail(self, None)

    def fire_disconnect(self, normal: bool = True) -> None:
        reason = ReasonCode(PacketTypes.DISCONNECT, identifier=0 if normal else 137)
        flags = DisconnectFlags(is_disconnect_packet_from_server=not normal)
        self.connected = False
        if self.on_disconnect:
            self.on_disconnect(self, None, flags, reason, None)

    def drop(self) -> None:
        """Unclean connection loss: the broker publishes the will."""
        if self.will is not None and self.will.retain:
            self._retain(self.will.topic, self.will.payload)
        self.fire_disconnect(normal=False)

    def reconnect(self) -> None:
        self.fire_connect(success=True)

    def inject(self, topic: str, payload: str | bytes, retain: bool = False) -> None:
        message = MQTTMessage()
        message.topic = topic.encode()
        message.payload = payload.encode() if isinstance(payload, str) else payload
        message.retain = retain
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
    _Client.retained = {}
    return _Client


class FakeSysfs:
    """A /sys tree with just what evmqtt.sysinfo reads."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self._n = 0

    def add(
        self,
        event: str,
        *,
        virtual: bool = False,
        manufacturer: str | None = None,
        product: str | None = None,
    ) -> Path:
        self._n += 1
        if virtual:
            parent = self.root / "devices" / "virtual" / "input"
        else:
            usb = self.root / "devices" / "pci0000:00" / "usb1" / f"1-{self._n}"
            usb.mkdir(parents=True, exist_ok=True)
            if manufacturer:
                (usb / "manufacturer").write_text(manufacturer + "\n")
            if product:
                (usb / "product").write_text(product + "\n")
            parent = usb / f"1-{self._n}:1.0" / "input"
        node = parent / f"input{self._n}"
        node.mkdir(parents=True, exist_ok=True)
        cls = self.root / "class" / "input" / event
        cls.mkdir(parents=True, exist_ok=True)
        (cls / "device").symlink_to(node)
        return node
