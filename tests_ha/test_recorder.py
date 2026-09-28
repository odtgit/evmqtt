"""Key press attributes stay out of the recorder."""

from __future__ import annotations

from datetime import timedelta

import pytest
from homeassistant.components.recorder import Recorder
from homeassistant.components.recorder.history import get_significant_states
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
)

from tests.fakes import FakeEvdevRegistry, keyboard_capabilities, press
from tests_ha.common import device_id, settle, setup_entry


@pytest.fixture
def mock_recorder_before_hass(async_setup_recorder_instance: None) -> None:
    return


async def test_key_attributes_not_recorded(
    recorder_mock: Recorder, hass: HomeAssistant, fake_evdev: FakeEvdevRegistry
) -> None:
    start = dt_util.utcnow() - timedelta(seconds=1)
    remote = fake_evdev.add("/dev/input/event0", "Remote", keyboard_capabilities())
    await setup_entry(hass, enabled_devices=[device_id(remote)])
    remote.push_all([press("KEY_LEFTCTRL"), press("KEY_P")])
    await settle(hass, remote)
    live = hass.states.get("event.remote_key").attributes
    assert live["key"] == "KEY_P"
    assert live["modifiers"] == ["KEY_LEFTCTRL"]
    await async_wait_recording_done(hass)

    history = await recorder_mock.async_add_executor_job(
        get_significant_states, hass, start, None, ["event.remote_key"]
    )
    states = history["event.remote_key"]
    fired = [s for s in states if s.attributes.get("event_type") == "press"]
    assert fired
    for state in states:
        for attr in ("key", "modifiers", "state", "device_id", "device_path"):
            assert attr not in state.attributes
