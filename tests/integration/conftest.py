"""Shared uinput fixtures.

The uinput tier creates real keyboards. It only runs when EVMQTT_UINPUT=1
(local opt-in, see tests/README.md) or EVMQTT_REQUIRE_UINPUT=1 (CI, where
a skip becomes a failure). Every test device is named evmqtt-test-*, uses
vendor:product 7e57:0001, and can never emit power/sleep keys.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator
from typing import Any

import pytest
from evdev import UInput, ecodes

UINPUT_NODE_WRITABLE = os.path.exists("/dev/uinput") and os.access(
    "/dev/uinput", os.W_OK
)
REQUIRE_UINPUT = os.environ.get("EVMQTT_REQUIRE_UINPUT") == "1"
OPT_IN = REQUIRE_UINPUT or os.environ.get("EVMQTT_UINPUT") == "1"

TEST_PREFIX = "evmqtt-test-"
TEST_VENDOR = 0x7E57
TEST_PRODUCT = 0x0001
FORBIDDEN_KEYS = frozenset(
    {ecodes.KEY_POWER, ecodes.KEY_SLEEP, ecodes.KEY_SUSPEND, ecodes.KEY_WAKEUP}
)

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


class SafeUInput(UInput):  # type: ignore[misc]
    """Refuses to emit power, sleep, suspend or wakeup keys."""

    def write(self, etype: int, code: int, value: int) -> None:
        if etype == ecodes.EV_KEY and code in FORBIDDEN_KEYS:
            raise AssertionError(f"test tried to emit forbidden key {code}")
        super().write(etype, code, value)


UInputFactory = Callable[..., UInput]


@pytest.fixture
def make_uinput() -> Iterator[UInputFactory]:
    """Create UInput devices whose event node is readable; close them at teardown."""
    if not OPT_IN:
        pytest.skip("uinput tier is opt-in: set EVMQTT_UINPUT=1 (see tests/README.md)")
    if not UINPUT_NODE_WRITABLE:
        skip_or_fail("/dev/uinput not writable")
    created: list[UInput] = []

    def factory(events: dict[int, list[int]] | None = None, **kwargs: Any) -> UInput:
        kwargs.setdefault("name", f"{TEST_PREFIX}device")
        assert str(kwargs["name"]).startswith(TEST_PREFIX), kwargs["name"]
        assert kwargs.setdefault("vendor", TEST_VENDOR) == TEST_VENDOR
        assert kwargs.setdefault("product", TEST_PRODUCT) == TEST_PRODUCT
        ui = SafeUInput(events or {ecodes.EV_KEY: KEYBOARD_KEYS}, **kwargs)
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
