"""Per-device asyncio reader."""

from __future__ import annotations

import asyncio
import errno
import logging
from collections.abc import Callable
from enum import Enum

from evdev import ecodes

from evmqtt.core.devices import DeviceInfo, InputDeviceLike, describe
from evmqtt.core.events import KeyEvent, KeyState, ReaderStopped, StopReason
from evmqtt.core.keys import KeyConfig, ModifierTracker, canonical_name, key_names

logger = logging.getLogger(__name__)

EventCallback = Callable[[KeyEvent], None]
StoppedCallback = Callable[[ReaderStopped], None]

_STATES = {int(state): state for state in KeyState}


class GrabMode(Enum):
    NEVER = "never"
    ALWAYS = "always"
    WHILE_ENABLED = "while_enabled"


class DeviceReader:
    """Reads one device on the running loop via add_reader. No threads.

    start()/close() are sync and must run on the loop thread; run() wraps
    them for task-style use and is cancellable. Modifier state is tracked
    while disabled, events are only emitted while enabled. The reader owns
    the device and closes it.
    """

    def __init__(
        self,
        device: InputDeviceLike,
        on_event: EventCallback,
        *,
        info: DeviceInfo | None = None,
        key_config: KeyConfig | None = None,
        grab: GrabMode = GrabMode.WHILE_ENABLED,
        enabled: bool = True,
        on_stopped: StoppedCallback | None = None,
    ) -> None:
        self._device = device
        self._on_event = on_event
        self._on_stopped = on_stopped
        self.info = info or describe(device)
        self._config = key_config or KeyConfig()
        self._grab_mode = grab
        self._enabled = enabled
        self._tracker = ModifierTracker(self._config.modifiers)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._done: asyncio.Future[ReaderStopped] | None = None
        self._result: ReaderStopped | None = None
        self._reading = False
        self._grabbed = False

    @property
    def device(self) -> InputDeviceLike:
        return self._device

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def grabbed(self) -> bool:
        return self._grabbed

    @property
    def running(self) -> bool:
        return self._reading

    @property
    def result(self) -> ReaderStopped | None:
        return self._result

    def start(self) -> None:
        """Grab per GrabMode and register the fd. Raises OSError if grab fails."""
        if self._loop is not None:
            raise RuntimeError("reader already started")
        self._loop = asyncio.get_running_loop()
        self._done = self._loop.create_future()
        if self._wants_grab(self._enabled):
            try:
                self._grab()
            except OSError as err:
                self._finish(StopReason.ERROR, err)
                raise
        self._loop.add_reader(self._device.fd, self._on_readable)
        self._reading = True

    async def wait(self) -> ReaderStopped:
        if self._result is not None:
            return self._result
        if self._done is None:
            raise RuntimeError("reader not started")
        return await asyncio.shield(self._done)

    async def run(self) -> ReaderStopped:
        """start(), wait until closed or unplugged, always clean up."""
        self.start()
        try:
            return await self.wait()
        finally:
            self.close()

    def close(self) -> None:
        self._finish(StopReason.STOPPED, None)

    def set_enabled(self, enabled: bool) -> None:
        """Toggle emission; with WHILE_ENABLED also grab/ungrab.

        Raises OSError if the grab fails, leaving the reader disabled.
        """
        if enabled == self._enabled:
            return
        if self._reading and self._grab_mode is GrabMode.WHILE_ENABLED:
            if enabled:
                self._grab()
            else:
                self._ungrab()
        self._enabled = enabled

    def _wants_grab(self, enabled: bool) -> bool:
        if self._grab_mode is GrabMode.ALWAYS:
            return True
        return self._grab_mode is GrabMode.WHILE_ENABLED and enabled

    def _grab(self) -> None:
        if not self._grabbed:
            self._device.grab()
            self._grabbed = True

    def _ungrab(self) -> None:
        if self._grabbed:
            self._grabbed = False
            try:
                self._device.ungrab()
            except OSError:
                pass

    def _finish(self, reason: StopReason, error: OSError | None) -> None:
        if self._result is not None:
            return
        if self._reading and self._loop is not None:
            self._loop.remove_reader(self._device.fd)
        self._reading = False
        self._ungrab()
        try:
            self._device.close()
        except OSError:
            pass
        self._result = ReaderStopped(self.info, reason, error)
        if self._done is not None and not self._done.done():
            self._done.set_result(self._result)
        if reason is not StopReason.STOPPED:
            logger.warning(
                "Reader for '%s' (%s) stopped: %s %s",
                self.info.name,
                self.info.path,
                reason.value,
                error or "",
            )
        if self._on_stopped is not None:
            try:
                self._on_stopped(self._result)
            except Exception:
                logger.exception("Error in on_stopped for '%s'", self.info.path)

    def _on_readable(self) -> None:
        try:
            batch = list(self._device.read())
        except BlockingIOError:
            return
        except OSError as err:
            reason = (
                StopReason.UNPLUGGED if err.errno == errno.ENODEV else StopReason.ERROR
            )
            self._finish(reason, err)
            return
        for raw in batch:
            if self._result is not None:
                return
            if raw.type != ecodes.EV_KEY:
                continue
            try:
                self._handle(raw.code, raw.value, raw.timestamp())
            except Exception:
                logger.exception("Error handling event on '%s'", self.info.path)

    def _handle(self, code: int, value: int, timestamp: float) -> None:
        state = _STATES.get(value)
        if state is None:
            return
        names = key_names(code)
        if any(name in self._config.ignored for name in names):
            return
        self._tracker.update(names, state)
        if not self._enabled:
            return
        self._on_event(
            KeyEvent(
                device_id=self.info.id,
                device_name=self.info.name,
                device_path=self.info.path,
                key=canonical_name(names),
                names=names,
                code=code,
                state=state,
                modifiers=self._tracker.active,
                is_modifier=self._tracker.is_modifier(names),
                timestamp=timestamp,
            )
        )
