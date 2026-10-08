"""HTTP transport for the same run/stop contract as AutonomyCycleHost."""
from __future__ import annotations

import json
import time

from autonomy.runtime.session import RunConfiguration
from implementations.vehicle.picar.donkey_client import DonkeyClient


class OnboardRuntimeClient:
    def __init__(self, base_url: str, *, timeout_s: float = 5.0) -> None:
        self.client = DonkeyClient(base_url, timeout_s=timeout_s)

    def _command(self, command: str, **data) -> dict:
        response = json.loads(self.client.post_json(
            "/autonomy/runtime", {"command": command, **data}
        ))
        if not response.get("ok"):
            raise RuntimeError(response.get("error") or "onboard runtime rejected command")
        return response

    def start(self, configuration: RunConfiguration | None = None, *, record: bool = False) -> dict:
        return self._command("run", configuration=(configuration or RunConfiguration()).to_dict(), record=record)

    def read_recording(self, run_id: str, *, after: int) -> dict:
        return self._command("read_recording", run_id=run_id, after=after)["recording"]

    def stop(self) -> dict:
        return self._command("stop")

    def status(self) -> dict:
        response = json.loads(self.client.get_bytes("/autonomy/runtime"))
        if not response.get("ok"):
            raise RuntimeError(response.get("error") or "onboard runtime unavailable")
        return response

    def restart(self, *, timeout_s: float = 30.0) -> None:
        previous = self.status()["host_run_id"]
        self._command("restart")
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            try:
                current = self.status()
                if current.get("host_run_id") != previous:
                    return
            except (RuntimeError, OSError, ValueError):
                pass
            time.sleep(0.2)
        raise TimeoutError("onboard service did not restart before the deadline")
