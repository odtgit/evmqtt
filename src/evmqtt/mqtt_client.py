"""paho-mqtt wrapper: background network thread, callbacks marshalled to asyncio."""

from __future__ import annotations

import asyncio
import logging
import ssl
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import paho.mqtt.client as mqtt
from paho.mqtt.client import topic_matches_sub
from paho.mqtt.enums import CallbackAPIVersion
from paho.mqtt.properties import Properties
from paho.mqtt.reasoncodes import ReasonCode

logger = logging.getLogger(__name__)

MessageCallback = Callable[[str, str, bool], None]


@dataclass(frozen=True)
class BrokerSettings:
    host: str
    port: int = 1883
    username: str | None = None
    password: str | None = None
    tls: bool = False
    tls_ca: str | None = None


@dataclass(frozen=True)
class Will:
    topic: str
    payload: str
    qos: int = 1
    retain: bool = True


class MQTTClientWrapper:
    """connect_async + loop_start: paho retries the broker with backoff forever.

    Every callback (connect, disconnect, messages) runs on the asyncio loop
    via call_soon_threadsafe, never on paho's thread. Publishing while
    disconnected is dropped; callers republish state on connect.
    """

    def __init__(
        self,
        client_id: str,
        settings: BrokerSettings,
        loop: asyncio.AbstractEventLoop,
        *,
        will: Will | None = None,
        on_connect: Callable[[], None] | None = None,
        on_disconnect: Callable[[], None] | None = None,
        min_delay: int = 1,
        max_delay: int = 60,
    ) -> None:
        self.client_id = client_id
        self.settings = settings
        self._loop = loop
        self._on_connect_cb = on_connect
        self._on_disconnect_cb = on_disconnect
        self._connected = threading.Event()
        self._subscriptions: dict[str, MessageCallback] = {}
        self._lock = threading.Lock()
        self._failures = 0
        self._started = False
        self._stopping = False

        self.client = mqtt.Client(
            callback_api_version=CallbackAPIVersion.VERSION2,
            client_id=client_id,
            protocol=mqtt.MQTTv311,
        )
        self.client.enable_logger(logging.getLogger("paho"))
        self.client.reconnect_delay_set(min_delay=min_delay, max_delay=max_delay)
        if settings.username:
            self.client.username_pw_set(settings.username, settings.password)
        if will is not None:
            self.client.will_set(will.topic, will.payload, will.qos, will.retain)
        if settings.tls or settings.tls_ca:
            if settings.tls_ca:
                self.client.tls_set(settings.tls_ca)
            else:
                self.client.tls_set_context(
                    ssl.create_default_context(purpose=ssl.Purpose.SERVER_AUTH)
                )
            self.client.tls_insecure_set(False)
        self.client.on_connect = self._on_connect
        self.client.on_connect_fail = self._on_connect_fail
        self.client.on_disconnect = self._on_disconnect
        self.client.on_message = self._on_message

    @property
    def is_connected(self) -> bool:
        return self._connected.is_set()

    def _call(self, func: Callable[..., None], *args: Any) -> None:
        try:
            self._loop.call_soon_threadsafe(func, *args)
        except RuntimeError:
            pass

    def _on_connect(
        self,
        client: mqtt.Client,
        userdata: Any,
        flags: mqtt.ConnectFlags,
        reason_code: ReasonCode,
        properties: Properties | None = None,
    ) -> None:
        if reason_code.is_failure:
            self._failures += 1
            logger.error(
                "MQTT broker %s:%d refused the connection: %s; retrying",
                self.settings.host,
                self.settings.port,
                reason_code,
            )
            return
        self._failures = 0
        logger.info(
            "Connected to MQTT broker %s:%d", self.settings.host, self.settings.port
        )
        with self._lock:
            topics = list(self._subscriptions)
        for topic in topics:
            self.client.subscribe(topic)
        self._connected.set()
        if self._on_connect_cb is not None:
            self._call(self._on_connect_cb)

    def _on_connect_fail(self, client: mqtt.Client, userdata: Any) -> None:
        self._failures += 1
        log = logger.warning if self._failures == 1 else logger.debug
        log(
            "MQTT broker %s:%d unreachable (attempt %d), retrying with backoff",
            self.settings.host,
            self.settings.port,
            self._failures,
        )

    def _on_disconnect(
        self,
        client: mqtt.Client,
        userdata: Any,
        flags: mqtt.DisconnectFlags,
        reason_code: ReasonCode,
        properties: Properties | None = None,
    ) -> None:
        was_connected = self._connected.is_set()
        self._connected.clear()
        if not was_connected:
            return
        if self._stopping:
            logger.info("Disconnected from MQTT broker")
            return
        logger.warning("Disconnected from MQTT broker: %s, reconnecting", reason_code)
        if self._on_disconnect_cb is not None:
            self._call(self._on_disconnect_cb)

    def _on_message(
        self, client: mqtt.Client, userdata: Any, message: mqtt.MQTTMessage
    ) -> None:
        topic = message.topic
        try:
            payload = message.payload.decode("utf-8")
        except UnicodeDecodeError:
            logger.warning("Dropping non-UTF-8 payload on '%s'", topic)
            return
        with self._lock:
            matches = [
                cb
                for sub, cb in self._subscriptions.items()
                if topic_matches_sub(sub, topic)
            ]
        for callback in matches[:1]:
            self._call(self._dispatch, callback, topic, payload, bool(message.retain))

    @staticmethod
    def _dispatch(
        callback: MessageCallback, topic: str, payload: str, retain: bool
    ) -> None:
        try:
            callback(topic, payload, retain)
        except Exception:
            logger.exception("Error handling message on '%s'", topic)

    def start(self) -> None:
        """Start connecting in the background. Never raises for an unreachable broker."""
        if self._started:
            return
        self._started = True
        logger.info(
            "Connecting to MQTT broker %s:%d as '%s'",
            self.settings.host,
            self.settings.port,
            self.client_id,
        )
        self.client.connect_async(self.settings.host, self.settings.port)
        self.client.loop_start()

    def stop(self) -> None:
        """Clean disconnect (no LWT) and join the network thread. Blocking."""
        self._stopping = True
        self.client.disconnect()
        self.client.loop_stop()
        self._connected.clear()

    def publish(
        self, topic: str, payload: str | bytes, qos: int = 0, retain: bool = False
    ) -> mqtt.MQTTMessageInfo | None:
        if not self._connected.is_set():
            logger.debug("Not connected, dropping publish to '%s'", topic)
            return None
        return self.client.publish(topic, payload, qos=qos, retain=retain)

    def subscribe(self, topic: str, callback: MessageCallback) -> None:
        with self._lock:
            self._subscriptions[topic] = callback
        if self._connected.is_set():
            self.client.subscribe(topic)

    def unsubscribe(self, topic: str) -> None:
        with self._lock:
            removed = self._subscriptions.pop(topic, None)
        if removed is not None and self._connected.is_set():
            self.client.unsubscribe(topic)
