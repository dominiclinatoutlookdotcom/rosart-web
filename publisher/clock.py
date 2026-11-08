"""The real clock behind the clock seam (spec 006 E9).

Deadlines and windows are computed on ``mono()`` within one ``boot_id``; ``utc_now()`` is for display
and history only, so a wall-clock step changes nothing that matters. Tests inject
``tests/publisher/fake_clock.FakeClock`` instead.
"""
from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone

BOOT_ID_FILE = "/proc/sys/kernel/random/boot_id"


class RealClock:
    def __init__(self) -> None:
        self._fallback_boot_id = uuid.uuid4().hex     # no /proc: one id per process, so a restart is "another boot"

    def mono(self) -> float:
        return time.monotonic()

    def utc_now(self) -> datetime:
        return datetime.now(timezone.utc)

    def boot_id(self) -> str:
        try:
            with open(BOOT_ID_FILE, encoding="ascii") as handle:
                return handle.read().strip() or self._fallback_boot_id
        except OSError:
            return self._fallback_boot_id

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)
