"""Each host's latest completed frame, read from the host when a page asks for it.

The viewer serves one page for every host, so it keeps a perception view per
host, as ``hosts viewer`` keeps one for its host. Each read takes the host's
latest frame transaction, whose image is the one published with its report;
the view buffers the images by frame, so a page loads the image that matches
the report it shows.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from urllib.parse import parse_qs, urlencode, urlparse

from autonomy.runtime.http_errors import error_payload

from ...frontend.perception_view import PerceptionView
from ..discovery import runtime_base_url
from ..errors import RuntimeHostError
from ..publications.frame_publication import health_error
from ..publications.host_publications import TransactionCache, fetch_frame_sample
from .observation import ObservationPublisher

_READ_ERRORS = (RuntimeHostError, ConnectionError, OSError, TypeError, ValueError)


@dataclass
class _HostFrames:
    view: PerceptionView
    publisher: ObservationPublisher
    cache: TransactionCache = field(default_factory=TransactionCache)
    lock: threading.Lock = field(default_factory=threading.Lock)


class HostFrames:
    """Hosts' latest frames as the perception page reads them, and their images.

    ``frame_route`` is the read that answers an image; each frame names its
    image there by host and frame.
    """

    def __init__(self, *, frame_route: str, timeout_s: float = 3.0) -> None:
        self.frame_route = frame_route
        self.timeout_s = timeout_s
        self._hosts: dict[str, _HostFrames] = {}
        self._lock = threading.Lock()

    def latest(self, host_name: str) -> dict[str, object]:
        """The host's latest frame, read from it now; a refusal names why there is none."""

        frames = self._frames(host_name)
        with frames.lock:
            try:
                publication, jpeg = fetch_frame_sample(runtime_base_url(host_name), timeout_s=self.timeout_s,
                                                       cache=frames.cache)
                if publication.get("report") is None:
                    frames.publisher.invalidate(health_error(publication))
                else:
                    frames.publisher.publish(publication, jpeg)
            except _READ_ERRORS as exc:
                frames.publisher.invalidate(str(exc))
            body = frames.view.latest_json()
        if body is None:
            return error_payload(frames.view.unavailable_reason)
        document = json.loads(body)
        image = document["image"]
        if image.get("url"):
            frame_id = parse_qs(urlparse(image["url"]).query)["v"][0]
            image["url"] = f"{self.frame_route}?{urlencode({'host_name': host_name, 'v': frame_id})}"
        return document

    def image(self, host_name: str, frame_id: str) -> tuple[bytes, str] | dict[str, object]:
        """A frame's image while the host's view still buffers it."""

        with self._lock:
            frames = self._hosts.get(host_name)
        image = None if frames is None else frames.view.frame(frame_id)
        return image if image is not None else error_payload("That frame's image is no longer available.")

    def _frames(self, host_name: str) -> _HostFrames:
        with self._lock:
            if host_name not in self._hosts:
                view = PerceptionView(host_name=host_name)
                self._hosts[host_name] = _HostFrames(view, ObservationPublisher(view))
            return self._hosts[host_name]
