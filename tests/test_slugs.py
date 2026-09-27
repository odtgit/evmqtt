"""Slugs become Home Assistant entity ids."""

from __future__ import annotations

import pytest

from evmqtt.core import slugify


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("USB Keyboard", "usb-keyboard"),
        ("gpio_ir_recv", "gpio-ir-recv"),
        ("Logitech G502 HERO Gaming Mouse", "logitech-g502-hero-gaming-mouse"),
        ("  leading and trailing  ", "leading-and-trailing"),
        ("multiple---hyphens", "multiple-hyphens"),
        ("Special!@#Chars$%^", "specialchars"),
        ("", "unknown-device"),
        ("!!!", "unknown-device"),
    ],
)
def test_slugify(text: str, expected: str) -> None:
    assert slugify(text) == expected
