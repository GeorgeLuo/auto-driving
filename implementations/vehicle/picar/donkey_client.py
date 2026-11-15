from __future__ import annotations

import json
import urllib.error
import urllib.request


def endpoint_url(base_url: str, endpoint: str) -> str:
    return f"{base_url.rstrip('/')}/{endpoint.lstrip('/')}"


class DonkeyClient:
    """Small HTTP adapter for the Donkey web server."""

    def __init__(self, base_url: str = "http://piracer.local:8887", timeout_s: float = 5.0):
        self.base_url = base_url.rstrip("/")
        self.timeout_s = float(timeout_s)

    def post_json(
        self,
        endpoint: str,
        payload: dict[str, object],
        timeout_s: float | None = None,
    ) -> bytes:
        url = endpoint_url(self.base_url, endpoint)
        body = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout_s or self.timeout_s) as response:
                return response.read()
        except urllib.error.URLError as exc:
            raise RuntimeError(f"POST failed for {url}: {exc}") from exc

    def set_drive(
        self,
        *,
        angle: float = 0.0,
        throttle: float = 0.0,
        drive_mode: str = "user",
        recording: bool = False,
    ) -> None:
        payload = {
            "angle": float(angle),
            "throttle": float(throttle),
            "drive_mode": drive_mode,
            "recording": bool(recording),
        }
        self.post_json("/drive", payload, timeout_s=2.0)

    def stop(self) -> None:
        self.set_drive(angle=0.0, throttle=0.0, drive_mode="user", recording=False)
