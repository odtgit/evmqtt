"""Device id scheme, keyboard filter and discovery, with fixtures from real hosts."""

from __future__ import annotations

import pytest
from evdev import ecodes

from evmqtt.core import (
    describe,
    has_key_events,
    is_keyboard_like,
    is_placeholder_serial,
    list_devices,
    make_device_id,
    phys_interface,
)
from tests.fakes import FakeEvdevRegistry, FakeInputDevice, keyboard_capabilities

USB, BLUETOOTH, HOST = 0x03, 0x05, 0x19


def keys(*names: str) -> dict[int, list[int]]:
    return {
        ecodes.EV_SYN: [ecodes.SYN_REPORT],
        ecodes.EV_KEY: sorted(ecodes.ecodes[n] for n in names),
    }


KEYBOARD = keyboard_capabilities(ecodes.KEY_LEFTSHIFT, ecodes.KEY_ENTER)
MOUSE = keys("BTN_LEFT", "BTN_RIGHT", "BTN_MIDDLE", "BTN_SIDE", "BTN_EXTRA")
POWER_BUTTON = keys("KEY_POWER", "KEY_WAKEUP")
VIDEO_BUS = keys(
    "KEY_BRIGHTNESSDOWN",
    "KEY_BRIGHTNESSUP",
    "KEY_SWITCHVIDEOMODE",
    "KEY_VIDEO_NEXT",
    "KEY_VIDEO_PREV",
    "KEY_BRIGHTNESS_CYCLE",
    "KEY_BRIGHTNESS_AUTO",
    "KEY_DISPLAY_OFF",
)
IR_REMOTE = keys("KEY_POWER", "KEY_OK", "KEY_UP", "KEY_DOWN", "KEY_VOLUMEUP")
CEC_REMOTE = keys("KEY_SELECT", "KEY_EXIT", "KEY_PLAYPAUSE", "KEY_CHANNELUP")
MACRO_ONLY = keys("KEY_UNKNOWN", "KEY_MACRO27", "KEY_MACRO28")
GAMEPAD = keys("BTN_SOUTH", "BTN_EAST", "BTN_TL", "BTN_START")
ACCELEROMETER = {ecodes.EV_ABS: [ecodes.ABS_X, ecodes.ABS_Y, ecodes.ABS_Z]}
JACK = {ecodes.EV_SW: [ecodes.SW_HEADPHONE_INSERT]}


def fake(
    path: str = "/dev/input/event0",
    name: str = "Dev",
    caps: dict[int, list[int]] | None = None,
    **attrs,
) -> FakeInputDevice:
    return FakeInputDevice(path, name, caps or KEYBOARD, **attrs)


def device_id(**attrs) -> str:
    return describe(fake(**attrs)).id


HUNTSMAN = {
    "name": "Razer Razer Huntsman Mini",
    "uniq": "00000000001A",
    "bustype": USB,
    "vendor": 0x1532,
    "product": 0x0257,
}


def test_id_is_readable_slug_plus_hash() -> None:
    value = device_id(phys="usb-0000:0d:00.3-3/input0", **HUNTSMAN)
    prefix, digest = value.rsplit("-", 1)
    assert prefix == "razer-razer-huntsman-mini"
    assert len(digest) == 8
    assert int(digest, 16) >= 0


def test_id_does_not_depend_on_event_number() -> None:
    before = device_id(path="/dev/input/event3", phys="usb-0000:0d:00.3-3/input0")
    after = device_id(path="/dev/input/event17", phys="usb-0000:0d:00.3-3/input0")
    assert before == after


def test_id_does_not_depend_on_firmware_version() -> None:
    phys = "usb-0000:0d:00.3-3/input0"
    assert device_id(phys=phys, version=0x0111) == device_id(phys=phys, version=0x0200)


def test_interfaces_sharing_a_serial_get_distinct_ids() -> None:
    ids = {device_id(phys=f"usb-0000:0d:00.3-3/input{n}", **HUNTSMAN) for n in range(4)}
    assert len(ids) == 4


