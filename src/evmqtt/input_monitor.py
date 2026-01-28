"""Input device monitoring for evmqtt."""

from __future__ import annotations

import json
import logging
import threading
from typing import TYPE_CHECKING

import evdev

from evmqtt.key_handler import KeyHandler

if TYPE_CHECKING:
    from evmqtt.mqtt_client import MQTTClientWrapper

logger = logging.getLogger(__name__)


class InputMonitor(threading.Thread):
    """Monitor a Linux input device and publish events to MQTT.

    This class runs as a daemon thread, continuously reading events
    from an input device and publishing key presses to an MQTT topic.

    Attributes:
        device: The evdev InputDevice being monitored.
        state_topic: MQTT topic for publishing key events.
        config_topic: MQTT topic for Home Assistant autodiscovery.
    """

    def __init__(
        self,
        mqtt_client: MQTTClientWrapper,
        device_path: str,
        base_topic: str,
        gateway_name: str,
        key_handler: KeyHandler | None = None,
    ) -> None:
        """Initialize the input monitor.

        Args:
            mqtt_client: MQTT client for publishing messages.
            device_path: Path to the input device (e.g., /dev/input/event0).
            base_topic: Base MQTT topic for this gateway.
            gateway_name: Display name for Home Assistant autodiscovery.
            key_handler: Optional KeyHandler instance (shared across monitors).
        """
        super().__init__(daemon=True)
        self._mqtt_client = mqtt_client
        self.device = evdev.InputDevice(device_path)
        self.state_topic = f"{base_topic}/state"
        self.config_topic = f"{base_topic}/config"
        self._gateway_name = gateway_name
        self._key_handler = key_handler or KeyHandler()
        self._stop_event = threading.Event()

        # Publish autodiscovery configuration
        self._publish_autodiscovery()

        logger.info(
            "Monitoring '%s' (%s) -> topic '%s'",
            self.device.name,
            device_path,
            self.state_topic,
        )

    def _publish_autodiscovery(self) -> None:
        """Publish Home Assistant MQTT autodiscovery configuration."""
        config = {
            "name": self._gateway_name,
            "state_topic": self.state_topic,
            "icon": "mdi:code-json",
            "unique_id": f"evmqtt_{self.device.path.replace('/', '_')}",
        }
        config_json = json.dumps(config)
        self._mqtt_client.publish(self.config_topic, config_json, retain=True)
        logger.info("Published autodiscovery config to '%s'", self.config_topic)

    def run(self) -> None:
        """Main monitoring loop.

        Reads events from the input device and publishes key presses
        to the MQTT broker. This method runs until stop() is called.
        """
        try:
            # Grab the device to prevent events from reaching the console
            self.device.grab()
            logger.info("Grabbed device '%s'", self.device.path)
        except OSError as e:
            logger.error("Failed to grab device '%s': %s", self.device.path, e)
            return

        try:
            for event in self.device.read_loop():
                if self._stop_event.is_set():
                    break

                if event.type != evdev.ecodes.EV_KEY:
                    continue

                self._handle_key_event(event)

        except OSError as e:
            if not self._stop_event.is_set():
                logger.error("Error reading from device '%s': %s", self.device.path, e)
        finally:
            try:
                self.device.ungrab()
            except OSError:
                pass

    def _handle_key_event(self, event: evdev.InputEvent) -> None:
        """Process a key event and publish if appropriate.

        Args:
            event: The evdev InputEvent to process.
        """
        key_event = evdev.categorize(event)
        keycode = key_event.keycode
        keystate = key_event.keystate

        # Update modifier key state
        primary_key = keycode[0] if isinstance(keycode, list) else keycode
        self._key_handler.update_modifier_state(primary_key, keystate)

        # Check if this event should be published
        if not self._key_handler.should_publish(keycode, keystate):
            return

        # Build and publish the message
        formatted_key = self._key_handler.format_keycode(keycode)
        modifier_suffix = self._key_handler.get_modifier_suffix()

        message = {
            "key": formatted_key + modifier_suffix,
            "devicePath": self.device.path,
            "deviceName": self.device.name,
        }
        message_json = json.dumps(message)

        self._mqtt_client.publish(self.state_topic, message_json)
        logger.debug("Published: %s", message_json)

    def stop(self) -> None:
        """Signal the monitor to stop."""
        self._stop_event.set()
        logger.info("Stopping monitor for '%s'", self.device.path)


def list_available_devices() -> list[dict[str, str]]:
    """List all available input devices.

    Returns:
        List of dictionaries with 'path' and 'name' keys for each device.
    """
    devices = []
    for path in evdev.list_devices():
        try:
            device = evdev.InputDevice(path)
            devices.append({"path": device.path, "name": device.name})
        except OSError:
            continue
    return devices
