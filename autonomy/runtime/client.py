"""HTTP client for the runtime contract in ``autonomy.runtime.routes``.

Every vehicle runtime serves the same routes, so the CLI addresses a runtime
only by ``base_url``.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any

from autonomy.runtime.routes import MEMORY_RESET_PATH, RUNTIME_PATH, STATUS_PATH
from autonomy.runtime.session import RunConfiguration


class RuntimeClient:
    def __init__(self, base_url: str, *, timeout_s: float = 5.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_s = float(timeout_s)

    def request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        """Return the JSON body, including error responses that carry one."""

        url = f"{self.base_url}{path}"
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            url, data=data, method=method,
            headers={} if data is None else {"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_s) as response:
                body = response.read()
        except urllib.error.HTTPError as exc:
            with exc:
                body = exc.read()
            try:
                return json.loads(body)
            except ValueError:
                raise RuntimeError(f"{method} {url} returned HTTP {exc.code}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            reason = getattr(exc, "reason", exc)
            raise RuntimeError(f"{method} {url} failed: {reason}") from exc
        return json.loads(body)

    def _command(self, command: str, **data: Any) -> dict[str, Any]:
        response = self.request("POST", RUNTIME_PATH, {"command": command, **data})
        if not response.get("ok"):
            raise RuntimeError(response.get("error") or f"runtime rejected {command}")
        return response

    def start(self, configuration: RunConfiguration | None = None, *, record: bool = False) -> dict[str, Any]:
        return self._command("run", configuration=(configuration or RunConfiguration()).to_dict(), record=record)

    def read_recording(self, run_id: str, *, after: int) -> dict[str, Any]:
        return self._command("read_recording", run_id=run_id, after=after)["recording"]

    def stop(self) -> dict[str, Any]:
        return self._command("stop")

    def status(self) -> dict[str, Any]:
        response = self.request("GET", RUNTIME_PATH)
        if not response.get("ok"):
            raise RuntimeError(response.get("error") or "runtime unavailable")
        return response

    def host_status(self) -> dict[str, Any]:
        return self.request("GET", STATUS_PATH)

    def reset_memory(self) -> dict[str, Any]:
        return self.request("POST", MEMORY_RESET_PATH, {})

    def restart(self, *, timeout_s: float = 30.0) -> None:
        previous = self.status()["host_run_id"]
        self._command("restart")
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            try:
                if self.status().get("host_run_id") != previous:
                    return
            except (RuntimeError, OSError, ValueError):
                pass
            time.sleep(0.2)
        raise TimeoutError(f"runtime at {self.base_url} did not restart within {timeout_s:g} s")
