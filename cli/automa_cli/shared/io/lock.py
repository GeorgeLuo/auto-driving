"""Exclusive access to a record shared by threads and by processes.

The viewer, the CLI, and another viewer are different threads or processes.
A thread lock covers the threads in this process. ``flock`` covers the other
processes. The lock is the directory that already holds the record, held only
while that record is read or written.
"""

from __future__ import annotations

import fcntl
import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from ..paths import runtime_root


_guard = threading.Lock()
_locks: dict[str, threading.Lock] = {}


def _thread_lock(path: Path) -> threading.Lock:
    key = str(path)
    with _guard:
        lock = _locks.get(key)
        if lock is None:
            lock = threading.Lock()
            _locks[key] = lock
        return lock


@contextmanager
def exclusive(directory: Path) -> Iterator[None]:
    """Hold ``directory`` against other threads in this process and other processes."""

    directory.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(directory, os.O_RDONLY)
    try:
        with _thread_lock(directory):
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
    finally:
        os.close(descriptor)


@contextmanager
def registry() -> Iterator[None]:
    """The vehicle and simulator records. A host's run directory has its own lock."""

    with exclusive(runtime_root()):
        yield
