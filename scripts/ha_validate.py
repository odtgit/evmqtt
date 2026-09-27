"""Validate evmqtt's discovery against a real Home Assistant.

Starts mosquitto and ghcr.io/home-assistant/home-assistant in docker (host
network, free ports), onboards HA, adds the MQTT integration, then runs the
evmqtt Gateway in-process on fake input devices (tests/fakes.py, nothing is
grabbed on this machine) and checks entities, device grouping,
availability, events, the switch and the 1.x cleanup through HA's REST API.

    .venv/bin/python scripts/ha_validate.py [--image IMAGE] [--keep]

Needs docker and the dev install (pip install -e ".[mqtt,dev]"). Not run in CI.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

import evdev  # noqa: E402

import evmqtt.sysinfo as sysinfo  # noqa: E402
from evmqtt.config import Config  # noqa: E402
from evmqtt.gateway import Gateway  # noqa: E402
from tests.fakes import FakeEvdevRegistry, keyboard_capabilities, press  # noqa: E402

HA_IMAGE = "ghcr.io/home-assistant/home-assistant:stable"
MQTT_IMAGE = "eclipse-mosquitto:2"
CLIENT_ID = "http://127.0.0.1/"
RESULTS: list[tuple[bool, str]] = []


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def run(*args: str) -> str:
    return subprocess.run(args, check=True, capture_output=True, text=True).stdout


def check(ok: bool, what: str) -> None:
    RESULTS.append((ok, what))
    print(f"{'PASS' if ok else 'FAIL'} {what}", flush=True)


class HA:
    def __init__(self, port: int) -> None:
        self.url = f"http://127.0.0.1:{port}"
        self.token = ""

    def request(
        self, method: str, path: str, body: Any = None, form: bool = False
    ) -> Any:
        headers = {}
        data = None
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        if body is not None:
            if form:
                data = urllib.parse.urlencode(body).encode()
                headers["Content-Type"] = "application/x-www-form-urlencoded"
            else:
                data = json.dumps(body).encode()
                headers["Content-Type"] = "application/json"
        req = urllib.request.Request(
            self.url + path, data=data, headers=headers, method=method
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode()
        try:
            return json.loads(raw)
        except ValueError:
            return raw

    def wait_up(self, timeout: float = 240) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                self.request("GET", "/api/onboarding")
                return
            except (urllib.error.URLError, OSError):
                time.sleep(1)
        raise RuntimeError("Home Assistant did not come up")

    def onboard(self) -> None:
        user = self.request(
            "POST",
            "/api/onboarding/users",
            {
                "client_id": CLIENT_ID,
                "name": "validate",
                "username": "validate",
                "password": "validate-evmqtt",
                "language": "en",
            },
        )
        token = self.request(
            "POST",
            "/auth/token",
            {
                "grant_type": "authorization_code",
                "code": user["auth_code"],
                "client_id": CLIENT_ID,
            },
            form=True,
        )
        self.token = token["access_token"]

    def add_mqtt(self, port: int) -> None:
        step = self.request(
            "POST", "/api/config/config_entries/flow", {"handler": "mqtt"}
        )
        for _ in range(5):
            if step.get("type") == "create_entry":
                return
            if step.get("type") == "menu":
                body: dict[str, Any] = {"next_step_id": "broker"}
            elif step.get("type") == "form":
                body = {
                    "broker": "127.0.0.1",
                    "port": port,
                    "protocol": "5",
                    "other_settings": {
                        "set_client_cert": False,
                        "set_ca_cert": "off",
                        "transport": "tcp",
                    },
                }
            else:
                break
            try:
                step = self.request(
                    "POST", f"/api/config/config_entries/flow/{step['flow_id']}", body
                )
            except urllib.error.HTTPError as err:
                raise RuntimeError(f"MQTT flow {step}: {err.read().decode()}") from err
        raise RuntimeError(f"MQTT config flow: {step}")

    def states(self) -> dict[str, dict[str, Any]]:
        return {s["entity_id"]: s for s in self.request("GET", "/api/states")}

    def template(self, text: str) -> str:
        return str(self.request("POST", "/api/template", {"template": text})).strip()

    def call(self, domain: str, service: str, entity_id: str) -> None:
        self.request(
            "POST", f"/api/services/{domain}/{service}", {"entity_id": entity_id}
        )


def wait_until(predicate, timeout: float = 30, interval: float = 0.5) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if predicate():
                return True
        except (urllib.error.URLError, KeyError, OSError):
            pass
        time.sleep(interval)
    return bool(predicate())


async def await_until(predicate, timeout: float = 30) -> bool:
    return await asyncio.to_thread(wait_until, predicate, timeout)


def find(states: dict[str, dict[str, Any]], domain: str, suffix: str) -> str | None:
    for entity_id in states:
        if entity_id.startswith(f"{domain}.") and entity_id.endswith(suffix):
            return entity_id
    return None


def seed_legacy(mqtt_port: int) -> None:
    import paho.mqtt.client as mqtt

    client = mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION2)
    client.connect("127.0.0.1", mqtt_port)
    client.loop_start()
    legacy = "homeassistant/sensor/evmqtt"
    device = {
        "identifiers": ["evmqtt_legacy-kbd_event9"],
        "name": "Legacy Kbd",
        "manufacturer": "evmqtt",
        "model": "Input Device",
    }
    sensor = {
        "name": "Input Events - Legacy Kbd",
        "state_topic": f"{legacy}/legacy-kbd/state",
        "unique_id": "evmqtt_legacy-kbd_event9_sensor",
        "value_template": "{{ value_json.key }}",
        "device": device,
    }
    switch = {
        "name": "Legacy Kbd Enable",
        "state_topic": f"{legacy}/legacy-kbd/switch/state",
        "command_topic": f"{legacy}/legacy-kbd/switch/set",
        "unique_id": "evmqtt_legacy-kbd_event9_switch",
        "device": device,
    }
    for topic, payload in (
        (f"{legacy}/legacy-kbd/config", json.dumps(sensor)),
        ("homeassistant/switch/evmqtt_legacy-kbd_event9/config", json.dumps(switch)),
        (f"{legacy}/legacy-kbd/switch/state", "ON"),
    ):
        client.publish(topic, payload, qos=1, retain=True).wait_for_publish(5)
    client.disconnect()
    client.loop_stop()


async def validate(ha: HA, mqtt_port: int, workdir: Path) -> None:
    registry = FakeEvdevRegistry()
    evdev.InputDevice = registry.open  # type: ignore[misc]
    evdev.list_devices = registry.list_devices  # type: ignore[assignment]
    sysinfo.SYSFS_ROOT = workdir / "sys"
    kbd = registry.add(
        "/dev/input/event0",
        name="Validate Remote",
        capabilities=keyboard_capabilities(),
        phys="usb-0000:00:14.0-1/input0",
        vendor=0x046D,
        product=0xC52B,
    )

    seed_legacy(mqtt_port)
    legacy_ok = await await_until(
        lambda: find(ha.states(), "sensor", "legacy_kbd") is not None, 60
    )
    check(legacy_ok, "1.x sensor entity exists before the upgrade")

    config = Config.from_dict(
        {
            "mqtt_host": "127.0.0.1",
            "mqtt_port": mqtt_port,
            "name": "Validate Gateway",
            "base_topic": "evmqtt/validate",
            "keystates": ["PRESS", "RELEASE"],
            "state_file": str(workdir / "state.json"),
            "rescan_interval": 0,
        }
    )
    gateway = Gateway(config)
    await gateway.start()
    try:
        ok = await await_until(
            lambda: (
                find(ha.states(), "event", "_key")
                and find(ha.states(), "switch", "_enabled")
                and find(ha.states(), "binary_sensor", "_status")
            ),
            60,
        )
        check(bool(ok), "event, switch and gateway status entities created")
        states = ha.states()
        event_id = find(states, "event", "_key") or ""
        switch_id = find(states, "switch", "_enabled") or ""
        status_id = find(states, "binary_sensor", "_status") or ""
        print(f"     entities: {event_id}, {switch_id}, {status_id}")

        attrs = states.get(event_id, {}).get("attributes", {})
        check(
            attrs.get("event_types") == ["press", "release"],
            f"event_types {attrs.get('event_types')}",
        )
        check(attrs.get("device_class") == "button", "event device_class button")
        ok = await await_until(lambda: ha.states()[switch_id]["state"] == "on")
        check(ok, "switch is on")
        ok = await await_until(lambda: ha.states()[status_id]["state"] == "on")
        check(ok, "gateway status on")

        dev_name = ha.template(
            f"{{{{ device_attr(device_id('{event_id}'), 'name') }}}}"
        )
        check(dev_name == "Validate Remote", f"event device name {dev_name!r}")
        same = ha.template(
            f"{{{{ device_id('{event_id}') == device_id('{switch_id}') }}}}"
        )
        check(same == "True", "event and switch share one device")
        via = ha.template(
            f"{{{{ device_attr(device_id('{event_id}'), 'via_device_id') "
            f"== device_id('{status_id}') }}}}"
        )
        check(via == "True", "input device is via the gateway device")
        for attr, want in (
            ("manufacturer", "None"),
            ("model_id", "046d:c52b"),
        ):
            got = ha.template(
                f"{{{{ device_attr(device_id('{event_id}'), '{attr}') }}}}"
            )
            check(got == want, f"device {attr} {got!r}")
        gw_name = ha.template(
            f"{{{{ device_attr(device_id('{status_id}'), 'name') }}}}"
        )
        check(gw_name == "Validate Gateway", f"gateway device name {gw_name!r}")

        kbd.push(press("KEY_A"))
        ok = await await_until(
            lambda: ha.states()[event_id]["attributes"].get("key") == "KEY_A"
        )
        attrs = ha.states()[event_id]["attributes"]
        check(
            ok and attrs.get("event_type") == "press",
            f"key event reached HA: {attrs.get('event_type')} {attrs.get('key')}",
        )

        await asyncio.to_thread(ha.call, "switch", "turn_off", switch_id)
        ok = await await_until(lambda: not kbd.grabbed)
        check(ok, "switch.turn_off ungrabs the device")
        ok = await await_until(lambda: ha.states()[switch_id]["state"] == "off")
        check(ok, "switch state off in HA")
        await asyncio.to_thread(ha.call, "switch", "turn_on", switch_id)
        ok = await await_until(lambda: kbd.grabbed)
        check(ok, "switch.turn_on grabs the device again")

        ok = await await_until(
            lambda: (
                find(ha.states(), "sensor", "legacy_kbd") is None
                and find(ha.states(), "switch", "legacy_kbd_enable") is None
            )
        )
        check(ok, "1.x entities removed by the cleanup")

        kbd.unplug()
        ok = await await_until(lambda: ha.states()[event_id]["state"] == "unavailable")
        check(ok, "unplugged device goes unavailable")
        check(ha.states()[status_id]["state"] == "on", "gateway stays online on unplug")
    finally:
        await gateway.stop()
    ok = await await_until(lambda: ha.states()[status_id]["state"] == "off")
    check(ok, "gateway status off after clean stop")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", default=HA_IMAGE)
    parser.add_argument("--keep", action="store_true", help="leave containers running")
    args = parser.parse_args()

    mqtt_port = free_port()
    ha_port = free_port()
    names = [f"evmqtt-validate-mqtt-{mqtt_port}", f"evmqtt-validate-ha-{ha_port}"]
    with tempfile.TemporaryDirectory() as tmp:
        workdir = Path(tmp)
        (workdir / "sys").mkdir()
        (workdir / "mosquitto.conf").write_text(
            f"listener {mqtt_port} 127.0.0.1\nallow_anonymous true\n"
        )
        (workdir / "configuration.yaml").write_text(
            "homeassistant:\n  name: evmqtt-validate\n"
            f"http:\n  server_host: 127.0.0.1\n  server_port: {ha_port}\n"
            "api:\nonboarding:\nconfig:\nauth:\n"
        )
        try:
            run(
                "docker", "run", "-d", "--rm", "--name", names[0], "--network", "host",
                "--user", f"{run('id', '-u').strip()}",
                "-v", f"{workdir}:{workdir}:ro",
                MQTT_IMAGE, "mosquitto", "-c", str(workdir / "mosquitto.conf"),
            )  # fmt: skip
            run(
                "docker", "create", "--name", names[1], "--network", "host",
                args.image,
            )  # fmt: skip
            run(
                "docker", "cp", str(workdir / "configuration.yaml"),
                f"{names[1]}:/config/configuration.yaml",
            )  # fmt: skip
            run("docker", "start", names[1])
            ha = HA(ha_port)
            print(f"Waiting for {args.image} on :{ha_port}", flush=True)
            ha.wait_up()
            version = run(
                "docker", "image", "inspect", args.image, "--format",
                '{{index .Config.Labels "org.opencontainers.image.version"}}',
            ).strip()  # fmt: skip
            print(f"Home Assistant {version}", flush=True)
            ha.onboard()
            ha.add_mqtt(mqtt_port)
            asyncio.run(validate(ha, mqtt_port, workdir))
            logs = subprocess.run(
                ["docker", "logs", names[1]], capture_output=True, text=True
            )
            problems = [
                line
                for line in (logs.stdout + logs.stderr).splitlines()
                if ("WARNING" in line or "ERROR" in line)
                and ("mqtt" in line.lower() or "evmqtt" in line.lower())
            ]
            for line in problems:
                print(f"     HA log: {line}")
            check(not problems, "no MQTT warnings or errors in the HA log")
        finally:
            if not args.keep:
                for name in names:
                    subprocess.run(
                        ["docker", "rm", "-f", name], capture_output=True, check=False
                    )
    failed = [what for ok, what in RESULTS if not ok]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed")
    return 1 if failed or not RESULTS else 0


if __name__ == "__main__":
    sys.exit(main())