def test_port_move_with_serial_keeps_id() -> None:
    before = device_id(phys="usb-0000:0d:00.3-3/input1", **HUNTSMAN)
    other_port = device_id(phys="usb-0000:00:14.0-1.4/input1", **HUNTSMAN)
    assert before == other_port


def test_identical_usb_devices_without_serial_split_on_port() -> None:
    common = {"name": "USB Keyboard", "vendor": 0x046D, "product": 0xC31C}
    port1 = device_id(phys="usb-0000:00:14.0-1/input0", **common)
    port2 = device_id(phys="usb-0000:00:14.0-2/input0", **common)
    hub_port = device_id(phys="usb-0000:00:14.0-1.4/input0", **common)
    assert len({port1, port2, hub_port}) == 3


@pytest.mark.parametrize("uniq", ["", "   ", "0", "000000000000", "00:00:00:00:00:00"])
def test_placeholder_serial_falls_back_to_port(uniq: str) -> None:
    common = {"name": "USB Keyboard", "uniq": uniq}
    port1 = device_id(phys="usb-0000:00:14.0-1/input0", **common)
    port2 = device_id(phys="usb-0000:00:14.0-2/input0", **common)
    assert port1 != port2
    assert port1 == device_id(phys="usb-0000:00:14.0-1/input0", name="USB Keyboard")
    assert is_placeholder_serial(uniq)


def test_real_serials_are_not_placeholders() -> None:
    assert not is_placeholder_serial("00000000001A")
    assert not is_placeholder_serial("11:22:33:44:55:66")
    assert device_id(uniq=" ABC ", phys="a/input0") == device_id(
        uniq="ABC", phys="b/input0"
    )


def test_bluetooth_id_follows_device_mac_not_adapter() -> None:
    common = {
        "name": "Remote",
        "bustype": BLUETOOTH,
        "vendor": 0x0717,
        "product": 0x0101,
    }
    adapter = "aa:bb:cc:dd:ee:ff"
    first = device_id(phys=adapter, uniq="11:22:33:44:55:66", **common)
    second = device_id(phys=adapter, uniq="11:22:33:44:55:77", **common)
    new_adapter = device_id(
        phys="aa:bb:cc:dd:ee:00", uniq="11:22:33:44:55:66", **common
    )
    assert first != second
    assert first == new_adapter


def test_phys_interface() -> None:
    assert phys_interface("usb-0000:00:14.0-3.2/input1") == "input1"
    assert phys_interface("aa:bb:cc:dd:ee:ff") == ""
    assert phys_interface("") == ""


def test_same_fake_serial_gets_suffixes(fake_evdev: FakeEvdevRegistry) -> None:
    for n, port in ((4, 1), (5, 2)):
        fake_evdev.add(
            f"/dev/input/event{n}",
            "Cheap Remote",
            phys=f"usb-0000:00:14.0-{port}/input0",
            uniq="1234567890",
        )
    ids = [d.id for d in list_devices()]
    assert ids[1] == f"{ids[0]}-2"


def test_nodes_sharing_phys_split_on_name() -> None:
    phys = "usb-0000:00:14.0-3/input1"
    consumer = device_id(name="Flirc Consumer Control", phys=phys)
    keyboard = device_id(name="Flirc Keyboard", phys=phys)
    assert consumer != keyboard


def test_acpi_power_buttons_split_on_phys() -> None:
    common = {"name": "Power Button", "bustype": HOST, "product": 1}
    assert device_id(phys="PNP0C0C/button/input0", **common) != device_id(
        phys="LNXPWRBN/button/input0", **common
    )


def test_make_device_id_matches_describe_and_handles_odd_names() -> None:
    assert make_device_id("Kbd", "p", "u", 3, 1, 2) == device_id(
        name="Kbd", phys="p", uniq="u", bustype=3, vendor=1, product=2
    )
    assert make_device_id("!!!", "", "", 0, 0, 0).startswith("unknown-device-")
    long_id = make_device_id("x" * 100, "", "", 0, 0, 0)
    assert len(long_id) == 32 + 1 + 8


