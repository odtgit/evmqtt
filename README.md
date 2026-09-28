# evmqtt - Linux Input Event to MQTT Gateway

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

Capture Linux input events (keyboards, IR remotes, gamepads) and publish them to an MQTT broker. Perfect for integrating hardware buttons and remote controls with Home Assistant.

Based on the original [gist](https://gist.github.com/jamesbulpin/b940e7d81e2e65158f12e59b4d6a0c3c) by James Bulpin.

## Features

- Home Assistant MQTT device discovery: one HA device per input device, with an `event` entity for keys and a `switch` to enable or disable it
- Stable device ids that survive reboots, `eventN` renumbering and (with a serial) port moves
- Grabs only devices you list and have enabled; auto-discovered devices are read without taking them from the system
- Enable state persists across restarts
- Gateway and per-device availability (LWT), hotplug support
- Keeps running while the broker is down and reconnects with backoff
- Home Assistant add-on that uses the Mosquitto add-on's credentials automatically
- Docker, systemd and plain Python deployment

## Installation

### Option 1: Home Assistant Add-on (Recommended)

The easiest way to use evmqtt with Home Assistant is as a Supervisor add-on.

> **Note:** This is an add-on, not a HACS integration. Add-ons require direct hardware access and run as separate Docker containers, which HACS does not support. Install via the Supervisor Add-on Store instead.

#### Add Repository to Supervisor

1. Go to **Settings** → **Add-ons** → **Add-on Store**
2. Click **⋮** (three dots menu) → **Repositories**
3. Add this repository URL: `https://github.com/odtgit/evmqtt`
4. Click **Add** → **Close**
5. Find "evmqtt" in the add-on store and click **Install**
6. Configure via the add-on's **Configuration** tab
7. Start the add-on

#### Local Add-on Installation

Alternatively, clone directly to your local add-ons folder:

```bash
cd /addons
git clone https://github.com/odtgit/evmqtt
```

Then restart Home Assistant, go to **Settings** → **Add-ons** → **evmqtt** and configure.

### Option 2: Docker Container

```bash
# Build the image (use standard Python base for standalone deployment)
docker build --build-arg -t evmqtt .

# Create your config from the template
cp config.example.json config.json
# Edit config.json with your settings

# Run with access to all input devices, including hotplugged ones
docker run -d \
  --name evmqtt \
  --network host \
  --device-cgroup-rule='c 13:* rw' \
  -v /dev/input:/dev/input:ro \
  -v $(pwd)/config.json:/data/config.json:ro \
  -v evmqtt-state:/var/lib/evmqtt \
  -e STATE_DIRECTORY=/var/lib/evmqtt \
  evmqtt
```

`c 13:* rw` gives the container every input device, so auto-discovery also
finds the host's own keyboard. It is read but not grabbed, so it keeps
working, but its keys are published too. List the device you want in
`devices` or `enabled_devices` (see [Device selection](#device-selection)),
or pass only that device instead of the cgroup rule (`--device /dev/input/rc`;
a device passed this way is not seen again after it is replugged).

Or use Docker Compose (also expects a `config.json` created from `config.example.json` as above):

```bash
docker compose up -d
```

### Option 3: Python Package

```bash
# Install from source (the daemon needs the mqtt extra)
pip install ".[mqtt]"

# Or install in development mode
pip install -e ".[mqtt,dev]"

# Run
evmqtt -c config.json -v
```

### Option 4: Systemd Service

```bash
# Clone and install the package
git clone https://github.com/odtgit/evmqtt
cd evmqtt
pip install ".[mqtt]"

# Configure
sudo mkdir -p /etc/evmqtt
sudo cp config.example.json /etc/evmqtt/config.json
sudo chmod 644 /etc/evmqtt/config.json
# Edit /etc/evmqtt/config.json with your settings

# Install service
sudo cp evmqtt.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now evmqtt
```

`evmqtt.service` runs as a systemd `DynamicUser` in the `input` group, so
`/etc/evmqtt/config.json` must stay world-readable (mode 644) for the
service to read it.

## Configuration

The same keys work in `config.json` and in the add-on options.

| Key | Default | Description |
|-----|---------|-------------|
| `mqtt_host` | add-on: provided broker | Broker host. Required outside the add-on. |
| `mqtt_port` | `1883`, `8883` with TLS | Broker port |
| `mqtt_username` / `mqtt_password` | none | Broker credentials |
| `mqtt_tls` | `false` | Connect with TLS |
| `mqtt_tls_ca` | system CAs | CA file for TLS (implies TLS) |
| `name` | `evmqtt <hostname>` | Name of the gateway device in HA |
| `discovery_prefix` | `homeassistant` | HA discovery prefix |
| `base_topic` | `evmqtt/<hostname>` | Root of all state, event and command topics. Must not be under `discovery_prefix`. |
| `auto_discover` | `true` | Select keyboard-like devices automatically. When `false`, only `devices` are used. |
| `devices` | `[]` | Extra devices by stable id, path or name. Listed devices are used even if virtual or not keyboard-like, and are grabbed while enabled. |
| `enabled_devices` | `[]` (all) | Initial state for devices seen for the first time, by id, path or name. Empty enables all. Listed devices are grabbed while enabled. |
| `keystates` | `["PRESS"]` | Any of `PRESS`, `REPEAT`, `RELEASE` |
| `rescan_interval` | `5` | Seconds between hotplug scans, `0` disables |
| `state_file` | see below | Where the enable state is kept |
| `cleanup_legacy` | `true` | Remove retained 1.x discovery on start |
| `log_level` | `info` | `debug`, `info`, `warning`, `error`. `-v`, `-d` and `--log-level` override it. |

Deprecated 1.x keys still load with a warning: `serverip`, `port`,
`username`, `password`, `tls`, `tls_ca` map to the `mqtt_*` keys; `topic` and
`filter_keys_only` are described in [Upgrading from 1.x](#upgrading-from-1x).

Configuration is read from, in order: `-c FILE`, `$EVMQTT_CONFIG`,
`/data/options.json` (add-on), `./config.local.json`, `./config.json`.

```json
{
  "mqtt_host": "192.168.1.10",
  "mqtt_username": "mqtt_user",
  "mqtt_password": "mqtt_password",
  "name": "Living room remote",
  "keystates": ["PRESS", "RELEASE"],
  "enabled_devices": ["gpio-ir-recv-1a2b3c4d"]
}
```

### Home Assistant add-on

Leave **MQTT Host** empty: the add-on declares `services: mqtt:need` and
reads host, port, credentials and TLS of the broker Home Assistant provides
(the Mosquitto add-on) from the Supervisor. Any `mqtt_*` option you set
overrides the provided value.

### Device selection

By default evmqtt uses every device that has at least one real keyboard key,
so mice, power buttons and the video bus are left alone. Virtual devices
(bus `VIRTUAL` or created through uinput, like keyd's
`keyd virtual keyboard` or ydotool) are always skipped unless listed in
`devices` or `enabled_devices`: grabbing keyd's output device takes away all
keyboard input on a desktop. Bluetooth LE keyboards and remotes, which BlueZ
creates through uhid, are not treated as virtual.

In `devices` and `enabled_devices` a path may also be a symlink to the event
node, such as a udev rule's `/dev/input/rc` or `/dev/input/by-id/...`.

`evmqtt --list-devices` prints every device with its stable id and whether
it is selected by default:

```
  /dev/input/event3    razer-razer-huntsman-mini-048d6e11           "Razer Razer Huntsman Mini"  [keyboard] (default)
  /dev/input/event10   keyd-virtual-keyboard-271f969c               "keyd virtual keyboard"  [keyboard, virtual]
```

The id is also in the log and in every event payload (`deviceId`).

### Enable, grab and persistence

A device listed in `devices` or `enabled_devices` is grabbed (`EVIOCGRAB`)
while it is enabled: its keys reach evmqtt only, not the console or desktop.
Turning the switch off releases the grab and stops events; on turns both back
on. A device that cannot be grabbed (for example because another program
holds it) is reported unavailable and retried on the next rescan.

A device found only by auto-discovery is never grabbed, since it may be the
keyboard you use on that machine: its keys are published while it is enabled
and still reach the system. List a remote to grab it, so that keys like
`KEY_POWER` or `KEY_SLEEP` on it do not also act on the host.

The switch state is saved to a state file, keyed by device id:

| Deployment | State file |
|------------|------------|
| add-on | `/data/evmqtt-state.json` |
| systemd (`StateDirectory=evmqtt`) | `/var/lib/evmqtt/state.json` |
| compose (`STATE_DIRECTORY`) | `/var/lib/evmqtt/state.json` in the `evmqtt-state` volume |
| otherwise | `$XDG_STATE_HOME/evmqtt/state.json`, or `~/.local/state/evmqtt/state.json` |

`enabled_devices` only seeds devices the state file does not know yet.

### MQTT over TLS

Set `mqtt_tls` to use the system CA certificates, or `mqtt_tls_ca` to a CA
file. The default port becomes 8883. In a container, mount the CA file:

```yaml
    volumes:
      - "/etc/ssl/certs/ca-certificates.crt:/etc/ssl/certs/ca-certificates.crt:ro"
```

## Usage

```
evmqtt [-h] [-c CONFIG] [--log-level {debug,info,warning,error}] [-v] [-d]
       [--list-devices] [--auto-discover]
```

evmqtt keeps running when the broker is unreachable or refuses the
connection, and reconnects with backoff (1 s up to 60 s). It keeps running
with no devices and picks them up when they are plugged in. It exits with 1
only for configuration errors (bad option, missing CA file, no broker
configured, Supervisor refusing access).

## MQTT contract

`<base>` is `base_topic`, `<id>` the stable device id, `<node>` the gateway
id derived from `base_topic` (`evmqtt/pi` gives `pi`).

| Topic | Retained | Payload |
|-------|----------|---------|
| `<base>/status` | yes | `online` / `offline` (last will) |
| `<base>/<id>/availability` | yes | `online` / `offline` |
| `<base>/<id>/event` | no | key event JSON |
| `<base>/<id>/switch/state` | yes | `ON` / `OFF` |
| `<base>/<id>/switch/set` | | `ON` / `OFF` (command) |
| `<prefix>/device/evmqtt_<node>/config` | yes | gateway discovery |
| `<prefix>/device/evmqtt_<node>_<id>/config` | yes | device discovery |

evmqtt also listens to `<prefix>/status` and republishes discovery when
Home Assistant comes online.

Key event, one message per configured key state:

```json
{
  "event_type": "press",
  "key": "KEY_VOLUMEUP",
  "modifiers": ["KEY_LEFTSHIFT"],
  "state": "PRESS",
  "deviceId": "gpio-ir-recv-1a2b3c4d",
  "deviceName": "gpio_ir_recv",
  "devicePath": "/dev/input/event3"
}
```

`key` is the kernel name of the key, `modifiers` the modifier keys held on
the same device, sorted. Modifier keys and `KEY_NUMLOCK` produce no events
of their own.

Device discovery (`homeassistant/device/evmqtt_pi_gpio-ir-recv-1a2b3c4d/config`):

```json
{
  "device": {
    "identifiers": ["evmqtt_pi_gpio-ir-recv-1a2b3c4d"],
    "name": "gpio_ir_recv",
    "manufacturer": "Logitech",
    "model": "USB Receiver",
    "model_id": "046d:c52b",
    "via_device": "evmqtt_pi"
  },
  "origin": {"name": "evmqtt", "sw_version": "2.1.0", "support_url": "https://github.com/odtgit/evmqtt"},
  "availability": [
    {"topic": "evmqtt/pi/status", "payload_available": "online", "payload_not_available": "offline"},
    {"topic": "evmqtt/pi/gpio-ir-recv-1a2b3c4d/availability", "payload_available": "online", "payload_not_available": "offline"}
  ],
  "availability_mode": "all",
  "components": {
    "event": {
      "platform": "event",
      "unique_id": "evmqtt_pi_gpio-ir-recv-1a2b3c4d_event",
      "name": "Key",
      "icon": "mdi:keyboard",
      "device_class": "button",
      "state_topic": "evmqtt/pi/gpio-ir-recv-1a2b3c4d/event",
      "event_types": ["press"]
    },
    "switch": {
      "platform": "switch",
      "unique_id": "evmqtt_pi_gpio-ir-recv-1a2b3c4d_switch",
      "name": "Enabled",
      "icon": "mdi:keyboard-settings",
      "entity_category": "config",
      "state_topic": "evmqtt/pi/gpio-ir-recv-1a2b3c4d/switch/state",
      "command_topic": "evmqtt/pi/gpio-ir-recv-1a2b3c4d/switch/set",
      "payload_on": "ON",
      "payload_off": "OFF",
      "state_on": "ON",
      "state_off": "OFF"
    }
  }
}
```

`manufacturer` and `model` come from the USB descriptors in sysfs and are
left out when unknown, `model_id` is `vendor:product`. The gateway device
has a `Status` connectivity binary_sensor on `<base>/status`. Discovery needs
Home Assistant 2024.12 or later.

A device that is unplugged goes unavailable and keeps its entities; it comes
back when plugged in again.

## Home Assistant

Each input device shows up as a device with `event.<device>_key` and
`switch.<device>_enabled`. Automation on a key:

```yaml
automation:
  - alias: "Remote volume up"
    triggers:
      - trigger: state
        entity_id: event.gpio_ir_recv_key
    conditions:
      - condition: template
        value_template: >
          {{ trigger.to_state.attributes.event_type == 'press'
             and trigger.to_state.attributes.key == 'KEY_VOLUMEUP' }}
    actions:
      - action: media_player.volume_up
        target:
          entity_id: media_player.living_room
```

Node-RED and other MQTT consumers subscribe to `<base>/+/event` for the JSON
stream.

## Upgrading from 1.x

2.0 changes topics, entities, payloads and some config keys. Old entities
are removed automatically; automations on them have to be rewritten.

**Topics**

| 1.x | 2.0 |
|-----|-----|
| `<topic>/<slug>/state` | `<base>/<id>/event` |
| `<topic>/<slug>/config`, `homeassistant/switch/<uid>/config` | `homeassistant/device/evmqtt_<node>_<id>/config` |
| `<topic>/<slug>/switch/state`, `/switch/set` | `<base>/<id>/switch/state`, `/switch/set` |
| none | `<base>/status`, `<base>/<id>/availability` |

`<slug>` was the name slug (plus `-2` for duplicates, `eventN` in manual
mode); `<id>` is the stable id (name slug plus a hash), so topics no longer
move when `eventN` changes.

**Entities**

- `sensor.<name>_<device>` (last key as state) becomes `event.<device>_key`.
  The key is in the `key` attribute, the state is the event time.
- `switch.<device>_enable` becomes `switch.<device>_enabled`, in the device's
  configuration section.
- Every input device is its own HA device, linked to a new gateway device.

**Payload**

- New: `event_type` (lowercase key state), `modifiers` (list), `deviceId`.
- `key` is the plain key name. 1.x appended held modifiers
  (`KEY_A_KEY_LEFTSHIFT`) and joined aliased names (`KEY_MIN_INTERESTING|KEY_MUTE`);
  2.0 sends `KEY_A` with `"modifiers": ["KEY_LEFTSHIFT"]`, and `KEY_MUTE`.
- `state`, `devicePath` and `deviceName` are unchanged.

**Config**

- `serverip`, `port`, `username`, `password`, `tls`, `tls_ca`: renamed to
  `mqtt_host`, `mqtt_port`, `mqtt_username`, `mqtt_password`, `mqtt_tls`,
  `mqtt_tls_ca`. The old names still work and log a warning.
- `topic`: deprecated. If it is under `discovery_prefix` (the 1.x default
  `homeassistant/sensor/evmqtt`), it is ignored for state topics, which move
  to `base_topic`. If it is elsewhere and `base_topic` is not set, it becomes
  `base_topic`. In both cases it tells the cleanup where the 1.x discovery is.
- `filter_keys_only`: ignored. The default filter is stricter (keyboard-like,
  no virtual devices); list anything else in `devices`.
- `devices` and `enabled_devices` accept ids and names as well as paths, and
  `devices` no longer requires `auto_discover: false`.
- `auto_discover` now defaults to `true` in `config.json` too.
- Since 2.1.0, only devices listed in `devices` or `enabled_devices` are
  grabbed. 1.x and 2.0.0 grabbed every device they used; list your devices
  to keep that.
- Add-on: `mqtt_host` can be left empty to use the Mosquitto add-on.
- Enable/disable is now kept in a state file instead of the retained switch
  topic; the first 2.0 start seeds it from `enabled_devices`.

**Automations**

- Replace `state` triggers on `sensor.*` with a `state` trigger on the
  `event.*` entity and a condition on `trigger.to_state.attributes.key`
  (see the example above). A `to:` on the key no longer works: the state of
  an event entity is a timestamp.
- Keys with modifiers: check `attributes.modifiers` instead of matching
  `KEY_A_KEY_LEFTSHIFT`.
- MQTT triggers and Node-RED flows: subscribe to `<base>/+/event`.
- Switches: update entity ids.

**Cleanup of old entities**

On the first connect evmqtt subscribes for a few seconds to
`<prefix>/+/+/config` and `<topic>/+/config`, and clears (empty retained
message) only configs whose `unique_id` starts with `evmqtt_` and whose
`state_topic` is under the 1.x topic, plus the retained 1.x switch state.
Home Assistant then removes the old sensor and switch entities. Nothing else
is touched: other integrations' configs, unparseable payloads and 2.0 device
configs are left alone. Set `cleanup_legacy: false` to skip it.

If several 1.x gateways shared one broker and topic, the first upgraded one
removes the 1.x entities of all of them; the others recreate theirs on their
next 1.x start. Upgrade them together, or set `cleanup_legacy: false` until
the last one is upgraded.

## Core Library

`evmqtt.core` is the evdev-only asyncio layer the daemon runs on, usable
without MQTT (`pip install evmqtt`):

```python
import asyncio
from evmqtt.core import DeviceReader, KeyState, is_keyboard_like, list_devices, open_device

async def main():
    info = list_devices(is_keyboard_like)[0]
    reader = DeviceReader(
        open_device(info.path),
        lambda e: e.state is KeyState.PRESS and print(e.key, e.modifiers),
        info=info,
    )
    await reader.run()

asyncio.run(main())
```

`info.id` is stable across reboots and eventN renumbering: name slug plus a
hash of bus, vendor, product, name and either the serial (uniq, plus the
interface number) when the device has a real one, so it survives a port
move, or the port path (phys) when it does not. The MQTT daemon keys its
topics and Home Assistant ids on it.

## Development

### Running Tests

```bash
# Install dev dependencies
pip install -e ".[mqtt,dev]"

# Run tests (see tests/README.md for the broker and uinput tiers)
pytest -m "not broker and not uinput"

# Run with coverage
pytest tests/ -v --cov=evmqtt --cov-report=html
```

### Project Structure

```
evmqtt/
├── src/evmqtt/             # Main package
│   ├── __init__.py
│   ├── core/               # evdev-only asyncio library (no MQTT)
│   ├── __main__.py         # CLI entry point
│   ├── config.py           # Configuration
│   ├── gateway.py          # Daemon: readers, hotplug, persistence, MQTT
│   ├── ha.py               # Topics and HA discovery payloads
│   ├── mqtt_client.py      # paho wrapper
│   ├── state.py            # Enable state file
│   ├── supervisor.py       # Add-on broker lookup
│   └── sysinfo.py          # sysfs: virtual devices, vendor/model
├── tests/                  # Test suite
├── config.yaml             # HA add-on manifest
├── repository.yaml         # HA add-on repository manifest
├── Dockerfile              # Container build
├── pyproject.toml          # Python packaging
└── run.sh                  # Container entrypoint
```

### Type Checking

```bash
mypy src/evmqtt/core
```

### Linting

```bash
ruff check src/ tests/
ruff format src/ tests/
```

### Contributing

PR titles must follow [Conventional Commits](https://www.conventionalcommits.org/):
`<type>(<optional scope>): <description>`, e.g. `feat: add rescan on hotplug` or
`fix(addon): correct default topic`. The PR title becomes the squash-merge commit
subject, which drives the automatic release (see [RELEASING.md](RELEASING.md)).
Allowed types: `feat`, `fix`, `perf`, `refactor`, `docs`, `test`, `ci`, `build`,
`chore`, `style`, `revert`. Add `!` before the colon for a breaking change.

## Requirements

- Python 3.10+
- evdev >= 1.6.0
- paho-mqtt >= 2.0.0 for the daemon (`evmqtt[mqtt]`)
- Linux with input device access

## Troubleshooting

### Permission Denied for Input Device

Add your user to the `input` group:

```bash
sudo usermod -a -G input $USER
# Log out and back in
```

Or run with sudo (not recommended for production).

### Device Not Found

1. Check the device exists: `ls -la /dev/input/`
2. Verify permissions: `groups` should include `input`
3. For Docker/add-on, ensure the device is passed through

### MQTT Connection Failed

evmqtt logs `MQTT broker ... unreachable` or `refused the connection` and
keeps retrying.

1. Verify `mqtt_host` and `mqtt_port`
2. Check username/password (`refused ... Not authorized`)
3. Check the broker: `mosquitto_sub -h <broker> -t 'evmqtt/#' -v`

### Devices Not Appearing in Home Assistant

1. Check MQTT discovery is enabled in Home Assistant and `discovery_prefix` matches it
2. Check the device is selected: `evmqtt --list-devices`, and the log at startup
3. Look in **Settings** → **Devices & Services** → **MQTT** → **Devices**

## License

MIT License - see LICENSE file for details.

## Credits

- Original concept by [James Bulpin](https://gist.github.com/jamesbulpin/b940e7d81e2e65158f12e59b4d6a0c3c)
- [python-evdev](https://python-evdev.readthedocs.io/) for input device access
- [paho-mqtt](https://eclipse.dev/paho/index.php?page=clients/python/index.php) for MQTT client
