"""One presence-gated cardboard box from Jev's experimental vision API."""

from __future__ import annotations

import io
import json
import math
import os
import time
import uuid

from PIL import Image
import requests

from autonomy.decision_cycle.perception.evidence.values import (
    PerceivedThing,
    PerceptionEvidenceBatch,
    PerceptionSignal,
    ViewLocation,
)
from autonomy.decision_cycle.perception.plugin import (
    PerceptionPluginContract,
    PerceptionPluginInputs,
)
from implementations.decision_cycle.perception.feeds.camera import (
    CameraFrame,
    FRONT_CAMERA_RGB_INPUT,
)
from implementations.decision_cycle.perception.shared.obstructions.boxes import zone


class JevCardboardBoxPlugin:
    plugin_id = "jev_cardboard_box"
    contract = PerceptionPluginContract(
        inputs=(FRONT_CAMERA_RGB_INPUT,),
        description="Presence and one image-space box for a visible cardboard shipping box.",
        emits=("signal has_box", "signal obstacle_in_sight", "one obstruction_evidence box"),
        limitations=(
            "experimental remote vision; requires JEV_API_KEY or TYPESAFE_API_KEY",
            "one cardboard box per frame; no depth, tracking, or other obstacle classes",
            "network and provider latency are part of every perception call",
        ),
    )

    def __init__(
        self,
        *,
        working_width: int = 640,
        presence_threshold: float = 0.5,
        minimum_box_confidence: float = 0.5,
        timeout_s: float = 10.0,
    ) -> None:
        self.working_width = int(working_width)
        if not 280 <= self.working_width <= 1568:
            raise ValueError("working_width must be between 280 and 1568")
        self.presence_threshold = _probability(presence_threshold)
        self.minimum_box_confidence = _probability(minimum_box_confidence)
        self.timeout_s = float(timeout_s)
        if not math.isfinite(self.timeout_s) or self.timeout_s <= 0:
            raise ValueError("timeout_s must be finite and positive")
        self._session = requests.Session()

    def perceive(self, inputs: PerceptionPluginInputs) -> PerceptionEvidenceBatch:
        frame = inputs.require("frame", CameraFrame)
        api_key = os.environ.get("JEV_API_KEY") or os.environ.get("TYPESAFE_API_KEY")
        if not api_key:
            raise RuntimeError("jev_cardboard_box requires JEV_API_KEY or TYPESAFE_API_KEY")
        started = time.perf_counter()
        image = Image.fromarray(frame.rgb)
        width, height = image.size
        scale = min(1.0, self.working_width / width, 1568 / height)
        scale = max(scale, 280 / min(width, height))
        size = (round(width * scale), round(height * scale))
        if min(size) < 280 or max(size) > 1568:
            raise ValueError("camera aspect ratio cannot fit Jev's image limits")
        if size != image.size:
            image = image.resize(size, Image.Resampling.LANCZOS)
        encoded = io.BytesIO()
        image.save(encoded, format="JPEG", quality=90)
        ref = str(uuid.uuid4())
        request = {
            "model": "jev-1.14.0",
            "state": {"photo": ref},
            "assets": {ref: {"type": "image", "part": "photo"}},
            "questions": {
                "has_box": {
                    "type": "noul",
                    "instructions": "Is there a cardboard shipping box visible in `photo`?",
                },
                "box": {
                    "type": "box",
                    "instructions": (
                        "The cardboard shipping box in `photo`, including all its visible faces and flaps."
                    ),
                },
            },
        }
        # Multipart avoids base64 expansion. One call evaluates both questions;
        # there are no cached responses, retries, or hidden second passes.
        prepared = time.perf_counter()
        try:
            response = self._session.post(
                "https://api.typesafe.ai/v1/systemone",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "TypeSafe-Danger-Zone-Images": "true",
                },
                files=[
                    ("request", (None, json.dumps(request), "application/json")),
                    ("photo", ("frame.jpg", encoded.getvalue(), "image/jpeg")),
                ],
                timeout=self.timeout_s,
                allow_redirects=False,
            )
        except requests.RequestException:
            # Never serialize the request, its headers, or provider error body.
            raise RuntimeError("Jev transport failed") from None
        completed = time.perf_counter()
        if response.status_code != 200:
            raise RuntimeError(f"Jev HTTP {response.status_code}")
        try:
            payload = response.json()
            presence = _probability(payload["answers"]["has_box"]["noul"])
            answer = payload["answers"]["box"]
            box_confidence = _probability(answer["confidence"])
            edges = answer["box"]
            bbox = tuple(_probability(edges[key]) for key in ("left", "top", "right", "bottom"))
            if bbox[0] >= bbox[2] or bbox[1] >= bbox[3]:
                raise ValueError("unordered box")
            if payload["model"] != "jev-1.14.0":
                raise ValueError("unexpected model")
        except (KeyError, TypeError, ValueError):
            raise RuntimeError("Jev returned an invalid detection response") from None
        # Box always returns coordinates, even for absent targets. The separate
        # Noul is essential: never publish an absent target's invented box.
        has_box = presence >= self.presence_threshold
        accepted = has_box and box_confidence >= self.minimum_box_confidence
        things = ()
        if accepted:
            things = (PerceivedThing(
                thing_id="jev_cardboard_box_000",
                kind="obstruction_evidence",
                label="cardboard box",
                location=ViewLocation(frame="image", zone=zone(bbox), bbox_xyxy_norm=bbox),
                confidence=min(presence, box_confidence),
                properties={"evidence": "jev_vision", "model": payload["model"],
                            "presence_probability": presence, "box_confidence": box_confidence},
            ),)
        return PerceptionEvidenceBatch(
            signals=(
                PerceptionSignal(signal_id="has_box", value=has_box,
                                 confidence=presence if has_box else 1 - presence),
                PerceptionSignal(signal_id="obstacle_in_sight", value=accepted,
                                 confidence=min(presence, box_confidence) if accepted else 1 - presence,
                                 properties={"obstacle_count": len(things)}),
            ),
            things=things,
            measurements={
                "model": payload["model"], "presence_probability": presence,
                "box_confidence": box_confidence, "box_accepted": accepted,
                "working_width": size[0], "working_height": size[1],
                "prepare_ms": round((prepared - started) * 1000, 3),
                "request_ms": round((completed - prepared) * 1000, 3),
                "usage": payload.get("usage", {}),
            },
        )


def _probability(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("probability must be a number")
    number = float(value)
    if not math.isfinite(number) or not 0 <= number <= 1:
        raise ValueError("probability must be finite and between zero and one")
    return number
