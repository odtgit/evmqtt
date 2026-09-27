# Tests

| Tier | Run | Needs |
|------|-----|-------|
| fast | `pytest -m "not broker and not uinput"` | nothing |
| broker | `pytest -m broker` | `mosquitto` on PATH |
| uinput | `EVMQTT_UINPUT=1 pytest -m uinput` | writable `/dev/uinput`, the udev rule below |

## uinput tier

Creates real keyboards through `/dev/uinput`. Opt-in locally with
`EVMQTT_UINPUT=1`; CI sets `EVMQTT_REQUIRE_UINPUT=1`, which also turns a
skip into a failure.

Test devices are named `evmqtt-test-*`, use vendor:product `7e57:0001`
and never emit `KEY_POWER`, `KEY_SLEEP`, `KEY_SUSPEND` or `KEY_WAKEUP`.

On a desktop, keep them away from the compositor and logind first:

```
# /etc/udev/rules.d/90-evmqtt-test.rules
SUBSYSTEM=="input", ATTRS{name}=="evmqtt-test-*", ENV{LIBINPUT_IGNORE_DEVICE}="1", TAG-="power-switch"
```

```sh
sudo udevadm control --reload
```

keyd users: exclude the test devices in every config's `[ids]` section,
then `sudo keyd reload`:

```
[ids]
*
-7e57:0001
```
