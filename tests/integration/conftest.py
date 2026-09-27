"""Shared uinput fixtures. See test_uinput.py for the skip/fail policy."""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator

import pytest
from evdev import UInput, ecodes

UINPUT_NODE_WRITABLE = os.path.exists("/dev/uinput") and os.access(
    "/dev/uinput", os.W_OK
)
REQUIRE_UINPUT = os.environ.get("EVMQTT_REQUIRE_UINPUT") == "1"

KEYBOARD_KEYS = sorted(
    {
        ecodes.KEY_A,
        ecodes.KEY_B,
        ecodes.KEY_MUTE,
        ecodes.KEY_LEFTSHIFT,
    }
)


def skip_or_fail(reason: str) -> None:
    if REQUIRE_UINPUT:
        pytest.fail(reason)
    pytest.skip(reason)


UInputFactory = Callable[..., UInput]


@pytest.fixture
def make_uinput() -> Iterator[UInputFactory]:
    """Create UInput devices whose event node is readable; close them at teardown."""
    if not UINPUT_NODE_WRITABLE:
        skip_or_fail("/dev/uinput not writable")
    created: list[UInput] = []

    def factory(events: dict[int, list[int]] | None = None, **kwargs: object) -> UInput:
        ui = UInput(events or {ecodes.EV_KEY: KEYBOARD_KEYS}, **kwargs)
        created.append(ui)
        if ui.device is None:
            skip_or_fail("uinput device created but no /dev/input/eventN node")
        return ui

    yield factory
    for ui in created:
        try:
            ui.close()
        except OSError:
            pass
