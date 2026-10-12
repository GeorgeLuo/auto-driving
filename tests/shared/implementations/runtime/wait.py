from __future__ import annotations

import time


def wait_until(predicate, *, timeout_s: float = 5.0, message: str = ""):
    deadline = time.monotonic() + timeout_s
    while True:
        value = predicate()
        if value:
            return value
        if time.monotonic() > deadline:
            raise AssertionError(message or "condition not reached")
        time.sleep(0.02)
