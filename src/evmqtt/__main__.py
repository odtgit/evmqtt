"""Main entry point for evmqtt.

This module provides the command-line interface for the evmqtt gateway.
It can be run with:
    python -m evmqtt
    evmqtt (if installed as a package)
"""

from __future__ import annotations

import argparse
import asyncio
import importlib
import logging
import signal
import sys
from dataclasses import replace
from platform import node as hostname
from time import time
from typing import TYPE_CHECKING, NoReturn

from evmqtt.config import Config
from evmqtt.device_discovery import DiscoveredDevice, discover_devices
from evmqtt.input_monitor import InputMonitor, list_available_devices
from evmqtt.key_handler import KeyHandler

if TYPE_CHECKING:
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
        force=True,
    )


def generate_client_id() -> str:
    """Generate a unique MQTT client ID.

    Returns:
        Client ID string based on hostname and current time.
    """
    return f"evmqtt_{hostname()}_{int(time())}"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command line arguments.

    Args:
        argv: Argument list to parse (default: sys.argv[1:]).

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
    return parser.parse_args(argv)


def mqtt_import_error() -> ImportError | None:
    """The daemon needs paho-mqtt, which lives in the [mqtt] extra."""
    try:
        importlib.import_module("paho.mqtt.client")
    except ImportError as e:
        return e
    return None


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

    Runs on an asyncio loop: one core reader per device, paho's network
    thread for MQTT. Inbound switch commands hop from paho's thread into
    the loop with call_soon_threadsafe.
    """

    def __init__(self, config: Config, connect_timeout: float = 30.0) -> None:
        """Initialize the application.

        Args:
            config: Application configuration.
            connect_timeout: Seconds to wait for the MQTT broker to connect.
        """
        self._config = config
        self._connect_timeout = connect_timeout
        self._mqtt_client: MQTTClientWrapper | None = None
        self._monitors: list[InputMonitor] = []
        self._monitors_by_path: dict[str, InputMonitor] = {}
        self._tasks: list[asyncio.Task[object]] = []
        self._wake = asyncio.Event()
        self._shutdown_requested = False
        self._stopped = False

    @property
    def shutdown_requested(self) -> bool:
        """Whether a stop was requested (vs. monitors ending on their own)."""
        return self._shutdown_requested

    async def start(self) -> None:
        """Connect to MQTT, open devices, publish discovery, start readers."""
        from evmqtt.mqtt_client import MQTTClientWrapper

        loop = asyncio.get_running_loop()
        self._mqtt_client = MQTTClientWrapper(generate_client_id(), self._config)
        await asyncio.to_thread(self._mqtt_client.connect)

        if not await self._mqtt_client.async_wait_for_connection(
            timeout=self._connect_timeout
        ):
            logger.error("Failed to connect to MQTT broker within timeout")
            raise ConnectionError("MQTT connection timeout")

        if self._config.auto_discover:
            self._setup_auto_discovery()
        else:
            self._setup_manual_devices()

        if not self._monitors:
            raise RuntimeError("No input devices could be opened")

        self._setup_switch_subscriptions(loop)

        for monitor in self._monitors:
            task: asyncio.Task[object] = asyncio.create_task(monitor.run())
            task.add_done_callback(self._on_monitor_done)
            self._tasks.append(task)

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
                "  %s (%s) -> %s [id %s]",
                device.name,
                device.path,
                device.slug,
                device.device_id,
            )

        # Empty enabled_devices means all devices start enabled.
        enabled_paths = set(self._config.enabled_devices)
        all_enabled = not enabled_paths

        for device in discovered:
            initially_enabled = all_enabled or device.path in enabled_paths
            self._create_monitor_for_device(device, initially_enabled)

    def _setup_manual_devices(self) -> None:
        """Set up monitors for manually specified devices."""
        available_devices = list_available_devices()
        logger.info("Found %d available input device(s):", len(available_devices))
        for device in available_devices:
            logger.info("  Path: %s, Name: %s", device["path"], device["name"])

        for device_path in self._config.devices:
            # Manual setup keeps the path-based topics, no slug.
            self._add_monitor(device_path)

    def _create_monitor_for_device(
        self, device: DiscoveredDevice, initially_enabled: bool
    ) -> None:
        """Create and configure an InputMonitor for a discovered device."""
        self._add_monitor(
            device.path,
            device_slug=device.slug,
            unique_id=device.unique_id,
            initially_enabled=initially_enabled,
        )

    def _add_monitor(
        self,
        device_path: str,
        device_slug: str | None = None,
        unique_id: str | None = None,
        initially_enabled: bool = True,
    ) -> None:
        try:
            monitor = InputMonitor(
                mqtt_client=self._mqtt_client,
                device_path=device_path,
                base_topic=self._config.topic,
                gateway_name=self._config.name,
                key_handler=KeyHandler(publish_states=self._config.keystates),
                device_slug=device_slug,
                unique_id=unique_id,
                initially_enabled=initially_enabled,
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

    def _setup_switch_subscriptions(self, loop: asyncio.AbstractEventLoop) -> None:
        """Subscribe to switch command topics for all monitors."""
        for monitor in self._monitors:
            self._mqtt_client.subscribe(
                monitor.switch_command_topic,
                lambda topic, payload, m=monitor: loop.call_soon_threadsafe(
                    m.handle_switch_command, payload
                ),
            )
            logger.debug(
                "Subscribed to switch commands for '%s' on '%s'",
                monitor.device.name,
                monitor.switch_command_topic,
            )

    def _on_device_enabled_change(self, device_path: str, enabled: bool) -> None:
        logger.info(
            "Device '%s' enabled state changed to: %s",
            device_path,
            enabled,
        )

    def _on_monitor_done(self, task: asyncio.Task[object]) -> None:
        if all(t.done() for t in self._tasks):
            self._wake.set()

    async def wait(self) -> None:
        """Return when stop is requested or every monitor has ended."""
        await self._wake.wait()

    def request_stop(self) -> None:
        """Make wait() return. Safe from a loop signal handler."""
        self._shutdown_requested = True
        self._wake.set()

    async def stop(self) -> None:
        """Stop readers, then disconnect MQTT. Idempotent."""
        if self._stopped:
            return
        self._stopped = True
        self._shutdown_requested = True

        logger.info("Shutting down...")

        for monitor in self._monitors:
            monitor.stop()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)

        if self._mqtt_client:
            await asyncio.to_thread(self._mqtt_client.disconnect)

        logger.info("Shutdown complete")

    def _handle_signal(self, signum: int) -> None:
        logger.info("Received %s, initiating shutdown", signal.Signals(signum).name)
        self.request_stop()


async def run_application(app: Application) -> int:
    """Run app until a signal or until every monitor has ended."""
    loop = asyncio.get_running_loop()
    signals = (signal.SIGINT, signal.SIGTERM)
    for sig in signals:
        loop.add_signal_handler(sig, app._handle_signal, sig)
    try:
        await app.start()
        await app.wait()
        if not app.shutdown_requested:
            logger.error("All input monitors have stopped unexpectedly")
            return 1
    except ConnectionError as e:
        logger.error("Connection error: %s", e)
        return 1
    except RuntimeError as e:
        logger.error("Runtime error: %s", e)
        return 1
    except (TypeError, ValueError) as e:
        logger.error("Invalid configuration: %s", e)
        return 1
    except OSError as e:
        logger.error("Startup error: %s", e)
        return 1
    finally:
        await app.stop()
        for sig in signals:
            loop.remove_signal_handler(sig)
    return 0


def main(argv: list[str] | None = None) -> int:
    """Main entry point.

    Args:
        argv: Argument list to parse (default: sys.argv[1:]).

    Returns:
        Exit code (0 for success, non-zero for errors).
    """
    args = parse_args(argv)
    setup_logging(verbose=args.verbose, debug=args.debug)

    if args.list_devices:
        list_devices_and_exit()

    missing = mqtt_import_error()
    if missing is not None:
        logger.error(
            "The evmqtt daemon needs paho-mqtt: pip install 'evmqtt[mqtt]' (%s)",
            missing,
        )
        return 1

    try:
        config = Config.load(args.config)

        # Override auto_discover from command line if specified
        if args.auto_discover:
            config = replace(config, auto_discover=True)

    except FileNotFoundError as e:
        logger.error("Configuration error: %s", e)
        return 1
    except (KeyError, ValueError) as e:
        logger.error("Invalid configuration: %s", e)
        return 1

    return asyncio.run(run_application(Application(config)))


if __name__ == "__main__":
    sys.exit(main())
