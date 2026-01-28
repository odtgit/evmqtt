"""MQTT client wrapper for evmqtt."""

from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING, Any, Callable

import paho.mqtt.client as mqtt

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
        on_connect_callback: Callable[..., None] | None = None,
        on_disconnect_callback: Callable[..., None] | None = None,
    ) -> None:
        """Initialize the MQTT client.

        Args:
            client_id: Unique identifier for this client.
            config: Configuration object containing MQTT connection details.
            on_connect_callback: Optional callback for connection events.
            on_disconnect_callback: Optional callback for disconnection events.
        """
        self.client_id = client_id
        self._config = config
        self._connected = threading.Event()
        self._subscriptions: dict[str, MessageCallback] = {}
        self._subscription_lock = threading.Lock()

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

        self.client.username_pw_set(config.username, config.password)

        # Set up callbacks
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_message = self._on_message

        if on_connect_callback:
            self._user_on_connect = on_connect_callback
        else:
            self._user_on_connect = None

        if on_disconnect_callback:
            self._user_on_disconnect = on_disconnect_callback
        else:
            self._user_on_disconnect = None

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

            # Re-subscribe to all topics after reconnection
            with self._subscription_lock:
                for topic in self._subscriptions:
                    logger.debug("Re-subscribing to '%s'", topic)
                    self.client.subscribe(topic)

        if self._user_on_connect:
            self._user_on_connect(client, userdata, flags, reason_code, properties)

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

        if self._user_on_disconnect:
            self._user_on_disconnect(client, userdata, flags, reason_code, properties)

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
                if self._topic_matches(subscribed_topic, topic):
                    try:
                        callback(topic, payload)
                    except Exception as e:
                        logger.error(
                            "Error in message callback for '%s': %s", topic, e
                        )
                    break

    @staticmethod
    def _topic_matches(pattern: str, topic: str) -> bool:
        """Check if a topic matches a subscription pattern.

        Supports MQTT wildcards:
        - # matches any number of levels
        - + matches exactly one level

        Args:
            pattern: The subscription pattern (may contain wildcards).
            topic: The actual topic to match.

        Returns:
            True if the topic matches the pattern.
        """
        pattern_parts = pattern.split("/")
        topic_parts = topic.split("/")

        pattern_idx = 0
        topic_idx = 0

        while pattern_idx < len(pattern_parts) and topic_idx < len(topic_parts):
            pattern_part = pattern_parts[pattern_idx]

            if pattern_part == "#":
                # # matches everything from here
                return True
            elif pattern_part == "+":
                # + matches exactly one level
                pattern_idx += 1
                topic_idx += 1
            elif pattern_part == topic_parts[topic_idx]:
                # Exact match
                pattern_idx += 1
                topic_idx += 1
            else:
                return False

        # Check if we've matched completely
        return pattern_idx == len(pattern_parts) and topic_idx == len(topic_parts)

    def connect(self) -> None:
        """Connect to the MQTT broker and start the network loop."""
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

    def unsubscribe(self, topic: str) -> None:
        """Unsubscribe from an MQTT topic.

        Args:
            topic: The topic to unsubscribe from.
        """
        with self._subscription_lock:
            if topic in self._subscriptions:
                del self._subscriptions[topic]

        if self._connected.is_set():
            self.client.unsubscribe(topic)
            logger.debug("Unsubscribed from '%s'", topic)

    def wait_for_connection(self, timeout: float = 10.0) -> bool:
        """Wait for the MQTT connection to be established.

        Args:
            timeout: Maximum time to wait in seconds.

        Returns:
            True if connected, False if timeout occurred.
        """
        return self._connected.wait(timeout=timeout)

    @property
    def is_connected(self) -> bool:
        """Check if the client is currently connected."""
        return self._connected.is_set()

    def __enter__(self) -> MQTTClientWrapper:
        """Context manager entry."""
        self.connect()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: Any,
    ) -> None:
        """Context manager exit."""
        self.disconnect()
