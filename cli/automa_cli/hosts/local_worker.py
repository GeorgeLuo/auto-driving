"""Process entry that supervises the local Chase host across restarts."""

from __future__ import annotations

import json
import signal
import socket
import subprocess
import sys
from typing import Any

from implementations.runtime.chase_sim.service import RESTART_EXIT_CODE

from ..releases.errors import RuntimeHostError
from ..releases.paths import bundle_root
from ..releases.store import release_code_dir
from .local import HOST_RECORD
from .staging import automation_dir, staged_host

# The host imports only the release: ``-I`` drops the working tree and user
# site from ``sys.path``; the release directory is put first.
BOOT = (
    "import sys; sys.path.insert(0, sys.argv.pop(1)); "
    "from implementations.runtime.chase_sim.service import main; "
    "raise SystemExit(main(sys.argv[1:]))"
)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def supervise_chase_host(host_name: str) -> int:
    """Supervise the Chase host: start it from the latest release, again on restart.

    Runs as this module's main, which ``ensure_chase_host`` starts. The port
    stays the same across restarts so a client's ``base_url`` survives them.
    """

    vehicle = staged_host(host_name)
    ws_url = (vehicle.get("connection") or {}).get("ws_url")
    runtime_dir = bundle_root(host_name) / "runtime"
    record_path = automation_dir(host_name) / HOST_RECORD
    port = _free_port()
    child: subprocess.Popen[bytes] | None = None

    def forward(signum: int, _frame: Any) -> None:
        if child is not None and child.poll() is None:
            child.send_signal(signum)
        else:
            raise SystemExit(0)

    signal.signal(signal.SIGTERM, forward)
    signal.signal(signal.SIGINT, forward)
    while True:
        try:
            release_dir, release = release_code_dir(host_name)
        except RuntimeHostError as exc:
            print(f"Chase host cannot start: {exc}", file=sys.stderr, flush=True)
            return 2
        print(
            f"Starting the Chase host for {host_name} from release {release['archive_sha256'][:12]}",
            file=sys.stderr,
            flush=True,
        )
        command = [
            sys.executable,
            "-I",
            "-c",
            BOOT,
            str(release_dir),
            "--host-name",
            host_name,
            "--runtime-dir",
            str(runtime_dir),
            "--port",
            str(port),
            "--host-record",
            str(record_path),
            "--release",
            json.dumps(release),
            *(["--ws-url", ws_url] if isinstance(ws_url, str) and ws_url else []),
        ]
        child = subprocess.Popen(command, cwd=record_path.parent)
        code = child.wait()
        if code != RESTART_EXIT_CODE:
            return code


if __name__ == "__main__":
    raise SystemExit(supervise_chase_host(sys.argv[1]))
