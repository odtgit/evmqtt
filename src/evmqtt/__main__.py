"""Main entry point for evmqtt.

This module provides the command-line interface for the evmqtt gateway.
It can be run with:
    python -m evmqtt
    evmqtt (if installed as a package)
"""

from __future__ import annotations

import argparse
import logging
import signal
import sys
from platform import node as hostname
from time import time
from typing import NoReturn

from evmqtt.config import Config
from evmqtt.device_discovery import DiscoveredDevice, discover_devices
from evmqtt.input_monitor import InputMonitor, list_available_devices
from evmqtt.key_handler import KeyHandler
from evmqtt.mqtt_client import MQTTClientWrapper

logger = logging.getLogger(__name__)


def setup_logging(verbose: bool = False, debug: bool = False) -> None:
    """Configure logging for the application.

    Args:
        verbose: Enable verbose (INFO) logging.
        debug: Enable debug (DEBUG) logging.
    """
    if debug:
        level = logging.DEBUG
    elif verbose:
        level = logging.INFO
    else:
        level = logging.WARNING

    logging.basicConfig(
        level=level,
        format="[%(asctime)s] %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stderr,
    )


def generate_client_id() -> str:
    """Generate a unique MQTT client ID.

    Returns:
        Client ID string based on hostname and current time.
    """
    return f"evmqtt_{hostname()}_{int(time())}"


def parse_args() -> argparse.Namespace:
    """Parse command line arguments.

    Returns:
        Parsed arguments namespace.
    """
    parser = argparse.ArgumentParser(
        prog="evmqtt",
        description="Linux input event to MQTT gateway",
    )
    parser.add_argument(
        "-c",
        "--config",
        help="Path to configuration file (default: config.json)",
        default=None,
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable verbose logging",
    )
    parser.add_argument(
        "-d",
        "--debug",
        action="store_true",
        help="Enable debug logging",
    )
    parser.add_argument(
        "--list-devices",
        action="store_true",
        help="List available input devices and exit",
    )
    parser.add_argument(
        "--auto-discover",
        action="store_true",
        help="Override config to enable auto-discovery of all input devices",
    )
    return parser.parse_args()


def list_devices_and_exit() -> NoReturn:
    """List available input devices and exit."""
    devices = list_available_devices()
    if not devices:
        print("No input devices found.")
        sys.exit(1)

    print(f"Found {len(devices)} input device(s):")
    for device in devices:
        print(f"  Path: {device['path']}, Name: {device['name']}")
    sys.exit(0)


