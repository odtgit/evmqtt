# Changelog

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [3.0.0] - 2026-09-28

Breaking release. See [Upgrading to 3.0](README.md#upgrading-to-30).

### Changed

- **Breaking:** auto-discovered devices start disabled (opt-in). Only devices
  listed in `devices` or `enabled_devices` start enabled; enable others with
  their Home Assistant switch. 2.x state files are migrated once: devices that
  were only on because of the old "empty means all" default are switched off,
  with a warning naming them. Before this, every keyboard-like device on the
  host, including a local keyboard, published its keys by default.

### Added

- HACS integration (`custom_components/evmqtt`) for HA Container and Core:
  config flow, native `event` and `switch` entities per device, hotplug,
  optional MQTT mirror with the daemon's event payload.

## [2.1.0] - 2026-09-28

Fixes the 2.0.0 auto-detection regressions from #20.

### Fixed

- Bluetooth LE keyboards and remotes (BlueZ, uhid) are selected by
  auto-discovery again. Only uinput (keyd, ydotool) and `BUS_VIRTUAL`
  devices count as virtual.
- Symlinks work as selectors in `devices` and `enabled_devices`
  (`/dev/input/rc`, `/dev/input/by-id/...`).

### Changed

- Only devices listed in `devices` or `enabled_devices` are grabbed, and only
  while enabled. Auto-discovered devices are read and published but not
  grabbed, so their keys also reach the host. List your devices if you rely
  on grabbing.

## [2.0.0] - 2026-09-27

Breaking release. See [Upgrading from 1.x](README.md#upgrading-from-1x)
before updating; automations on the old sensor entities need rewriting.

### Added

- Async core library (`evmqtt.core`, evdev only), usable without MQTT.
  `pip install evmqtt` for the core, `pip install 'evmqtt[mqtt]'` for the
  daemon.
- HA `event` entity plus an enable `switch` per input device, device-based
  discovery, and a gateway device with a connectivity status entity.
- Gateway last-will and per-device availability, published on unplug.
- Hotplug: devices are picked up and dropped without a restart.
- Automatic cleanup of retained 1.x discovery (`cleanup_legacy: false` to
  opt out).
- Configurable key states (`keystates`: `PRESS`, `RELEASE`, `REPEAT`).
- Multi-arch (amd64, aarch64) images published to `ghcr.io/odtgit/evmqtt`;
  the add-on uses the prebuilt image instead of building locally.
- MQTT over TLS.

### Changed

- Stable device ids survive reboots, `eventN` renumbering and, with a real
  serial, port moves.
- Stricter default device filter: no mice, power buttons, video bus or
  virtual devices (e.g. keyd).
- The daemon keeps running through broker outages and reconnects with
  backoff instead of exiting.
- Only enabled devices are grabbed; the switch state persists across
  restarts.
- Add-on requires Home Assistant 2024.12+ for device-based discovery.

## [1.1.0] - 2026-01-28

- Auto-discovery of keyboard-like devices, plus an enable/disable switch
  per device.
- MQTT over TLS.
- CI: ruff, pytest across Python 3.10-3.13, Docker image build.
- Add-on and packaging fixes: translations, `repository.yaml`,
  `evmqtt.service` `ExecStart`, compose mount path, `run.sh` POSIX
  compatibility, per-arch Docker build.

## [1.0.0] - 2026-01-28

- Home Assistant add-on support.
- Test suite.

## Earlier

Before 1.0.0, evmqtt was an unversioned script (from the original
[gist](https://gist.github.com/jamesbulpin/b940e7d81e2e65158f12e59b4d6a0c3c)):
Docker packaging, a systemd unit, and support for multiple remotes/keys.
