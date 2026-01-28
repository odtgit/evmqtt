"""Tests for evmqtt.device_discovery module."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from evmqtt.device_discovery import (
    DiscoveredDevice,
    discover_device_by_path,
    discover_devices,
    generate_unique_id,
    get_device_capabilities,
    slugify,
)


class TestSlugify:
    """Tests for the slugify function."""

    def test_basic_slugify(self) -> None:
        """Test basic slugification."""
        assert slugify("USB Keyboard") == "usb-keyboard"
        assert slugify("Test Device") == "test-device"

    def test_slugify_underscores(self) -> None:
        """Test that underscores are converted to hyphens."""
        assert slugify("gpio_ir_recv") == "gpio-ir-recv"
        assert slugify("test_device_name") == "test-device-name"

    def test_slugify_special_characters(self) -> None:
        """Test that special characters are removed."""
        assert slugify("Device (USB)") == "device-usb"
        assert slugify("Test@#$%Device") == "testdevice"
        assert slugify("Logitech G502 HERO Gaming Mouse") == "logitech-g502-hero-gaming-mouse"

    def test_slugify_multiple_spaces(self) -> None:
        """Test that multiple spaces become single hyphens."""
        assert slugify("Test   Device") == "test-device"
        assert slugify("Multiple    Spaces") == "multiple-spaces"

    def test_slugify_leading_trailing(self) -> None:
        """Test that leading/trailing spaces and hyphens are removed."""
        assert slugify("  Test Device  ") == "test-device"
        assert slugify("-Test-Device-") == "test-device"

    def test_slugify_empty_result(self) -> None:
        """Test that empty result returns 'unknown-device'."""
        assert slugify("") == "unknown-device"
        assert slugify("@#$%") == "unknown-device"

    def test_slugify_uppercase(self) -> None:
        """Test that uppercase is converted to lowercase."""
        assert slugify("TEST DEVICE") == "test-device"
        assert slugify("CamelCase") == "camelcase"


class TestGenerateUniqueId:
    """Tests for the generate_unique_id function."""

    def test_basic_unique_id(self) -> None:
        """Test basic unique ID generation."""
        uid = generate_unique_id("/dev/input/event0", "usb-keyboard")
        assert uid == "evmqtt_usb-keyboard_event0"

    def test_unique_id_different_events(self) -> None:
        """Test unique IDs for different event numbers."""
        uid1 = generate_unique_id("/dev/input/event0", "keyboard")
        uid2 = generate_unique_id("/dev/input/event1", "keyboard")
        assert uid1 != uid2
        assert "event0" in uid1
        assert "event1" in uid2


class TestDiscoveredDevice:
    """Tests for the DiscoveredDevice class."""

    def test_has_keys_true(self) -> None:
        """Test has_keys returns True when EV_KEY is present."""
        device = DiscoveredDevice(
            path="/dev/input/event0",
            name="Test Keyboard",
            slug="test-keyboard",
            unique_id="evmqtt_test-keyboard_event0",
            capabilities=["EV_SYN", "EV_KEY", "EV_MSC"],
        )
        assert device.has_keys is True

    def test_has_keys_false(self) -> None:
        """Test has_keys returns False when EV_KEY is not present."""
        device = DiscoveredDevice(
            path="/dev/input/event0",
            name="Test Accelerometer",
            slug="test-accelerometer",
            unique_id="evmqtt_test-accelerometer_event0",
            capabilities=["EV_SYN", "EV_ABS"],
        )
        assert device.has_keys is False


class TestGetDeviceCapabilities:
    """Tests for the get_device_capabilities function."""

    @patch("evmqtt.device_discovery.evdev.ecodes")
    def test_get_capabilities(self, mock_ecodes: MagicMock) -> None:
        """Test getting device capabilities."""
        mock_ecodes.EV = {0: "EV_SYN", 1: "EV_KEY", 4: "EV_MSC"}

        mock_device = MagicMock()
        mock_device.capabilities.return_value = [0, 1, 4]

        caps = get_device_capabilities(mock_device)
        assert "EV_SYN" in caps
        assert "EV_KEY" in caps
        assert "EV_MSC" in caps

    @patch("evmqtt.device_discovery.evdev.ecodes")
    def test_get_capabilities_unknown_type(self, mock_ecodes: MagicMock) -> None:
        """Test getting capabilities with unknown type."""
        mock_ecodes.EV = {0: "EV_SYN"}

        mock_device = MagicMock()
        mock_device.capabilities.return_value = [0, 99]

        caps = get_device_capabilities(mock_device)
        assert "EV_SYN" in caps
        assert "EV_99" in caps  # Unknown type


class TestDiscoverDevices:
    """Tests for the discover_devices function."""

    @patch("evmqtt.device_discovery.evdev.InputDevice")
    @patch("evmqtt.device_discovery.evdev.list_devices")
    @patch("evmqtt.device_discovery.evdev.ecodes")
    def test_discover_devices_basic(
        self,
        mock_ecodes: MagicMock,
        mock_list_devices: MagicMock,
        mock_input_device: MagicMock,
    ) -> None:
        """Test basic device discovery."""
        mock_ecodes.EV = {0: "EV_SYN", 1: "EV_KEY"}
        mock_list_devices.return_value = ["/dev/input/event0", "/dev/input/event1"]

        mock_device0 = MagicMock()
        mock_device0.path = "/dev/input/event0"
        mock_device0.name = "USB Keyboard"
        mock_device0.capabilities.return_value = [0, 1]  # EV_SYN, EV_KEY

        mock_device1 = MagicMock()
        mock_device1.path = "/dev/input/event1"
        mock_device1.name = "USB Mouse"
        mock_device1.capabilities.return_value = [0, 1]  # EV_SYN, EV_KEY

        mock_input_device.side_effect = [mock_device0, mock_device1]

        devices = discover_devices()

        assert len(devices) == 2
        assert devices[0].name == "USB Keyboard"
        assert devices[0].slug == "usb-keyboard"
        assert devices[1].name == "USB Mouse"
        assert devices[1].slug == "usb-mouse"

    @patch("evmqtt.device_discovery.evdev.InputDevice")
    @patch("evmqtt.device_discovery.evdev.list_devices")
    @patch("evmqtt.device_discovery.evdev.ecodes")
    def test_discover_devices_filter_keys_only(
        self,
        mock_ecodes: MagicMock,
        mock_list_devices: MagicMock,
        mock_input_device: MagicMock,
    ) -> None:
        """Test filtering devices without key capabilities."""
        mock_ecodes.EV = {0: "EV_SYN", 1: "EV_KEY", 3: "EV_ABS"}
        mock_list_devices.return_value = ["/dev/input/event0", "/dev/input/event1"]

        mock_device0 = MagicMock()
        mock_device0.path = "/dev/input/event0"
        mock_device0.name = "USB Keyboard"
        mock_device0.capabilities.return_value = [0, 1]  # Has EV_KEY

        mock_device1 = MagicMock()
        mock_device1.path = "/dev/input/event1"
        mock_device1.name = "Accelerometer"
        mock_device1.capabilities.return_value = [0, 3]  # No EV_KEY

        mock_input_device.side_effect = [mock_device0, mock_device1]

        devices = discover_devices(filter_keys_only=True)

        assert len(devices) == 1
        assert devices[0].name == "USB Keyboard"

    @patch("evmqtt.device_discovery.evdev.InputDevice")
    @patch("evmqtt.device_discovery.evdev.list_devices")
    @patch("evmqtt.device_discovery.evdev.ecodes")
    def test_discover_devices_no_filter(
        self,
        mock_ecodes: MagicMock,
        mock_list_devices: MagicMock,
        mock_input_device: MagicMock,
    ) -> None:
        """Test discovery without filtering."""
        mock_ecodes.EV = {0: "EV_SYN", 1: "EV_KEY", 3: "EV_ABS"}
        mock_list_devices.return_value = ["/dev/input/event0", "/dev/input/event1"]

        mock_device0 = MagicMock()
        mock_device0.path = "/dev/input/event0"
        mock_device0.name = "USB Keyboard"
        mock_device0.capabilities.return_value = [0, 1]

        mock_device1 = MagicMock()
        mock_device1.path = "/dev/input/event1"
        mock_device1.name = "Accelerometer"
        mock_device1.capabilities.return_value = [0, 3]

        mock_input_device.side_effect = [mock_device0, mock_device1]

        devices = discover_devices(filter_keys_only=False)

        assert len(devices) == 2

    @patch("evmqtt.device_discovery.evdev.InputDevice")
    @patch("evmqtt.device_discovery.evdev.list_devices")
    @patch("evmqtt.device_discovery.evdev.ecodes")
    def test_discover_devices_duplicate_slugs(
        self,
        mock_ecodes: MagicMock,
        mock_list_devices: MagicMock,
        mock_input_device: MagicMock,
    ) -> None:
        """Test handling of duplicate device names (slugs)."""
        mock_ecodes.EV = {0: "EV_SYN", 1: "EV_KEY"}
        mock_list_devices.return_value = [
            "/dev/input/event0",
            "/dev/input/event1",
            "/dev/input/event2",
        ]

        # Three devices with the same name
        mock_device0 = MagicMock()
        mock_device0.path = "/dev/input/event0"
        mock_device0.name = "USB Keyboard"
        mock_device0.capabilities.return_value = [0, 1]

        mock_device1 = MagicMock()
        mock_device1.path = "/dev/input/event1"
        mock_device1.name = "USB Keyboard"
        mock_device1.capabilities.return_value = [0, 1]

        mock_device2 = MagicMock()
        mock_device2.path = "/dev/input/event2"
        mock_device2.name = "USB Keyboard"
        mock_device2.capabilities.return_value = [0, 1]

        mock_input_device.side_effect = [mock_device0, mock_device1, mock_device2]

        devices = discover_devices()

        assert len(devices) == 3
        slugs = [d.slug for d in devices]
        assert slugs[0] == "usb-keyboard"
        assert slugs[1] == "usb-keyboard-2"
        assert slugs[2] == "usb-keyboard-3"

    @patch("evmqtt.device_discovery.evdev.InputDevice")
    @patch("evmqtt.device_discovery.evdev.list_devices")
    def test_discover_devices_os_error(
        self,
        mock_list_devices: MagicMock,
        mock_input_device: MagicMock,
    ) -> None:
        """Test that OSError is handled gracefully."""
        mock_list_devices.return_value = ["/dev/input/event0", "/dev/input/event1"]
        mock_input_device.side_effect = OSError("Permission denied")

        devices = discover_devices()

        assert len(devices) == 0


class TestDiscoverDeviceByPath:
    """Tests for the discover_device_by_path function."""

    @patch("evmqtt.device_discovery.evdev.InputDevice")
    @patch("evmqtt.device_discovery.evdev.ecodes")
    def test_discover_by_path_success(
        self,
        mock_ecodes: MagicMock,
        mock_input_device: MagicMock,
    ) -> None:
        """Test discovering a device by path."""
        mock_ecodes.EV = {0: "EV_SYN", 1: "EV_KEY"}

        mock_device = MagicMock()
        mock_device.path = "/dev/input/event0"
        mock_device.name = "USB Keyboard"
        mock_device.capabilities.return_value = [0, 1]

        mock_input_device.return_value = mock_device

        device = discover_device_by_path("/dev/input/event0")

        assert device is not None
        assert device.path == "/dev/input/event0"
        assert device.name == "USB Keyboard"
        assert device.slug == "usb-keyboard"

    @patch("evmqtt.device_discovery.evdev.InputDevice")
    def test_discover_by_path_not_found(
        self,
        mock_input_device: MagicMock,
    ) -> None:
        """Test discovering a device that doesn't exist."""
        mock_input_device.side_effect = OSError("No such file or directory")

        device = discover_device_by_path("/dev/input/event99")

        assert device is None
