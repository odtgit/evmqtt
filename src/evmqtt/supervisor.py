"""Home Assistant Supervisor services API: MQTT broker lookup for the add-on."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import urllib.error
import urllib.request
from typing import Any

from evmqtt.config import Config, ConfigError
from evmqtt.mqtt_client import BrokerSettings

logger = logging.getLogger(__name__)

SUPERVISOR_URL = "http://supervisor"
_RETRY_STATUS = {400, 429, 500, 502, 503, 504}


class SupervisorError(Exception):
    def __init__(self, message: str, retry: bool) -> None:
        super().__init__(message)
        self.retry = retry


def fetch_mqtt_service(token: str, timeout: float = 10.0) -> dict[str, Any]:
    """GET /services/mqtt. Blocking. Returns the data object."""
    request = urllib.request.Request(
        f"{SUPERVISOR_URL}/services/mqtt",
        headers={"Authorization": f"Bearer {token}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as err:
        try:
            message = json.loads(err.read().decode("utf-8")).get("message", "")
        except (ValueError, AttributeError, OSError):
            message = ""
        raise SupervisorError(
            f"HTTP {err.code} {message}".strip(), err.code in _RETRY_STATUS
        ) from err
    except (urllib.error.URLError, OSError, ValueError) as err:
        raise SupervisorError(str(err), True) from err
    data = body.get("data") if isinstance(body, dict) else None
    if not isinstance(data, dict) or not data.get("host"):
        raise SupervisorError(f"unexpected response: {body!r}", True)
    return data


def merge(config: Config, service: dict[str, Any] | None) -> BrokerSettings:
    """Explicit mqtt_* options override the service values."""
    service = service or {}
    tls = config.mqtt_tls
    if tls is None:
        tls = bool(service.get("ssl", False)) or bool(config.mqtt_tls_ca)
    port = config.mqtt_port
    if port is None:
        port = int(service["port"]) if service.get("port") else (8883 if tls else 1883)
    host = config.mqtt_host or service.get("host")
    if not host:
        raise ConfigError("mqtt_host is not set")
    return BrokerSettings(
        host=str(host),
        port=port,
        username=config.mqtt_username
        if config.mqtt_username is not None
        else service.get("username") or None,
        password=config.mqtt_password
        if config.mqtt_password is not None
        else service.get("password") or None,
        tls=tls,
        tls_ca=config.mqtt_tls_ca,
    )


async def resolve_broker(
    config: Config, *, min_delay: float = 1.0, max_delay: float = 60.0
) -> BrokerSettings:
    """mqtt_host set: use it. Else ask the Supervisor, retrying with backoff."""
    if config.mqtt_host:
        return merge(config, None)
    token = os.environ.get("SUPERVISOR_TOKEN")
    if not token:
        raise ConfigError(
            "mqtt_host is not set and SUPERVISOR_TOKEN is missing; "
            "set mqtt_host or run as a Home Assistant add-on"
        )
    delay = min_delay
    attempt = 0
    loop = asyncio.get_running_loop()
    while True:
        attempt += 1
        try:
            service = await loop.run_in_executor(None, fetch_mqtt_service, token)
        except SupervisorError as err:
            if not err.retry:
                raise ConfigError(
                    f"Supervisor MQTT service lookup failed: {err}"
                ) from err
            log = logger.warning if attempt == 1 else logger.debug
            log(
                "MQTT service not available from Supervisor (%s), retry in %.0fs",
                err,
                delay,
            )
            await asyncio.sleep(delay)
            delay = min(delay * 2, max_delay)
            continue
        logger.info(
            "Using MQTT broker from Supervisor: %s:%s (%s)",
            service.get("host"),
            service.get("port"),
            service.get("addon") or service.get("app") or "unknown provider",
        )
        return merge(config, service)
