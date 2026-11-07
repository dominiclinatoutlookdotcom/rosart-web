"""A steppable clock for the publisher tests: monotonic, wall and boot id (clock seam, E9).

Deadlines are computed on `mono()`; `utc_now()` is display only, and `step_wall` moves it alone
so a test can prove the wall clock is ignored.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone


class FakeClock:
    def __init__(self, mono: float = 1000.0, wall: datetime | None = None, boot_id: str = "boot-1") -> None:
        self._mono = mono
        self._wall = wall or datetime(2026, 11, 8, 9, 0, 0, tzinfo=timezone.utc)
        self._boot_id = boot_id
        self.sleeps: list[float] = []

    def mono(self) -> float:
        return self._mono

    def utc_now(self) -> datetime:
        return self._wall

    def boot_id(self) -> str:
        return self._boot_id

    def advance(self, seconds: float) -> None:
        """Time passes: both clocks move."""
        self._mono += seconds
        self._wall += timedelta(seconds=seconds)

    def step_wall(self, seconds: float) -> None:
        """The wall clock jumps (NTP, DST) while the monotonic clock does not."""
        self._wall += timedelta(seconds=seconds)

    def reboot(self, boot_id: str) -> None:
        self._boot_id = boot_id
        self._mono = 0.0

    def sleep(self, seconds: float) -> None:
        """A fake sleep: records the wait and advances time."""
        self.sleeps.append(seconds)
        self.advance(seconds)
