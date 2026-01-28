"""Device discovery for evmqtt.

This module provides functionality to automatically discover all input devices
visible via evdev and generate human-readable names for MQTT topics.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

import evdev

logger = logging.getLogger(__name__)


@dataclass
class DiscoveredDevice:
    """Represents a discovered input device.

    Attributes:
        path: The device path (e.g., /dev/input/event0).
        name: The human-readable device name from evdev.
        slug: A slugified version of the name for use in topics/IDs.
        unique_id: A unique identifier combining path and slug.
        capabilities: List of device capabilities (e.g., EV_KEY, EV_REL).
    """

    path: str
    name: str
    slug: str
    unique_id: str
    capabilities: list[str]

    @property
    def has_keys(self) -> bool:
        """Check if the device supports key events."""
        return "EV_KEY" in self.capabilities


def slugify(text: str) -> str:
    """Convert text to a URL/topic-safe slug.

    Args:
        text: The text to slugify.

    Returns:
        A lowercase, hyphen-separated string with only alphanumeric characters.

    Examples:
        >>> slugify("USB Keyboard")
        'usb-keyboard'
        >>> slugify("Logitech G502 HERO Gaming Mouse")
        'logitech-g502-hero-gaming-mouse'
        >>> slugify("gpio_ir_recv")
        'gpio-ir-recv'
    """
    # Convert to lowercase
    text = text.lower()
    # Replace underscores and spaces with hyphens
    text = re.sub(r"[_\s]+", "-", text)
    # Remove any characters that aren't alphanumeric or hyphens
    text = re.sub(r"[^a-z0-9-]", "", text)
    # Remove multiple consecutive hyphens
    text = re.sub(r"-+", "-", text)
    # Remove leading/trailing hyphens
    text = text.strip("-")
    return text or "unknown-device"


def get_device_capabilities(device: evdev.InputDevice) -> list[str]:
    """Get list of capability names for a device.

    Args:
        device: The evdev InputDevice to query.

    Returns:
        List of capability type names (e.g., ['EV_SYN', 'EV_KEY', 'EV_MSC']).
    """
    capabilities = []
    for cap_type in device.capabilities():
        try:
            cap_name = evdev.ecodes.EV[cap_type]
            capabilities.append(cap_name)
        except KeyError:
            capabilities.append(f"EV_{cap_type}")
    return capabilities


def generate_unique_id(path: str, slug: str) -> str:
    """Generate a unique ID for a device.

    Args:
        path: The device path.
        slug: The slugified device name.

    Returns:
        A unique identifier string.
    """
    # Extract the event number from the path
    event_num = path.split("/")[-1]  # e.g., "event0"
    return f"evmqtt_{slug}_{event_num}"


def discover_devices(filter_keys_only: bool = True) -> list[DiscoveredDevice]:
    """Discover all available input devices.

    Args:
        filter_keys_only: If True, only return devices that support key events.
            This filters out devices like accelerometers that don't produce
            useful keyboard/remote events.

    Returns:
        List of DiscoveredDevice objects for all available devices.
    """
    devices = []
    seen_slugs: dict[str, int] = {}

    for path in sorted(evdev.list_devices()):
        try:
            device = evdev.InputDevice(path)
            capabilities = get_device_capabilities(device)

            # Skip devices without key capabilities if filtering
            if filter_keys_only and "EV_KEY" not in capabilities:
                logger.debug(
                    "Skipping device '%s' (%s) - no key capabilities",
                    device.name,
                    path,
                )
                continue

            # Generate slug and handle duplicates
            base_slug = slugify(device.name)

            # Handle duplicate slugs by appending a number
            if base_slug in seen_slugs:
                seen_slugs[base_slug] += 1
                slug = f"{base_slug}-{seen_slugs[base_slug]}"
            else:
                seen_slugs[base_slug] = 1
                slug = base_slug

            unique_id = generate_unique_id(path, slug)

            discovered = DiscoveredDevice(
                path=path,
                name=device.name,
                slug=slug,
                unique_id=unique_id,
                capabilities=capabilities,
            )
            devices.append(discovered)

            logger.debug(
                "Discovered device: %s (%s) -> slug: %s",
                device.name,
                path,
                slug,
            )

        except OSError as e:
            logger.warning("Could not access device %s: %s", path, e)
            continue

    logger.info("Discovered %d input device(s)", len(devices))
    return devices


def discover_device_by_path(path: str) -> DiscoveredDevice | None:
    """Discover a specific device by path.

    Args:
        path: The device path (e.g., /dev/input/event0).

    Returns:
        DiscoveredDevice if found and accessible, None otherwise.
    """
    try:
        device = evdev.InputDevice(path)
        capabilities = get_device_capabilities(device)
        slug = slugify(device.name)
        unique_id = generate_unique_id(path, slug)

        return DiscoveredDevice(
            path=path,
            name=device.name,
            slug=slug,
            unique_id=unique_id,
            capabilities=capabilities,
        )
    except OSError as e:
        logger.warning("Could not access device %s: %s", path, e)
        return None
