"""MQTT client wrapper for evmqtt."""

from __future__ import annotations

import asyncio
import logging
import ssl
import threading
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

import paho.mqtt.client as mqtt
from paho.mqtt.client import topic_matches_sub

if TYPE_CHECKING:
    from evmqtt.config import Config

logger = logging.getLogger(__name__)

# Type alias for message callback
MessageCallback = Callable[[str, str], None]


class MQTTClientWrapper:
    """Thread-safe MQTT client wrapper.

    Manages the connection to an MQTT broker and provides methods
    for publishing messages and subscribing to topics.

    Attributes:
        client_id: Unique identifier for this MQTT client.
        client: The underlying paho MQTT client instance.
    """

    def __init__(
        self,
        client_id: str,
        config: Config,
    ) -> None:
        """Initialize the MQTT client.

        Args:
            client_id: Unique identifier for this client.
            config: Configuration object containing MQTT connection details.
        """
        self.client_id = client_id
        self._config = config
        self._connected = threading.Event()
        self._subscriptions: dict[str, MessageCallback] = {}
        self._subscription_lock = threading.Lock()
        self._connect_waiters: list[Callable[[], None]] = []
        self._waiter_lock = threading.Lock()

        logger.info(
            "MQTT connecting to %s:%d as '%s'",
            config.serverip,
            config.port,
            client_id,
        )

        # paho-mqtt 2.0+ API
        self.client = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            client_id=client_id,
            protocol=mqtt.MQTTv311,
        )
        self.client.enable_logger(logging.getLogger("paho"))

        if config.username:
            self.client.username_pw_set(config.username, config.password)

        # Set up callbacks
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_message = self._on_message

    def _on_connect(
        self,
        client: mqtt.Client,
        userdata: Any,
        flags: mqtt.ConnectFlags,
        reason_code: mqtt.ReasonCode,
        properties: mqtt.Properties | None = None,
    ) -> None:
        """Handle MQTT connection events.

        Args:
            client: The MQTT client instance.
            userdata: User data (not used).
            flags: Connection flags.
            reason_code: Connection result code.
            properties: MQTT v5 properties (optional).
        """
        if reason_code.is_failure:
            logger.error("Failed to connect to MQTT broker: %s", reason_code)
        else:
            logger.info("Connected to MQTT broker successfully")
            self._connected.set()
            with self._waiter_lock:
                waiters = list(self._connect_waiters)
            for notify in waiters:
                notify()

            # Re-subscribe to all topics after reconnection
            with self._subscription_lock:
                for topic in self._subscriptions:
                    logger.debug("Re-subscribing to '%s'", topic)
                    self.client.subscribe(topic)

    def _on_disconnect(
        self,
        client: mqtt.Client,
        userdata: Any,
        flags: mqtt.DisconnectFlags,
        reason_code: mqtt.ReasonCode,
        properties: mqtt.Properties | None = None,
    ) -> None:
        """Handle MQTT disconnection events.

        Args:
            client: The MQTT client instance.
            userdata: User data (not used).
            flags: Disconnection flags.
            reason_code: Disconnection reason code.
            properties: MQTT v5 properties (optional).
        """
        self._connected.clear()
        logger.warning("Disconnected from MQTT broker: %s", reason_code)

    def _on_message(
        self,
        client: mqtt.Client,
        userdata: Any,
        message: mqtt.MQTTMessage,
    ) -> None:
        """Handle incoming MQTT messages.

        Args:
            client: The MQTT client instance.
            userdata: User data (not used).
            message: The received MQTT message.
        """
        topic = message.topic
        try:
            payload = message.payload.decode("utf-8")
        except UnicodeDecodeError:
            logger.warning("Could not decode message payload for topic '%s'", topic)
            return

        logger.debug("Received message on '%s': %s", topic, payload)

        # Find and call the matching callback
        with self._subscription_lock:
            for subscribed_topic, callback in self._subscriptions.items():
                if topic_matches_sub(subscribed_topic, topic):
                    try:
                        callback(topic, payload)
                    except Exception as e:
                        logger.error("Error in message callback for '%s': %s", topic, e)
                    break

    def connect(self) -> None:
        """Connect to the MQTT broker and start the network loop."""

        if self._config.tls or self._config.tls_ca:
            if self._config.tls_ca:
                self.client.tls_set(self._config.tls_ca)
            else:
                ssl_context = ssl.create_default_context(
                    purpose=ssl.Purpose.SERVER_AUTH
                )
                self.client.tls_set_context(ssl_context)
            self.client.tls_insecure_set(False)
        self.client.connect(self._config.serverip, self._config.port)
        self.client.loop_start()

    def disconnect(self) -> None:
        """Disconnect from the MQTT broker and stop the network loop."""
        self.client.loop_stop()
        self.client.disconnect()
        logger.info("Disconnected from MQTT broker")

    def publish(
        self,
        topic: str,
        payload: str | bytes,
        qos: int = 0,
        retain: bool = False,
    ) -> None:
        """Publish a message to an MQTT topic.

        Args:
            topic: The topic to publish to.
            payload: The message payload.
            qos: Quality of service level (0, 1, or 2).
            retain: Whether to retain the message on the broker.
        """
        self.client.publish(topic, payload, qos=qos, retain=retain)

    def subscribe(self, topic: str, callback: MessageCallback) -> None:
        """Subscribe to an MQTT topic.

        Args:
            topic: The topic to subscribe to (may include wildcards).
            callback: Function to call when a message is received.
                      Signature: callback(topic: str, payload: str) -> None
        """
        with self._subscription_lock:
            self._subscriptions[topic] = callback

        if self._connected.is_set():
            self.client.subscribe(topic)
            logger.debug("Subscribed to '%s'", topic)
        else:
            logger.debug("Queued subscription to '%s' (not yet connected)", topic)

    def wait_for_connection(self, timeout: float = 10.0) -> bool:
        """Wait for the MQTT connection to be established.

        Args:
            timeout: Maximum time to wait in seconds.

        Returns:
            True if connected, False if timeout occurred.
        """
        return self._connected.wait(timeout=timeout)

    async def async_wait_for_connection(self, timeout: float = 10.0) -> bool:
        """Like wait_for_connection, without blocking the event loop.

        on_connect runs on paho's network thread; it hops into the loop
        with call_soon_threadsafe.
        """
        loop = asyncio.get_running_loop()
        event = asyncio.Event()

        def notify() -> None:
            loop.call_soon_threadsafe(event.set)

        with self._waiter_lock:
            self._connect_waiters.append(notify)
        try:
            if self._connected.is_set():
                return True
            await asyncio.wait_for(event.wait(), timeout)
            return True
        except asyncio.TimeoutError:
            return False
        finally:
            with self._waiter_lock:
                self._connect_waiters.remove(notify)

    @property
    def is_connected(self) -> bool:
        """Check if the client is currently connected."""
        return self._connected.is_set()