@pytest.mark.parametrize(
    ("caps", "keyboard_like", "has_keys"),
    [
        (KEYBOARD, True, True),
        (IR_REMOTE, True, True),
        (CEC_REMOTE, True, True),
        (MACRO_ONLY, True, True),
        (MOUSE, False, True),
        (POWER_BUTTON, False, True),
        (VIDEO_BUS, False, True),
        (GAMEPAD, False, True),
        (ACCELEROMETER, False, False),
        (JACK, False, False),
    ],
    ids=[
        "keyboard",
        "ir-remote",
        "cec-remote",
        "razer-macro-iface",
        "mouse",
        "power-button",
        "video-bus",
        "gamepad",
        "accelerometer",
        "jack",
    ],
)
def test_keyboard_like_filter(
    caps: dict[int, list[int]], keyboard_like: bool, has_keys: bool
) -> None:
    info = describe(fake(caps=caps))
    assert is_keyboard_like(info) is keyboard_like
    assert info.is_keyboard_like is keyboard_like
    assert has_key_events(info) is has_keys
    assert info.has_key_events is has_keys


def test_describe_exposes_identity_and_key_capabilities() -> None:
    info = describe(
        fake(
            path="/dev/input/event5",
            caps=keys("KEY_MUTE", "KEY_A", "BTN_LEFT"),
            phys="usb-0000:0d:00.3-3/input0",
            **HUNTSMAN,
        )
    )
    assert info.path == "/dev/input/event5"
    assert info.name == "Razer Razer Huntsman Mini"
    assert info.phys == "usb-0000:0d:00.3-3/input0"
    assert info.uniq == "00000000001A"
    assert (info.bustype, info.vendor, info.product) == (USB, 0x1532, 0x0257)
    assert info.event_types == {ecodes.EV_SYN, ecodes.EV_KEY}
    assert info.key_names() == ["KEY_A", "KEY_MUTE", "BTN_LEFT"]


def test_list_devices_orders_by_event_number_closes_and_filters(
    fake_evdev: FakeEvdevRegistry,
) -> None:
    kbd = fake_evdev.add("/dev/input/event10", "Kbd", KEYBOARD, phys="usb-1/input0")
    mouse = fake_evdev.add("/dev/input/event2", "Mouse", MOUSE, phys="usb-2/input0")
    fake_evdev.add("/dev/input/event1", "Power Button", POWER_BUTTON, phys="LNX")
    fake_evdev.deny("/dev/input/event3")

    everything = list_devices()
    assert [d.path for d in everything] == [
        "/dev/input/event1",
        "/dev/input/event2",
        "/dev/input/event10",
    ]
    assert kbd.closed and mouse.closed

    assert [d.name for d in list_devices(is_keyboard_like)] == ["Kbd"]
    assert [d.name for d in list_devices(has_key_events)] == [
        "Power Button",
        "Mouse",
        "Kbd",
    ]


def test_list_devices_suffixes_exact_duplicates_before_filtering(
    fake_evdev: FakeEvdevRegistry,
) -> None:
    for n in (4, 5, 6):
        fake_evdev.add(
            f"/dev/input/event{n}", "py-evdev-uinput", phys="py-evdev-uinput"
        )
    fake_evdev.add(
        "/dev/input/event7", "py-evdev-uinput", MOUSE, phys="py-evdev-uinput"
    )
    ids = [d.id for d in list_devices()]
    base = ids[0]
    assert ids[1:3] == [f"{base}-2", f"{base}-3"]
    assert ids[3] != base
    assert len(set(ids)) == 4


def test_list_devices_skips_device_that_fails_to_query(
    fake_evdev: FakeEvdevRegistry, monkeypatch: pytest.MonkeyPatch
) -> None:
    broken = fake_evdev.add("/dev/input/event0", "Broken")
    fake_evdev.add("/dev/input/event1", "Good")

    def fail(*args, **kwargs):
        raise OSError(19, "No such device")

    monkeypatch.setattr(broken, "capabilities", fail)
    assert [d.name for d in list_devices()] == ["Good"]
    assert broken.closed
