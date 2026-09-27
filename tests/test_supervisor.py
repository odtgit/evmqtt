"""Supervisor services API lookup against a local HTTP server."""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

import evmqtt.supervisor as supervisor
from evmqtt.config import Config, ConfigError
from evmqtt.gateway import Gateway
from evmqtt.mqtt_client import BrokerSettings
from tests.fakes import until

SERVICE = {
    "host": "core-mosquitto",
    "port": 1883,
    "ssl": False,
    "protocol": "3.1.1",
    "username": "addons",
    "password": "secret",
    "addon": "core_mosquitto",
}


class FakeSupervisor:
    def __init__(self) -> None:
        self.responses: list[tuple[int, object]] = []
        self.requests: list[tuple[str, str | None]] = []


@pytest.fixture
def fake_supervisor(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeSupervisor]:
    state = FakeSupervisor()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            state.requests.append((self.path, self.headers.get("Authorization")))
            status, body = (
                state.responses.pop(0)
                if state.responses
                else (200, {"result": "ok", "data": SERVICE})
            )
            raw = body if isinstance(body, bytes) else json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
    )
    thread.start()
    monkeypatch.setattr(
        supervisor, "SUPERVISOR_URL", f"http://127.0.0.1:{server.server_port}"
    )
    monkeypatch.setenv("SUPERVISOR_TOKEN", "tok")
    yield state
    server.shutdown()
    server.server_close()


async def resolve(config: Config) -> BrokerSettings:
    return await supervisor.resolve_broker(config, min_delay=0.001, max_delay=0.002)


async def test_service_values_used_when_mqtt_host_empty(fake_supervisor) -> None:
    settings = await resolve(Config.from_dict({"mqtt_host": ""}, addon=True))
    assert settings == BrokerSettings(
        "core-mosquitto", 1883, "addons", "secret", False, None
    )
    assert fake_supervisor.requests == [("/services/mqtt", "Bearer tok")]


async def test_explicit_options_override_service(fake_supervisor) -> None:
    config = Config.from_dict(
        {
            "mqtt_port": 8884,
            "mqtt_username": "me",
            "mqtt_password": "pw",
            "mqtt_tls": True,
        }
    )
    settings = await resolve(config)
    assert settings == BrokerSettings("core-mosquitto", 8884, "me", "pw", True, None)


async def test_ssl_service_defaults_tls_and_port(fake_supervisor) -> None:
    fake_supervisor.responses.append(
        (200, {"result": "ok", "data": {"host": "h", "ssl": True}})
    )
    settings = await resolve(Config.from_dict({}))
    assert settings == BrokerSettings("h", 8883, None, None, True, None)


async def test_mqtt_host_set_skips_supervisor(fake_supervisor) -> None:
    settings = await resolve(Config.from_dict({"mqtt_host": "b", "mqtt_tls_ca": "/ca"}))
    assert settings == BrokerSettings("b", 8883, None, None, True, "/ca")
    assert fake_supervisor.requests == []


async def test_retries_until_service_is_provided(fake_supervisor, caplog) -> None:
    fake_supervisor.responses += [
        (400, {"result": "error", "message": "Service not enabled"}),
        (503, b"not json"),
        (200, {"result": "ok", "data": {}}),
    ]
    settings = await resolve(Config.from_dict({}))
    assert settings.host == "core-mosquitto"
    assert len(fake_supervisor.requests) == 4
    assert "Service not enabled" in caplog.text


@pytest.mark.parametrize("status", [401, 403, 404])
async def test_access_errors_are_fatal(fake_supervisor, status) -> None:
    fake_supervisor.responses.append(
        (status, {"result": "error", "message": "No access to mqtt service!"})
    )
    with pytest.raises(ConfigError, match=str(status)):
        await resolve(Config.from_dict({}))


async def test_no_token_and_no_host_is_config_error(monkeypatch) -> None:
    monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
    with pytest.raises(ConfigError, match="SUPERVISOR_TOKEN"):
        await resolve(Config.from_dict({}))


async def test_unreachable_supervisor_retries(monkeypatch) -> None:
    monkeypatch.setenv("SUPERVISOR_TOKEN", "tok")
    monkeypatch.setattr(supervisor, "SUPERVISOR_URL", "http://127.0.0.1:9")
    calls: list[int] = []
    real = supervisor.fetch_mqtt_service

    def flaky(token: str, timeout: float = 10.0):
        calls.append(1)
        if len(calls) < 3:
            return real(token, timeout=1.0)
        return {"host": "h", "port": 1883}

    monkeypatch.setattr(supervisor, "fetch_mqtt_service", flaky)
    settings = await resolve(Config.from_dict({}))
    assert settings.host == "h"
    assert len(calls) == 3


async def test_gateway_connects_to_supervisor_broker(
    fake_supervisor, fake_evdev, fake_mqtt, tmp_path
) -> None:
    config = Config.from_dict(
        {"state_file": str(tmp_path / "s.json"), "rescan_interval": 0}, addon=True
    )
    gateway = Gateway(config)
    gateway._resolver = resolve
    await gateway.start()
    try:
        assert await until(lambda: fake_mqtt.created and fake_mqtt.last().connected)
        client = fake_mqtt.last()
        assert (client.host, client.port) == ("core-mosquitto", 1883)
        assert (client.username, client.password) == ("addons", "secret")
    finally:
        await gateway.stop()


async def test_gateway_fatal_on_supervisor_access_error(
    fake_supervisor, fake_evdev, fake_mqtt, tmp_path
) -> None:
    fake_supervisor.responses.append((403, {"result": "error", "message": "no"}))
    config = Config.from_dict({"state_file": str(tmp_path / "s.json")}, addon=True)
    gateway = Gateway(config)
    await gateway.start()
    try:
        await gateway.wait()
        assert isinstance(gateway.fatal_error, ConfigError)
    finally:
        await gateway.stop()
