"""Input device monitoring for evmqtt."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import TYPE_CHECKING

import evmqtt
from evmqtt.core import (
    DeviceReader,
    GrabMode,
    KeyEvent,
    ReaderStopped,
    list_devices,
    open_device,
)
from evmqtt.key_handler import KeyHandler

if TYPE_CHECKING:
    from evmqtt.mqtt_client import MQTTClientWrapper

logger = logging.getLogger(__name__)


class InputMonitor:
    """Publish one input device's key events to MQTT.

    Wraps a core DeviceReader on the running asyncio loop. Supports Home
    Assistant autodiscovery for the sensor (key events) and a switch entity
    that enables/disables publishing from the Home Assistant UI.

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
        """Open the device and build topics.

        Args:
            mqtt_client: MQTT client for publishing messages.
            device_path: Path to the input device (e.g., /dev/input/event0).
            base_topic: Base MQTT topic for this gateway.
            gateway_name: Display name for Home Assistant autodiscovery.
            key_handler: Optional KeyHandler with the publish rules.
            device_slug: Optional slug for human-readable topic names.
            unique_id: Optional unique ID for this device.
            initially_enabled: Whether to start with publishing enabled.
            on_enabled_change: Optional callback when enabled state changes.
        """
        self._mqtt_client = mqtt_client
        self._gateway_name = gateway_name
        self._key_handler = key_handler or KeyHandler()
        self._on_enabled_change = on_enabled_change
        self.device = open_device(device_path)
        try:
            # 1.x grabs at start whether or not the switch is on. 4b: WHILE_ENABLED.
            self._reader = DeviceReader(
                self.device,
                self._handle_key_event,
                key_config=self._key_handler.key_config,
                grab=GrabMode.ALWAYS,
                enabled=initially_enabled,
            )
        except OSError:
            self.device.close()
            raise

        if device_slug:
            topic_suffix = device_slug
            self._unique_id = unique_id or f"evmqtt_{device_slug}"
        else:
            topic_suffix = device_path.replace("/", "_")
            self._unique_id = f"evmqtt_{topic_suffix}"

        device_base_topic = f"{base_topic}/{topic_suffix}"
        self.state_topic = f"{device_base_topic}/state"
        self.config_topic = f"{device_base_topic}/config"

        self.switch_config_topic = f"homeassistant/switch/{self._unique_id}/config"
        self.switch_state_topic = f"{device_base_topic}/switch/state"
        self.switch_command_topic = f"{device_base_topic}/switch/set"

        logger.info(
            "Monitoring '%s' (%s) -> topic '%s' [%s]",
            self.device.name,
            device_path,
            self.state_topic,
            "enabled" if initially_enabled else "disabled",
        )

    @property
    def enabled(self) -> bool:
        return self._reader.enabled

    @enabled.setter
    def enabled(self, value: bool) -> None:
        if self._reader.enabled == value:
            return
        self._reader.set_enabled(value)
        logger.info(
            "Monitor for '%s' %s",
            self.device.name,
            "enabled" if value else "disabled",
        )
        self._publish_switch_state()
        if self._on_enabled_change:
            self._on_enabled_change(self.device.path, value)

    @property
    def running(self) -> bool:
        return self._reader.running

    def setup_autodiscovery(self) -> None:
        """Publish Home Assistant sensor and switch discovery, and switch state."""
        self._publish_sensor_autodiscovery()
        self._publish_switch_autodiscovery()
        self._publish_switch_state()

    def _device_block(self) -> dict[str, object]:
        return {
            "identifiers": [self._unique_id],
            "name": self.device.name,
            "manufacturer": "evmqtt",
            "model": "Input Device",
            "sw_version": evmqtt.__version__,
        }

    def _publish_sensor_autodiscovery(self) -> None:
        config = {
            "name": f"{self._gateway_name} - {self.device.name}",
            "state_topic": self.state_topic,
            "icon": "mdi:keyboard",
            "unique_id": f"{self._unique_id}_sensor",
            "value_template": "{{ value_json.key }}",
            "json_attributes_topic": self.state_topic,
            "json_attributes_template": "{{ value_json | tojson }}",
            "device": self._device_block(),
        }
        self._mqtt_client.publish(self.config_topic, json.dumps(config), retain=True)
        logger.debug("Published sensor autodiscovery config to '%s'", self.config_topic)

    def _publish_switch_autodiscovery(self) -> None:
        config = {
            "name": f"{self.device.name} Enable",
            "state_topic": self.switch_state_topic,
            "command_topic": self.switch_command_topic,
            "icon": "mdi:toggle-switch",
            "unique_id": f"{self._unique_id}_switch",
            "payload_on": "ON",
            "payload_off": "OFF",
            "state_on": "ON",
            "state_off": "OFF",
            "device": self._device_block(),
        }
        self._mqtt_client.publish(
            self.switch_config_topic, json.dumps(config), retain=True
        )
        logger.debug(
            "Published switch autodiscovery config to '%s'", self.switch_config_topic
        )

    def _publish_switch_state(self) -> None:
        state = "ON" if self.enabled else "OFF"
        self._mqtt_client.publish(self.switch_state_topic, state, retain=True)
        logger.debug(
            "Published switch state '%s' to '%s'", state, self.switch_state_topic
        )

    def handle_switch_command(self, payload: str) -> None:
        """Apply an "ON"/"OFF" switch command. Runs on the loop thread."""
        payload_upper = payload.upper().strip()
        if payload_upper == "ON":
            self.enabled = True
        elif payload_upper == "OFF":
            self.enabled = False
        else:
            logger.warning("Invalid switch command: %s", payload)

    async def run(self) -> ReaderStopped | None:
        """Grab and publish until stop(), unplug or a read error.

        Returns None if the grab failed.
        """
        try:
            self._reader.start()
        except OSError as e:
            logger.error("Failed to grab device '%s': %s", self.device.path, e)
            return None
        logger.info("Grabbed device '%s'", self.device.path)
        try:
            return await self._reader.wait()
        finally:
            self._reader.close()

    def stop(self) -> None:
        logger.info("Stopping monitor for '%s'", self.device.path)
        self._reader.close()

    def _handle_key_event(self, event: KeyEvent) -> None:
        if not self._key_handler.should_publish(event.names, event.state):
            return
        message = {
            "key": self._key_handler.format_keycode(event.names)
            + self._key_handler.modifier_suffix(event.modifiers),
            "devicePath": self.device.path,
            "deviceName": self.device.name,
            "state": event.state.name,
        }
        message_json = json.dumps(message)
        self._mqtt_client.publish(self.state_topic, message_json)
        logger.debug("Published: %s", message_json)


def list_available_devices() -> list[dict[str, str]]:
    """List all available input devices as {'path', 'name'} dicts."""
    return [{"path": d.path, "name": d.name} for d in list_devices()]
