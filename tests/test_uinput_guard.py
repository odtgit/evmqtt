"""The uinput fixture can never emit power or sleep keys. No device is created."""

from __future__ import annotations

import pytest
from evdev import ecodes

from tests.integration.conftest import FORBIDDEN_KEYS, SafeUInput


@pytest.mark.parametrize("code", sorted(FORBIDDEN_KEYS))
def test_forbidden_keys_are_refused(code: int) -> None:
    with pytest.raises(AssertionError, match="forbidden"):
        SafeUInput.write(object(), ecodes.EV_KEY, code, 1)


def test_forbidden_set() -> None:
    assert {
        ecodes.KEY_POWER,
        ecodes.KEY_SLEEP,
        ecodes.KEY_SUSPEND,
        ecodes.KEY_WAKEUP,
    } == FORBIDDEN_KEYS
