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
    """

    def __init__(self, config: Config) -> None:
        """Initialize the application.

        Args:
            config: Application configuration.
        """
        self._config = config
        self._mqtt_client: MQTTClientWrapper | None = None
        self._monitors: list[InputMonitor] = []
        self._key_handler = KeyHandler()
        self._shutdown_requested = False

    def start(self) -> None:
        """Start the application."""
        # Set up signal handlers
        signal.signal(signal.SIGINT, self._handle_signal)
        signal.signal(signal.SIGTERM, self._handle_signal)

        # List available devices
        available_devices = list_available_devices()
        logger.info("Found %d available input device(s):", len(available_devices))
        for device in available_devices:
            logger.info("  Path: %s, Name: %s", device["path"], device["name"])

        # Create MQTT client
        client_id = generate_client_id()
        self._mqtt_client = MQTTClientWrapper(client_id, self._config)
        self._mqtt_client.connect()

        # Wait for connection
        if not self._mqtt_client.wait_for_connection(timeout=30.0):
            logger.error("Failed to connect to MQTT broker within timeout")
            raise ConnectionError("MQTT connection timeout")

        # Create input monitors
        for device_path in self._config.devices:
            try:
                monitor = InputMonitor(
                    mqtt_client=self._mqtt_client,
                    device_path=device_path,
                    base_topic=self._config.topic,
                    gateway_name=self._config.name,
                    key_handler=self._key_handler,
                )
                self._monitors.append(monitor)
            except FileNotFoundError:
                logger.error("Device not found: %s", device_path)
            except PermissionError:
                logger.error("Permission denied for device: %s", device_path)
            except OSError as e:
                logger.error("Error opening device %s: %s", device_path, e)

        if not self._monitors:
            raise RuntimeError("No input devices could be opened")

        # Start all monitors
        for monitor in self._monitors:
            monitor.start()

        logger.info("Application started with %d monitor(s)", len(self._monitors))

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
