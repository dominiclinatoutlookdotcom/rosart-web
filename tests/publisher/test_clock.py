"""The real clock behind the clock seam (E9): monotonic time, a boot id, UTC wall time for display."""
from datetime import timezone


def test_the_real_clock_gives_monotonic_time_a_stable_boot_id_and_aware_utc():
    from publisher.clock import RealClock

    clock = RealClock()
    first = clock.mono()

    assert clock.mono() >= first
    assert isinstance(clock.boot_id(), str) and clock.boot_id() and clock.boot_id() == clock.boot_id()
    assert clock.utc_now().tzinfo is not None and clock.utc_now().utcoffset() == timezone.utc.utcoffset(None)
