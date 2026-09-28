"""Validate the HACS integration (custom_components/evmqtt) in a real Home Assistant.

Starts ghcr.io/home-assistant/home-assistant with custom_components mounted,
onboards it and drives the config flow over the REST API. HA installs the
manifest requirement (evmqtt from PyPI) on first use.

    .venv/bin/python scripts/ha_integration_validate.py [--image IMAGE] [--keep]

Without --uinput HA gets no /dev/input and the flow must stop at no_input.
With --uinput (root, EVMQTT_UINPUT=1, CI only: keystrokes leak into a local
desktop) /dev/input is passed in the way the README documents for HA
Container (read-only bind mount, c 13:* rw) and uinput devices are created
after HA is up, so they reach the container by hotplug. Checks entities, a
key press reaching the event entity, grab and ungrab via the switch, EBUSY,
reload, a device hotplugged after setup starting disabled, and unplug.
"""

from __future__ import annotations

import argparse
import errno
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from ha_validate import HA, HA_IMAGE, RESULTS, check, find, free_port, run  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DEVICE_NAME = "evmqtt CI Remote"
HOTPLUG_NAME = "evmqtt CI Keypad"


def api(ha: HA, method: str, path: str, body: Any = None, timeout: float = 300) -> Any:
    req = urllib.request.Request(
        ha.url + path,
        data=None if body is None else json.dumps(body).encode(),
        headers={
            "Authorization": f"Bearer {ha.token}",
            "Content-Type": "application/json",
        },
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode()
    except urllib.error.HTTPError as err:
        raise RuntimeError(
            f"{method} {path}: {err.code} {err.read().decode()}"
        ) from err
    return json.loads(raw) if raw else None


def wait(predicate: Any, timeout: float = 30) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if predicate():
                return True
        except (KeyError, OSError, RuntimeError):
            pass
        time.sleep(0.5)
    try:
        return bool(predicate())
    except (KeyError, OSError, RuntimeError):
        return False


def options(step: dict[str, Any]) -> list[dict[str, str]]:
    for field in step.get("data_schema", []):
        if field.get("name") == "enabled_devices":
            return list(field["selector"]["select"]["options"])
    return []


def host_grab_busy(path: str) -> bool:
    import evdev

    dev = evdev.InputDevice(path)
    try:
        dev.grab()
    except OSError as err:
        return err.errno == errno.EBUSY
    else:
        dev.ungrab()
        return False
    finally:
        dev.close()


def validate_no_input(ha: HA) -> None:
    step = api(ha, "POST", "/api/config/config_entries/flow", {"handler": "evmqtt"})
    check(step.get("type") == "form", f"flow opens a form: {step.get('type')}")
    check(
        step.get("errors") == {"base": "no_input"},
        f"no /dev/input gives no_input: {step.get('errors')}",
    )
    api(ha, "DELETE", f"/api/config/config_entries/flow/{step['flow_id']}")


def validate_uinput(ha: HA, ui: Any) -> None:
    import evdev
    from evdev import ecodes

    path = ui.device.path
    print(f"     uinput remote at {path}, created after HA start", flush=True)

    flow = "/api/config/config_entries/flow"
    step = api(ha, "POST", flow, {"handler": "evmqtt"})
    check(step.get("type") == "form", f"flow opens a form: {step.get('errors')}")
    labels = [o["label"] for o in options(step)]
    check(
        not any(DEVICE_NAME in label for label in labels),
        "uinput remote hidden while virtual devices are excluded",
    )
    step = api(
        ha,
        "POST",
        f"{flow}/{step['flow_id']}",
        {"include_virtual": True, "enabled_devices": []},
    )
    ours = [o for o in options(step) if DEVICE_NAME in o["label"]]
    check(len(ours) == 1, f"uinput remote listed with virtual devices: {ours}")
    if not ours:
        return
    result = api(
        ha,
        "POST",
        f"{flow}/{step['flow_id']}",
        {"include_virtual": True, "enabled_devices": [ours[0]["value"]]},
    )
    check(result.get("type") == "create_entry", f"entry created: {result.get('type')}")
    entries = api(ha, "GET", "/api/config/config_entries/entry?domain=evmqtt")
    entry_id = entries[0]["entry_id"] if entries else ""

    def states() -> dict[str, dict[str, Any]]:
        return {s["entity_id"]: s for s in api(ha, "GET", "/api/states")}

    ok = wait(
        lambda: (
            find(states(), "event", "ci_remote_key")
            and find(states(), "switch", "ci_remote_enabled")
        ),
        60,
    )
    check(bool(ok), "event and switch entities created")
    event_id = find(states(), "event", "ci_remote_key") or ""
    switch_id = find(states(), "switch", "ci_remote_enabled") or ""
    print(f"     entities: {event_id}, {switch_id}", flush=True)
    check(states()[switch_id]["state"] == "on", "switch on for the selected device")
    check(host_grab_busy(path), "HA grabbed the device (host grab gets EBUSY)")

    def press(key: int) -> None:
        ui.write(ecodes.EV_KEY, key, 1)
        ui.syn()
        ui.write(ecodes.EV_KEY, key, 0)
        ui.syn()

    press(ecodes.KEY_A)
    ok = wait(lambda: states()[event_id]["attributes"].get("key") == "KEY_A")
    attrs = states()[event_id]["attributes"]
    check(
        ok and attrs.get("event_type") == "press" and attrs.get("state") == "PRESS",
        f"key press reached HA: {attrs.get('event_type')} {attrs.get('key')}",
    )

    api(ha, "POST", "/api/services/switch/turn_off", {"entity_id": switch_id})
    ok = wait(lambda: states()[switch_id]["state"] == "off")
    check(ok and not host_grab_busy(path), "switch off ungrabs")

    holder = evdev.InputDevice(path)
    holder.grab()
    try:
        try:
            api(ha, "POST", "/api/services/switch/turn_on", {"entity_id": switch_id})
            refused = ""
        except RuntimeError as err:
            refused = str(err)
        print(f"     turn_on while busy: {refused}", flush=True)
        check(bool(refused), "switch on while grabbed elsewhere is refused")
        check(states()[switch_id]["state"] == "off", "switch stays off on EBUSY")
    finally:
        holder.ungrab()
        holder.close()

    api(ha, "POST", "/api/services/switch/turn_on", {"entity_id": switch_id})
    ok = wait(lambda: states()[switch_id]["state"] == "on")
    check(ok and host_grab_busy(path), "switch on grabs again")

    api(ha, "POST", f"/api/config/config_entries/entry/{entry_id}/reload")
    ok = wait(
        lambda: (
            states()[switch_id]["state"] == "on"
            and states()[event_id]["state"] != "unavailable"
        )
    )
    check(ok and host_grab_busy(path), "enabled state survives a reload")
    press(ecodes.KEY_B)
    ok = wait(lambda: states()[event_id]["attributes"].get("key") == "KEY_B")
    check(ok, "events flow after reload")

    late = evdev.UInput(name=HOTPLUG_NAME)
    try:
        ok = wait(lambda: find(states(), "switch", "ci_keypad_enabled"), 60)
        late_switch = find(states(), "switch", "ci_keypad_enabled") or ""
        late_event = find(states(), "event", "ci_keypad_key") or ""
        check(bool(ok), f"device hotplugged after setup gets entities: {late_switch}")
        check(
            bool(late_switch) and states()[late_switch]["state"] == "off",
            "hotplugged device starts disabled",
        )
        check(
            bool(late_event) and states()[late_event]["state"] != "unavailable",
            "hotplugged device is read (available)",
        )
        check(not host_grab_busy(late.device.path), "hotplugged device not grabbed")
    finally:
        late.close()

    ui.close()
    ok = wait(lambda: states()[event_id]["state"] == "unavailable")
    check(ok, "unplugged device goes unavailable")
    check(states()[switch_id]["state"] == "unavailable", "switch unavailable unplugged")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", default=HA_IMAGE)
    parser.add_argument("--keep", action="store_true", help="leave the container")
    parser.add_argument("--uinput", action="store_true", help="CI only, needs root")
    args = parser.parse_args()
    if args.uinput and os.environ.get("EVMQTT_UINPUT") != "1":
        parser.error("--uinput needs EVMQTT_UINPUT=1 (keystrokes leak into a desktop)")

    ui = None
    port = free_port()
    name = f"evmqtt-integration-validate-{port}"
    with tempfile.TemporaryDirectory() as tmp:
        config = Path(tmp) / "configuration.yaml"
        config.write_text(
            "homeassistant:\n  name: evmqtt-validate\n  debug: true\n"
            f"http:\n  server_host: 127.0.0.1\n  server_port: {port}\n"
            "api:\nonboarding:\nconfig:\nauth:\n"
            "logger:\n  default: info\n  logs:\n"
            "    custom_components.evmqtt: debug\n"
        )
        devices = (
            ["--device-cgroup-rule", "c 13:* rw", "-v", "/dev/input:/dev/input:ro"]
            if args.uinput
            else []
        )
        try:
            run(
                "docker", "create", "--name", name, "--network", "host",
                "-v", f"{ROOT / 'custom_components'}:/config/custom_components:ro",
                *devices, args.image,
            )  # fmt: skip
            run("docker", "cp", str(config), f"{name}:/config/configuration.yaml")
            run("docker", "start", name)
            ha = HA(port)
            print(f"Waiting for {args.image} on :{port}", flush=True)
            ha.wait_up()
            ha.onboard()
            if args.uinput:
                import evdev

                ui = evdev.UInput(name=DEVICE_NAME)
                validate_uinput(ha, ui)
            else:
                validate_no_input(ha)
            pip = subprocess.run(
                ["docker", "exec", name, "python3", "-m", "pip", "show", "evmqtt", "evdev"],
                capture_output=True,
                text=True,
            )  # fmt: skip
            versions = [
                line for line in pip.stdout.splitlines() if line.startswith("Version")
            ]
            print(f"     installed evmqtt/evdev: {versions}", flush=True)
            logs = subprocess.run(
                ["docker", "logs", name], capture_output=True, text=True
            )
            text = logs.stdout + logs.stderr
            blocking = [
                line for line in text.splitlines() if "Detected blocking call" in line
            ]
            problems = [
                line
                for line in text.splitlines()
                if ("ERROR" in line or "Traceback" in line)
                and "evmqtt" in line
                and "Could not grab" not in line
            ]
            for line in blocking + problems:
                print(f"     HA log: {line}")
            check(not blocking, "no blocking calls detected by HA")
            check(not problems, "no evmqtt errors in the HA log")
        finally:
            if ui is not None:
                try:
                    ui.close()
                except OSError:
                    pass
            if not args.keep:
                subprocess.run(["docker", "rm", "-f", name], capture_output=True)
    failed = [what for ok, what in RESULTS if not ok]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed")
    return 1 if failed or not RESULTS else 0


if __name__ == "__main__":
    sys.exit(main())