class Application:
    """Main application controller.

    Manages the lifecycle of the MQTT client and input monitors.
    Supports automatic device discovery with enable/disable switches
    controllable from Home Assistant.
    """

    def __init__(self, config: Config) -> None:
        """Initialize the application.

        Args:
            config: Application configuration.
        """
        self._config = config
        self._mqtt_client: MQTTClientWrapper | None = None
        self._monitors: list[InputMonitor] = []
        self._monitors_by_path: dict[str, InputMonitor] = {}
        self._key_handler = KeyHandler()
        self._shutdown_requested = False

    def start(self) -> None:
        """Start the application."""
        # Set up signal handlers
        signal.signal(signal.SIGINT, self._handle_signal)
        signal.signal(signal.SIGTERM, self._handle_signal)

        # Create MQTT client
        client_id = generate_client_id()
        self._mqtt_client = MQTTClientWrapper(client_id, self._config)
        self._mqtt_client.connect()

        # Wait for connection
        if not self._mqtt_client.wait_for_connection(timeout=30.0):
            logger.error("Failed to connect to MQTT broker within timeout")
            raise ConnectionError("MQTT connection timeout")

        # Create input monitors
        if self._config.auto_discover:
            self._setup_auto_discovery()
        else:
            self._setup_manual_devices()

        if not self._monitors:
            raise RuntimeError("No input devices could be opened")

        # Subscribe to switch command topics for all monitors
        self._setup_switch_subscriptions()

        # Start all monitors
        for monitor in self._monitors:
            monitor.start()

        logger.info("Application started with %d monitor(s)", len(self._monitors))

    def _setup_auto_discovery(self) -> None:
        """Set up monitors for all discovered devices."""
        logger.info("Auto-discovering input devices...")
        discovered = discover_devices(filter_keys_only=self._config.filter_keys_only)

        if not discovered:
            logger.warning("No input devices discovered")
            return

        logger.info("Discovered %d input device(s):", len(discovered))
        for device in discovered:
            logger.info(
                "  %s (%s) -> %s",
                device.name,
                device.path,
                device.slug,
            )

        # Determine which devices should be enabled
        # If enabled_devices is empty, all devices start enabled
        # Otherwise, only devices in enabled_devices are enabled
        enabled_paths = set(self._config.enabled_devices)
        all_enabled = not enabled_paths  # Empty list means all enabled

        for device in discovered:
            initially_enabled = all_enabled or device.path in enabled_paths
            self._create_monitor_for_device(device, initially_enabled)

    def _setup_manual_devices(self) -> None:
        """Set up monitors for manually specified devices."""
        # List available devices for reference
        available_devices = list_available_devices()
        logger.info("Found %d available input device(s):", len(available_devices))
        for device in available_devices:
            logger.info("  Path: %s, Name: %s", device["path"], device["name"])

        for device_path in self._config.devices:
            try:
                # For manual setup, use the old-style monitor without slug
                monitor = InputMonitor(
                    mqtt_client=self._mqtt_client,
                    device_path=device_path,
                    base_topic=self._config.topic,
                    gateway_name=self._config.name,
                    key_handler=self._key_handler,
                    initially_enabled=True,
                    on_enabled_change=self._on_device_enabled_change,
                )
                monitor.setup_autodiscovery()
                self._monitors.append(monitor)
                self._monitors_by_path[device_path] = monitor
            except FileNotFoundError:
                logger.error("Device not found: %s", device_path)
            except PermissionError:
                logger.error("Permission denied for device: %s", device_path)
            except OSError as e:
                logger.error("Error opening device %s: %s", device_path, e)

    def _create_monitor_for_device(
        self, device: DiscoveredDevice, initially_enabled: bool
    ) -> None:
        """Create and configure an InputMonitor for a discovered device.

        Args:
            device: The discovered device information.
            initially_enabled: Whether the monitor should start enabled.
        """
        try:
            monitor = InputMonitor(
                mqtt_client=self._mqtt_client,
                device_path=device.path,
                base_topic=self._config.topic,
                gateway_name=self._config.name,
                key_handler=self._key_handler,
                device_slug=device.slug,
                unique_id=device.unique_id,
                initially_enabled=initially_enabled,
                on_enabled_change=self._on_device_enabled_change,
            )
            monitor.setup_autodiscovery()
            self._monitors.append(monitor)
            self._monitors_by_path[device.path] = monitor
        except FileNotFoundError:
            logger.error("Device not found: %s", device.path)
        except PermissionError:
            logger.error("Permission denied for device: %s", device.path)
        except OSError as e:
            logger.error("Error opening device %s: %s", device.path, e)

    def _setup_switch_subscriptions(self) -> None:
        """Subscribe to switch command topics for all monitors."""
        for monitor in self._monitors:
            # Subscribe to this monitor's switch command topic
            self._mqtt_client.subscribe(
                monitor.switch_command_topic,
                lambda topic, payload, m=monitor: m.handle_switch_command(payload),
            )
            logger.debug(
                "Subscribed to switch commands for '%s' on '%s'",
                monitor.device.name,
                monitor.switch_command_topic,
            )

    def _on_device_enabled_change(self, device_path: str, enabled: bool) -> None:
        """Callback when a device's enabled state changes.

        This can be used to persist the enabled state if needed.

        Args:
            device_path: Path of the device that changed.
            enabled: New enabled state.
        """
        logger.info(
            "Device '%s' enabled state changed to: %s",
            device_path,
            enabled,
        )
        # Future: Could persist this to a file or MQTT retained message

    def wait(self) -> None:
        """Wait for all monitors to complete."""
        for monitor in self._monitors:
            monitor.join()

    def stop(self) -> None:
        """Stop the application gracefully."""
        if self._shutdown_requested:
            return
        self._shutdown_requested = True

        logger.info("Shutting down...")

        # Stop all monitors
        for monitor in self._monitors:
            monitor.stop()

        # Disconnect MQTT
        if self._mqtt_client:
            self._mqtt_client.disconnect()

        logger.info("Shutdown complete")

    def _handle_signal(self, signum: int, frame: object) -> None:
        """Handle termination signals.

        Args:
            signum: Signal number.
            frame: Current stack frame.
        """
        sig_name = signal.Signals(signum).name
        logger.info("Received %s, initiating shutdown", sig_name)
        self.stop()


def main() -> int:
    """Main entry point.

    Returns:
        Exit code (0 for success, non-zero for errors).
    """
    args = parse_args()
    setup_logging(verbose=args.verbose, debug=args.debug)

    if args.list_devices:
        list_devices_and_exit()

    try:
        config = Config.load(args.config)

        # Override auto_discover from command line if specified
        if args.auto_discover:
            # Create a new config with auto_discover enabled
            config = Config(
                serverip=config.serverip,
                port=config.port,
                username=config.username,
                password=config.password,
                name=config.name,
                topic=config.topic,
                devices=config.devices,
                auto_discover=True,
                enabled_devices=config.enabled_devices,
                filter_keys_only=config.filter_keys_only,
            )

    except FileNotFoundError as e:
        logger.error("Configuration error: %s", e)
        return 1
    except (KeyError, ValueError) as e:
        logger.error("Invalid configuration: %s", e)
        return 1

    app = Application(config)

    try:
        app.start()
        app.wait()
    except ConnectionError as e:
        logger.error("Connection error: %s", e)
        return 1
    except RuntimeError as e:
        logger.error("Runtime error: %s", e)
        return 1
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    finally:
        app.stop()

    return 0


if __name__ == "__main__":
    sys.exit(main())
