"""Device discovery for the 1.x daemon: slug and unique_id naming on core discovery."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from evdev import ecodes

from evmqtt.core import has_key_events, list_devices, slugify

__all__ = [
    "DiscoveredDevice",
    "discover_devices",
    "generate_unique_id",
    "slugify",
]

logger = logging.getLogger(__name__)


@dataclass
class DiscoveredDevice:
    """Represents a discovered input device.

    Attributes:
        path: The device path (e.g., /dev/input/event0).
        name: The human-readable device name from evdev.
        slug: A slugified version of the name for use in topics/IDs.
        unique_id: 1.x id combining slug and eventN, used in MQTT topics.
        capabilities: List of device capabilities (e.g., EV_KEY, EV_REL).
        device_id: Stable core device id, unused by the 1.x MQTT contract.
    """

    path: str
    name: str
    slug: str
    unique_id: str
    capabilities: list[str]
    device_id: str = ""


def _capability_names(event_types: frozenset[int]) -> list[str]:
    names = []
    for cap_type in sorted(event_types):
        name = ecodes.EV.get(cap_type)
        names.append(name if isinstance(name, str) else f"EV_{cap_type}")
    return names


def generate_unique_id(path: str, slug: str) -> str:
    """1.x unique id: evmqtt_<slug>_<eventN>."""
    event_num = path.split("/")[-1]
    return f"evmqtt_{slug}_{event_num}"


def discover_devices(filter_keys_only: bool = True) -> list[DiscoveredDevice]:
    """Discover input devices with 1.x slugs.

    Args:
        filter_keys_only: If True, only return devices with any EV_KEY
            capability (1.x semantics; see evmqtt.core.is_keyboard_like for
            the stricter filter).

    Returns:
        DiscoveredDevice list in path order; duplicate names get -2, -3.
    """
    devices = []
    seen_slugs: dict[str, int] = {}

    for info in sorted(list_devices(), key=lambda d: d.path):
        if filter_keys_only and not has_key_events(info):
            logger.debug(
                "Skipping device '%s' (%s) - no key capabilities",
                info.name,
                info.path,
            )
            continue

        base_slug = slugify(info.name)
        if base_slug in seen_slugs:
            seen_slugs[base_slug] += 1
            slug = f"{base_slug}-{seen_slugs[base_slug]}"
        else:
            seen_slugs[base_slug] = 1
            slug = base_slug

        devices.append(
            DiscoveredDevice(
                path=info.path,
                name=info.name,
                slug=slug,
                unique_id=generate_unique_id(info.path, slug),
                capabilities=_capability_names(info.event_types),
                device_id=info.id,
            )
        )
        logger.debug(
            "Discovered device: %s (%s) -> slug: %s id: %s",
            info.name,
            info.path,
            slug,
            info.id,
        )

    logger.info("Discovered %d input device(s)", len(devices))
    return devices
