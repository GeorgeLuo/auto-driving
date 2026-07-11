from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


def endpoint_url(base_url: str, endpoint: str) -> str:
    return f"{base_url.rstrip('/')}/{endpoint.lstrip('/')}"


class DonkeyClient:
    """Small HTTP adapter for the Donkey web server."""

    def __init__(self, base_url: str = "http://piracer.local:8887", timeout_s: float = 5.0):
        self.base_url = base_url.rstrip("/")
        self.timeout_s = float(timeout_s)

    def get_bytes(self, endpoint: str, timeout_s: float | None = None) -> bytes:
        url = endpoint_url(self.base_url, endpoint)
        try:
            with urllib.request.urlopen(url, timeout=timeout_s or self.timeout_s) as response:
                return response.read()
        except urllib.error.URLError as exc:
            raise RuntimeError(f"GET failed for {url}: {exc}") from exc

    def post_json(
        self,
        endpoint: str,
        payload: dict[str, Any],
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

    def download_frame(self, path: Path, endpoint: str = "/frame.jpg") -> dict[str, Any]:
        image_bytes = self.get_bytes(endpoint, timeout_s=10.0)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(image_bytes)
        return {
            "endpoint": endpoint,
            "path": str(path),
            "bytes": len(image_bytes),
            "captured_at_ms": int(time.time() * 1000),
        }
