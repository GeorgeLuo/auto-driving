"""Save and open one processed frame from a host."""

from __future__ import annotations

from ..discovery import runtime_base_url
from ..errors import RuntimeHostError
from ..publications.frame_publication import health_error
from ..publications.host_publications import fetch_frame_sample, host_state
from ..staging import staged_host
from ...providers.metadata import CHASE_SIM, PICAR
from ...shared.open_view import launch_browser
from ...shared.paths import display_path
from .models import StillResult
from .view_discovery import runtime_view_dir


def save_host_still(*, host_name: str, timeout_s: float = 3.0) -> StillResult:
    """Save the host's latest processed frame and open that JPEG.

    The frame is the one ``GET /host/observation/transaction`` already returns.
    An idle Chase host has none until a run is started. A PiCar with none has
    a camera that is not ready.
    """

    try:
        base_url = runtime_base_url(host_name)
    except RuntimeHostError as exc:
        return StillResult(2, "error", error=str(exc))
    try:
        publication, jpeg = fetch_frame_sample(base_url, timeout_s=timeout_s)
    except ConnectionError as exc:
        return StillResult(2, "unavailable", host_name=host_name, base_url=base_url, error=str(exc))
    if jpeg is None:
        return _no_image(host_name, base_url, publication, timeout_s)
    directory = runtime_view_dir(host_name)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "still.jpg"
    path.write_bytes(jpeg)
    report = publication.get("report") if isinstance(publication.get("report"), dict) else {}
    frame_id = report.get("frame_id")
    age_ms = publication.get("result_age_ms")
    health = publication.get("health")
    return StillResult(
        0,
        "saved",
        host_name=host_name,
        base_url=base_url,
        frame_id=frame_id if isinstance(frame_id, str) else "",
        age_ms=age_ms if type(age_ms) is int else None,
        health=health if isinstance(health, str) and health else "unknown",
        path=display_path(path),
        browser=launch_browser(path.resolve().as_uri()),
    )


def _no_image(host_name: str, base_url: str, publication: dict[str, object], timeout_s: float) -> StillResult:
    try:
        provider = staged_host(host_name)["provider"]
    except RuntimeHostError as exc:
        return StillResult(2, "error", error=str(exc))
    if (
        provider == CHASE_SIM.name
        and host_state(base_url, host_name=host_name, timeout_s=timeout_s)["state"] != "running"
    ):
        missing = "Start a run."
    elif provider == PICAR.name:
        missing = "The camera is not ready."
    else:
        missing = health_error(publication) or "The latest frame has no image."
    return StillResult(2, "missing", host_name=host_name, missing=missing)
