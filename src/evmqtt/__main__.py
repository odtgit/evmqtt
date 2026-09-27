"""evmqtt command line: python -m evmqtt, or the evmqtt script."""

from __future__ import annotations

import argparse
import asyncio
import importlib
import json
import logging
import signal
import sys
from dataclasses import replace
from typing import TYPE_CHECKING

from evmqtt.config import LOG_LEVELS, Config, ConfigError
from evmqtt.core import list_devices

if TYPE_CHECKING:
    from evmqtt.gateway import Gateway

logger = logging.getLogger("evmqtt")


def setup_logging(level: str = "info") -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper()),
        format="[%(asctime)s] %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stderr,
        force=True,
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="evmqtt", description="Linux input event to MQTT gateway"
    )
    parser.add_argument("-c", "--config", help="configuration file", default=None)
    parser.add_argument(
        "--log-level", choices=LOG_LEVELS, help="override log_level from the config"
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="same as --log-level info"
    )
    parser.add_argument(
        "-d", "--debug", action="store_true", help="same as --log-level debug"
    )
    parser.add_argument(
        "--list-devices",
        action="store_true",
        help="list input devices with their stable ids and exit",
    )
    parser.add_argument(
        "--auto-discover", action="store_true", help="force auto_discover on"
    )
    return parser.parse_args(argv)


def cli_log_level(args: argparse.Namespace) -> str | None:
    if args.debug:
        return "debug"
    if args.verbose:
        return "info"
    return str(args.log_level) if args.log_level else None


def mqtt_import_error() -> ImportError | None:
    try:
        importlib.import_module("paho.mqtt.client")
    except ImportError as e:
        return e
    return None


def list_devices_cmd() -> int:
    from evmqtt.sysinfo import is_virtual

    devices = list_devices()
    if not devices:
        print("No input devices found.")
        return 1
    print(f"Found {len(devices)} input device(s):")
    for info in devices:
        flags = []
        if info.is_keyboard_like:
            flags.append("keyboard")
        if is_virtual(info):
            flags.append("virtual")
        default = info.is_keyboard_like and "virtual" not in flags
        print(
            f"  {info.path:<20} {info.id:<44} {json.dumps(info.name)}"
            f"  [{', '.join(flags) or '-'}]{' (default)' if default else ''}"
        )
    return 0


async def run_gateway(gateway: Gateway) -> int:
    loop = asyncio.get_running_loop()
    signals = (signal.SIGINT, signal.SIGTERM)

    def on_signal(sig: signal.Signals) -> None:
        logger.info("Received %s, shutting down", sig.name)
        gateway.request_stop()

    for sig in signals:
        loop.add_signal_handler(sig, on_signal, sig)
    try:
        await gateway.start()
        await gateway.wait()
    finally:
        await gateway.stop()
        for sig in signals:
            loop.remove_signal_handler(sig)
    err = gateway.fatal_error
    if err is None:
        return 0
    if isinstance(err, (ConfigError, FileNotFoundError, PermissionError)):
        logger.error("Configuration error: %s", err)
    else:
        logger.error("Fatal error: %r", err)
    return 1


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    override = cli_log_level(args)
    setup_logging(override or "info")

    if args.list_devices:
        return list_devices_cmd()

    missing = mqtt_import_error()
    if missing is not None:
        logger.error(
            "The evmqtt daemon needs paho-mqtt: pip install 'evmqtt[mqtt]' (%s)",
            missing,
        )
        return 1

    try:
        config = Config.load(args.config)
    except (OSError, ValueError) as e:
        logger.error("Configuration error: %s", e)
        return 1
    if args.auto_discover:
        config = replace(config, auto_discover=True)
    setup_logging(override or config.log_level)
    for warning in config.warnings:
        logger.warning("Config: %s", warning)
    logger.info(
        "Base topic '%s', discovery prefix '%s', state file '%s'",
        config.base_topic,
        config.discovery_prefix,
        config.state_path,
    )

    from evmqtt.gateway import Gateway

    return asyncio.run(run_gateway(Gateway(config)))


if __name__ == "__main__":
    sys.exit(main())
