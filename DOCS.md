# evmqtt

Linux input event to MQTT gateway, with Home Assistant MQTT discovery.

## Installation

1. Add this repository to the Supervisor: **Settings** → **Add-ons** →
   **Add-on Store** → **⋮** → **Repositories** → `https://github.com/odtgit/evmqtt`
2. Find "evmqtt" in the store and install it
3. Set **MQTT Host** (or leave it empty to use the Mosquitto add-on, see
   below), and any other options you need
4. Start the add-on

The add-on uses the prebuilt image `ghcr.io/odtgit/evmqtt`; nothing is
built locally.

## Configuration

| Option | Default | Description |
|--------|---------|--------------|
| `mqtt_host` | `""` | Broker host. Empty uses the MQTT broker Home Assistant provides (see below). |
| `mqtt_port` | not set | Broker port. `1883`, or `8883` with TLS, or the port Home Assistant provides. |
| `mqtt_username` | not set | Broker username. Empty uses the credentials Home Assistant provides. |
| `mqtt_password` | not set | Broker password. |
| `mqtt_tls` | not set | Connect with TLS ("MQTTS"). |
| `mqtt_tls_ca` | not set | Path to a CA certificate file for TLS. Setting it implies TLS. |
| `name` | `Input Events` | Name of the gateway device in Home Assistant. Clearing it falls back to `evmqtt <hostname>`. |
| `discovery_prefix` | `homeassistant` | Home Assistant MQTT discovery prefix. |
| `base_topic` | `""` | Root of all state, event and command topics. Empty means `evmqtt/<hostname>`. Must not be under `discovery_prefix`. |
| `auto_discover` | `true` | Select keyboard-like devices automatically (no mice, power buttons, video bus or virtual devices such as keyd). When `false`, only `devices` is used. |
| `keystates` | `["PRESS"]` | Key states reported as events: any of `PRESS`, `RELEASE`, `REPEAT`. |
| `devices` | `[]` | Extra devices to use, by stable id, path or name. Used even if virtual or not keyboard-like, and grabbed while enabled. See the add-on log, or `evmqtt --list-devices`, for ids. |
| `enabled_devices` | `[]` | Devices enabled the first time they are seen, by id, path or name. Empty enables all. Listed devices are grabbed while enabled. After that, the switch in Home Assistant decides, and the state survives restarts. |
| `state_file` | not set | Where the enable/disable state is stored. Defaults to `/data/evmqtt-state.json` in the add-on. |
| `rescan_interval` | `5` | Seconds between scans for plugged/unplugged devices. `0` disables hotplug detection. |
| `cleanup_legacy` | `true` | On start, remove retained 1.x discovery (`unique_id` starting `evmqtt_`) so the old sensor and switch entities disappear. |
| `log_level` | `info` | `debug`, `info`, `warning` or `error`. |

`topic` and `filter_keys_only` also exist, only to support upgrading from
1.x: see [Upgrading from 1.x](#upgrading-from-1x).

## MQTT broker auto-configuration

Leave **MQTT Host** empty and the add-on gets the broker's host, port,
credentials and TLS setting from the Home Assistant Supervisor (the
Mosquitto add-on), via `services: mqtt:need` in `config.yaml`. Any
`mqtt_*` option you do set overrides the corresponding value from the
Supervisor.

If no `mqtt_host` is set and the Supervisor has no MQTT service, the
add-on logs and retries with backoff instead of exiting; it starts
normally once a broker becomes available.

## Device selection

By default (`auto_discover: true`) evmqtt uses every input device that has
at least one real keyboard key, so mice, power buttons and the video bus
are left alone. Virtual devices (bus `VIRTUAL`, or created through uinput,
such as keyd's `keyd virtual keyboard` or ydotool) are always skipped
unless listed in `devices` or `enabled_devices`. Bluetooth LE keyboards and
remotes (BlueZ uhid) are not treated as virtual. A path may be a symlink,
such as `/dev/input/by-id/...`.

## Grabbing

A device listed in `devices` or `enabled_devices` is grabbed while enabled:
its keys reach evmqtt only, not the host. A device found only by
auto-discovery is never grabbed (it may be the host's own keyboard); its keys
are published and still reach the system. List a remote to grab it, so keys
like `KEY_POWER` do not also act on the host. Since 2.1.0; 2.0.0 grabbed every
enabled device.

Check the add-on log at startup, or run `evmqtt --list-devices` in the
add-on's terminal (**Settings** → **Add-ons** → **evmqtt** → **Terminal**
tab, if the add-on has one enabled), to see every device with its stable
id and whether it is selected.

## Upgrading from 1.x

2.0 is a breaking release: topics, entities, payloads and some config keys
changed. See
[Upgrading from 1.x](https://github.com/odtgit/evmqtt/blob/master/README.md#upgrading-from-1x)
in the README before updating from a 1.x install.

## Troubleshooting

**Devices not appearing in Home Assistant**

- Check MQTT discovery is enabled and `discovery_prefix` matches it
- Check the device is selected: add-on log at startup, or `evmqtt --list-devices`
- Look in **Settings** → **Devices & Services** → **MQTT** → **Devices**

**MQTT connection failed**

The log shows `MQTT broker ... unreachable` or `refused the connection` and
keeps retrying.

- Verify `mqtt_host` and `mqtt_port` (or that the Mosquitto add-on is
  running, if `mqtt_host` is empty)
- Check credentials (`refused ... Not authorized`)

**Device not found / permission denied**

The add-on requests `full_access` and `SYS_RAWIO`, so it should see every
`/dev/input` node. If a device is still missing, check it exists on the
host with `ls -la /dev/input/` over SSH, and that `rescan_interval` is not
`0`.

## Support

Issues and questions: <https://github.com/odtgit/evmqtt/issues>

## License

MIT, see [LICENSE](https://github.com/odtgit/evmqtt/blob/master/LICENSE).
