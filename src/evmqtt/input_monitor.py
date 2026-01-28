"""Input device monitoring for evmqtt."""

from __future__ import annotations

import json
import logging
import threading
from typing import TYPE_CHECKING, Callable

import evdev

from evmqtt.key_handler import KeyHandler

if TYPE_CHECKING:
    from evmqtt.device_discovery import DiscoveredDevice
    from evmqtt.mqtt_client import MQTTClientWrapper

logger = logging.getLogger(__name__)


class InputMonitor(threading.Thread):
    """Monitor a Linux input device and publish events to MQTT.

    This class runs as a daemon thread, continuously reading events
    from an input device and publishing key presses to an MQTT topic.

    Supports Home Assistant autodiscovery for both the sensor (key events)
    and a switch entity that allows users to enable/disable monitoring
    from the Home Assistant UI.

    Attributes:
        device: The evdev InputDevice being monitored.
        state_topic: MQTT topic for publishing key events.
        config_topic: MQTT topic for sensor Home Assistant autodiscovery.
        switch_config_topic: MQTT topic for switch Home Assistant autodiscovery.
        switch_state_topic: MQTT topic for switch state.
        switch_command_topic: MQTT topic for switch commands.
        enabled: Whether this monitor is currently enabled.
    """

    def __init__(
        self,
        mqtt_client: MQTTClientWrapper,
        device_path: str,
        base_topic: str,
        gateway_name: str,
        key_handler: KeyHandler | None = None,
        device_slug: str | None = None,
        unique_id: str | None = None,
        initially_enabled: bool = True,
        on_enabled_change: Callable[[str, bool], None] | None = None,
    ) -> None:
        """Initialize the input monitor.

        Args:
            mqtt_client: MQTT client for publishing messages.
            device_path: Path to the input device (e.g., /dev/input/event0).
            base_topic: Base MQTT topic for this gateway.
            gateway_name: Display name for Home Assistant autodiscovery.
            key_handler: Optional KeyHandler instance (shared across monitors).
            device_slug: Optional slug for human-readable topic names.
            unique_id: Optional unique ID for this device.
            initially_enabled: Whether to start with monitoring enabled.
            on_enabled_change: Optional callback when enabled state changes.
        """
        super().__init__(daemon=True)
        self._mqtt_client = mqtt_client
        self.device = evdev.InputDevice(device_path)
        self._gateway_name = gateway_name
        self._key_handler = key_handler or KeyHandler()
        self._stop_event = threading.Event()
        self._on_enabled_change = on_enabled_change

        # Use slug-based topics if provided, otherwise use path-based
        if device_slug:
            topic_suffix = device_slug
            self._unique_id = unique_id or f"evmqtt_{device_slug}"
        else:
            # Fallback to old path-based naming
            topic_suffix = device_path.replace("/", "_")
            self._unique_id = f"evmqtt_{topic_suffix}"

        # Build topic paths with human-readable device names
        device_base_topic = f"{base_topic}/{topic_suffix}"
        self.state_topic = f"{device_base_topic}/state"
        self.config_topic = f"{device_base_topic}/config"

        # Switch topics for enable/disable control
        self.switch_config_topic = f"homeassistant/switch/{self._unique_id}/config"
        self.switch_state_topic = f"{device_base_topic}/switch/state"
        self.switch_command_topic = f"{device_base_topic}/switch/set"

        # Enabled state - can be controlled via MQTT switch
        self._enabled = initially_enabled
        self._enabled_lock = threading.Lock()

        logger.info(
            "Monitoring '%s' (%s) -> topic '%s' [%s]",
            self.device.name,
            device_path,
            self.state_topic,
            "enabled" if self._enabled else "disabled",
        )

    @property
    def enabled(self) -> bool:
        """Check if monitoring is enabled."""
        with self._enabled_lock:
            return self._enabled

    @enabled.setter
    def enabled(self, value: bool) -> None:
        """Set the enabled state."""
        with self._enabled_lock:
            if self._enabled != value:
                self._enabled = value
                logger.info(
                    "Monitor for '%s' %s",
                    self.device.name,
                    "enabled" if value else "disabled",
                )
                # Publish state change
                self._publish_switch_state()
                # Notify callback
                if self._on_enabled_change:
                    self._on_enabled_change(self.device.path, value)

    def setup_autodiscovery(self) -> None:
        """Publish Home Assistant autodiscovery configurations.

        This publishes both the sensor config (for key events) and
        the switch config (for enable/disable control).
        """
        self._publish_sensor_autodiscovery()
        self._publish_switch_autodiscovery()
        self._publish_switch_state()

    def _publish_sensor_autodiscovery(self) -> None:
        """Publish Home Assistant MQTT autodiscovery configuration for sensor."""
        # Clean up the device name for display
        display_name = f"{self._gateway_name} - {self.device.name}"

        config = {
            "name": display_name,
            "state_topic": self.state_topic,
            "icon": "mdi:keyboard",
            "unique_id": f"{self._unique_id}_sensor",
            "value_template": "{{ value_json.key }}",
            "json_attributes_topic": self.state_topic,
            "json_attributes_template": "{{ value_json | tojson }}",
            "device": {
                "identifiers": [self._unique_id],
                "name": self.device.name,
                "manufacturer": "evmqtt",
                "model": "Input Device",
                "sw_version": "1.0.0",
            },
        }
        config_json = json.dumps(config)
        self._mqtt_client.publish(self.config_topic, config_json, retain=True)
        logger.debug("Published sensor autodiscovery config to '%s'", self.config_topic)

    def _publish_switch_autodiscovery(self) -> None:
        """Publish Home Assistant MQTT autodiscovery configuration for switch."""
        display_name = f"{self.device.name} Enable"

        config = {
            "name": display_name,
            "state_topic": self.switch_state_topic,
            "command_topic": self.switch_command_topic,
            "icon": "mdi:toggle-switch",
            "unique_id": f"{self._unique_id}_switch",
            "payload_on": "ON",
            "payload_off": "OFF",
            "state_on": "ON",
            "state_off": "OFF",
            "device": {
                "identifiers": [self._unique_id],
                "name": self.device.name,
                "manufacturer": "evmqtt",
                "model": "Input Device",
                "sw_version": "1.0.0",
            },
        }
        config_json = json.dumps(config)
        self._mqtt_client.publish(self.switch_config_topic, config_json, retain=True)
        logger.debug(
            "Published switch autodiscovery config to '%s'", self.switch_config_topic
        )

    def _publish_switch_state(self) -> None:
        """Publish the current switch state to MQTT."""
        state = "ON" if self._enabled else "OFF"
        self._mqtt_client.publish(self.switch_state_topic, state, retain=True)
        logger.debug("Published switch state '%s' to '%s'", state, self.switch_state_topic)

    def handle_switch_command(self, payload: str) -> None:
        """Handle a switch command from MQTT.

        Args:
            payload: The command payload ("ON" or "OFF").
        """
        payload_upper = payload.upper().strip()
        if payload_upper == "ON":
            self.enabled = True
        elif payload_upper == "OFF":
            self.enabled = False
        else:
            logger.warning("Invalid switch command: %s", payload)

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

                # Only handle events if enabled
                if self.enabled:
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

    def cleanup_autodiscovery(self) -> None:
        """Remove autodiscovery configurations from MQTT.

        This publishes empty payloads to remove the entities from HA.
        """
        self._mqtt_client.publish(self.config_topic, "", retain=True)
        self._mqtt_client.publish(self.switch_config_topic, "", retain=True)
        logger.debug("Removed autodiscovery configs for '%s'", self.device.name)


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
