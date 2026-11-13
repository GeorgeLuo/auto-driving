"""Which stop owns a host's run record.

A start may publish the run only while the stop count it read is still the
count on disk. A stop increments that count once it will leave the run
stopped, and puts it back when the stop does not succeed. An earlier start
then cannot publish a run after that stop. The count lives in the run
directory. The lock is held only while the record is read or written, so a
stop does not wait for another host's work or for plugin arming.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from ...shared.io.lock import exclusive
from .store import read_process_record, read_stop_count, write_stop_count


@dataclass(frozen=True)
class Claim:
    """The stop count after this stop claimed the run, and the pid it claimed."""

    stops: int
    pid: int | None


class Run:
    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def stops(self) -> int:
        return read_stop_count(self.directory)

    def claim(self) -> Claim:
        stops = self.stops() + 1
        write_stop_count(self.directory, stops)
        return Claim(stops, _pid(read_process_record(self.directory)))

    def release(self, claim: Claim) -> None:
        """Give the claim back when this stop did not leave the run stopped."""

        if self.stops() == claim.stops:
            write_stop_count(self.directory, claim.stops - 1)

    def pid(self) -> int | None:
        return _pid(read_process_record(self.directory))


def _pid(process: object) -> int | None:
    pid = process.get("pid") if isinstance(process, dict) else None
    return pid if type(pid) is int else None


@contextmanager
def section(directory: Path) -> Iterator[Run]:
    with exclusive(directory):
        yield Run(directory)


def keep(directory: Path, begun: int, write: Callable[[], None]) -> bool:
    """Write the run record when no stop has claimed it since ``begun``.

    ``False`` when a stop claimed the run. The write does not happen.
    """

    with section(directory) as run:
        if run.stops() != begun:
            return False
        write()
        return True
