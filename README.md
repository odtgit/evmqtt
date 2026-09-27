# evmqtt - Linux Input Event to MQTT Gateway

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

Capture Linux input events (keyboards, IR remotes, gamepads) and publish them to an MQTT broker. Perfect for integrating hardware buttons and remote controls with Home Assistant.

Based on the original [gist](https://gist.github.com/jamesbulpin/b940e7d81e2e65158f12e59b4d6a0c3c) by James Bulpin.

## Features

- **Auto-discovery** of all input devices - no manual configuration needed
- **Enable/disable switches** in Home Assistant UI for each device
- Human-readable device names in MQTT topics
- Monitors Linux input devices (`/dev/input/eventX`)
- Publishes key events as JSON to MQTT
- Home Assistant MQTT autodiscovery support
- Tracks modifier keys (Shift, Ctrl, Alt, etc.)
- **Home Assistant Add-on** with GUI configuration
- Docker support for standalone deployment

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
docker build --build-arg BUILD_FROM=python:3.13-alpine3.24 -t evmqtt .

# Create your config from the template
cp config.example.json config.json
# Edit config.json with your settings

# Run with access to input devices
docker run -d \
  --name evmqtt \
  --network host \
  --device=/dev/input/event3 \
  -v $(pwd)/config.json:/data/config.json \
  evmqtt
```

Or use Docker Compose (also expects a `config.json` created from `config.example.json` as above):

```bash
docker-compose up -d
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

### Home Assistant Add-on (GUI)

When running as a Home Assistant add-on, configure via the Supervisor UI:

| Option | Description |
|--------|-------------|
| **MQTT Host** | MQTT broker hostname (e.g., `homeassistant.local` for built-in Mosquitto) |
| **MQTT Port** | Broker port (default: `1883`) |
| **MQTT Username** | Authentication username (optional) |
| **MQTT Password** | Authentication password (optional) |
| **Sensor Name** | Display name in Home Assistant |
| **MQTT Topic** | Base topic for events (e.g., `homeassistant/sensor/evmqtt`) |
| **Auto Discover** | Enable automatic discovery of all input devices (default: `true`) |
| **Filter Keys Only** | Only include devices with key capabilities (default: `true`) |
| **Keystates** | List of key states (`PRESS`, `RELEASE`, `REPEAT`) to report (default: `[ PRESS ]`) |
| **Input Devices** | Manual list of device paths (when auto-discover is disabled) |
| **Enabled Devices** | List of device paths to enable by default (when auto-discover is enabled) |
| **Log Level** | Logging verbosity: `debug`, `info`, `warning`, `error` |

### Auto-Discovery Mode (Recommended)

With auto-discovery enabled (the default), evmqtt automatically:

1. Discovers all input devices on your system
2. Creates human-readable MQTT topics based on device names
3. Creates both a **sensor** and an **enable/disable switch** for each device
4. Allows you to enable or disable individual devices from the Home Assistant UI

Example: A device named "gpio_ir_recv" will get:
- Sensor topic: `homeassistant/sensor/evmqtt/gpio-ir-recv/state`
- Switch topic: `homeassistant/sensor/evmqtt/gpio-ir-recv/switch/state`

### JSON Configuration

For standalone deployment, copy `config.example.json` to `config.json` and edit it:

**Auto-discovery mode (recommended):**
```json
{
  "serverip": "192.168.1.100",
  "port": 1883,
  "username": "mqtt_user",
  "password": "mqtt_password",
  "name": "Input Events",
  "topic": "homeassistant/sensor/evmqtt",
  "auto_discover": true,
  "filter_keys_only": true,
  "enabled_devices": []
}
```

**Manual mode:**
```json
{
  "serverip": "192.168.1.100",
  "port": 1883,
  "username": "mqtt_user",
  "password": "mqtt_password",
  "name": "Input Events",
  "topic": "homeassistant/sensor/evmqtt",
  "auto_discover": false,
  "devices": [
    "/dev/input/event0",
    "/dev/input/event3"
  ]
}
```

#### MQTT over TLS
To enable MQTT over TLS ("MQTTS") using system CA certificates,
set the optional key `tls` to `true`:
```json
{
  ...
  "tls": true,
  ...
}
```

When running in a container, add a volume to gain access to the system certificates of the host:
```yaml
    volumes:
      - "/etc/ssl/certs/ca-certificates.crt:/etc/ssl/certs/ca-certificates.crt:ro"
```

To enable MQTT over TLS ("MQTTS") using a custom CA certificate,
set the optional key `tls_ca` to the path of the CA file:
```json
{
  ...
  "tls_ca": "/data/ca.crt",
  ...
}
```

When running in a container, add a volume for the custom CA certificate:
```yaml
    volumes:
      - "./custom-ca.crt:/data/ca.crt"
```

When TLS is enabled, the default MQTT port is 8883, and the `port` key may be omitted.

For the Home Assistant add-on, set `mqtt_tls` / `mqtt_tls_ca` and change `mqtt_port` to 8883 (the add-on default is 1883).

#### Keystates
By default, only key _presses_ are reported.
To customize this, set `keystates` to a combination of `PRESS`, `RELEASE` and `REPEAT`:
```json
{
  ...
  "keystates": [ "PRESS", "RELEASE", "REPEAT" ],
  ...
}
```

### Finding Input Devices

List available input devices:

```bash
# Using evmqtt
evmqtt --list-devices

# Or manually
ls -la /dev/input/
cat /proc/bus/input/devices
```

Look for your keyboard, remote, or other input device and note the `eventX` number.

### Environment Variables

| Variable | Description |
|----------|-------------|
| `EVMQTT_CONFIG` | Path to configuration file |

## Usage

### Command Line Options

```
evmqtt [-h] [-c CONFIG] [-v] [-d] [--list-devices] [--auto-discover]

Options:
  -h, --help            Show help message
  -c, --config CONFIG   Path to configuration file
  -v, --verbose         Enable verbose (INFO) logging
  -d, --debug           Enable debug logging
  --list-devices        List available input devices and exit
  --auto-discover       Enable auto-discovery (overrides config file)
```

### MQTT Topics

With auto-discovery, topics are organized by device:

```
homeassistant/sensor/evmqtt/
├── usb-keyboard/
│   ├── state          # Key events
│   ├── config         # HA sensor autodiscovery
│   └── switch/
│       ├── state      # Enable/disable state (ON/OFF)
│       └── set        # Command topic for toggling
├── gpio-ir-recv/
│   ├── state
│   ├── config
│   └── switch/
│       ├── state
│       └── set
└── ...
```

### MQTT Message Format

Events are published as JSON to `{topic}/{device-slug}/state`:

```json
{
  "key": "KEY_VOLUMEUP",
  "devicePath": "/dev/input/event3",
  "deviceName": "gpio_ir_recv",
  "state": "PRESS"
}
```

With modifier keys held:

```json
{
  "key": "KEY_A_KEY_LEFTSHIFT_KEY_LEFTCTRL",
  "devicePath": "/dev/input/event0",
  "deviceName": "USB Keyboard",
  "state": "PRESS"
}
```

### Home Assistant Autodiscovery

For each device, evmqtt creates:

1. **Sensor Entity**: Shows the last key pressed
   - Entity ID: `sensor.input_events_device_name`
   - Shows key code and device info as attributes

2. **Switch Entity**: Enable/disable monitoring for this device
   - Entity ID: `switch.device_name_enable`
   - Turn OFF to stop receiving events from this device
   - State is retained, so it persists across restarts

## Integration with Home Assistant

### Automation Example

```yaml
automation:
  - alias: "Volume Up Button"
    trigger:
      - platform: state
        entity_id: sensor.input_events_gpio_ir_recv
        to: "KEY_VOLUMEUP"
    action:
      - service: media_player.volume_up
        target:
          entity_id: media_player.living_room
```

### Filtering Devices in the UI

When auto-discovery is enabled, you can control which devices are active directly from Home Assistant:

1. Go to **Settings** → **Devices & Services** → **MQTT**
2. Find the device you want to disable
3. Toggle the **Enable** switch OFF

The device will stop publishing events until re-enabled.

### Node-RED Integration

You can also process events in Node-RED by subscribing to the MQTT topic.

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
move, or the port path (phys) when it does not. The 1.x MQTT topics still
use the name slug and eventN.

## Development

### Running Tests

```bash
# Install dev dependencies
pip install -e ".[mqtt,dev]"

# Run tests
pytest tests/ -v

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
│   ├── config.py           # Configuration handling
│   ├── mqtt_client.py      # MQTT client wrapper
│   ├── input_monitor.py    # Input device monitoring
│   ├── key_handler.py      # Key event processing
│   └── device_discovery.py # Auto-discovery logic
├── tests/                  # Test suite
├── config.yaml             # HA add-on manifest
├── build.yaml              # HA add-on per-arch base images
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

1. Verify broker address and port
2. Check username/password
3. Ensure the broker is running: `mosquitto_sub -h localhost -t '#'`

### Devices Not Appearing in Home Assistant

1. Check MQTT autodiscovery is enabled in Home Assistant
2. Verify the MQTT topic matches HA's autodiscovery prefix (default: `homeassistant`)
3. Look in **Settings** → **Devices & Services** → **MQTT** → **Devices**

## License

MIT License - see LICENSE file for details.

## Credits

- Original concept by [James Bulpin](https://gist.github.com/jamesbulpin/b940e7d81e2e65158f12e59b4d6a0c3c)
- [python-evdev](https://python-evdev.readthedocs.io/) for input device access
- [paho-mqtt](https://eclipse.dev/paho/index.php?page=clients/python/index.php) for MQTT client
