"""Polling cadence for the host run monitor and viewer."""

import time

PERCEPTION_COPY_INTERVAL_S = 1 / 30
MAX_POLL_GAP_S = 0.1


def pause_s(started: float, interval_s: float) -> float:
    """Target the interval on fast hosts; back off after slower passes.

    A 30 ms pass waits 30 ms (~16.7 Hz); a 200 ms pass waits 100 ms.
    """
    elapsed = max(0.0, time.monotonic() - started)
    return max(interval_s - elapsed, min(elapsed, MAX_POLL_GAP_S))
