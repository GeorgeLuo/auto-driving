"""Count discrete movement obstacles and request one Jev box per ordered slot."""

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
    PerceivedThing, PerceptionEvidenceBatch, PerceptionSignal, ViewLocation,
)
from autonomy.decision_cycle.perception.plugin import (
    PerceptionPluginContract, PerceptionPluginInputs,
)
from implementations.decision_cycle.perception.feeds.camera import (
    CameraFrame, FRONT_CAMERA_RGB_INPUT,
)
from implementations.decision_cycle.perception.shared.obstructions.boxes import zone


CAMERA_CONTEXT = "This is an eye-level camera looking ahead from a mobile observer."
OBSTACLE_TASK = (
    "Find discrete physical obstacles in the visible scene that would impede the "
    "observer's movement through the space. Each obstacle must be a distinct solid "
    "physical object. Exclude the floor, walls, ceiling, shadows, reflections and "
    "flat pictures. Include partially visible obstacles. Treat one object as one "
    "obstacle, not separate faces or parts."
)
OBSTACLE_ORDERING = (
    "Order obstacles from left to right by the horizontal center of their visible "
    "extent. Break a tie by putting the object with the lower bottom edge first."
)


def build_questions(max_obstacles: int) -> dict:
    questions = {
        "obstacle_count": {
            "type": "choice",
            "instructions": (
                "How many distinct movement-impeding obstacles satisfying `task` are "
                "visible in `photo`? Apply `camera_context`. Count separate objects, "
                "including partially visible ones."
            ),
            "criteria": {
                str(i): (f"{i} or more distinct obstacles." if i == max_obstacles
                         else f"Exactly {i} distinct obstacles.")
                for i in range(max_obstacles + 1)
            },
        },
    }
    for i in range(1, max_obstacles + 1):
        questions[f"obstacle_{i}"] = {
            "type": "box",
            "instructions": (
                f"Draw a tight bounding box around the visible extent of obstacle "
                f"number {i} in `photo`, using `ordering` to identify it. Apply "
                "`camera_context` and the obstacle definition in `task`. Bound one "
                "distinct obstacle, not a group of objects or the whole scene. "
                "Include all visible parts of that one object."
            ),
        }
    return questions


def _probability(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("probability must be a number")
    number = float(value)
    if not math.isfinite(number) or not 0 <= number <= 1:
        raise ValueError("probability must be finite and between zero and one")
    return number


class JevDiscreteObstaclesPlugin:
    plugin_id = "jev_discrete_obstacles"
    contract = PerceptionPluginContract(
        inputs=(FRONT_CAMERA_RGB_INPUT,),
        description="Eye-level camera: boxes for distinct obstacles that would impede movement.",
        emits=("signal obstacle_in_sight", "count-selected obstruction_evidence boxes"),
        limitations=(
            "experimental remote Jev vision; requires JEV_API_KEY or TYPESAFE_API_KEY",
            "eye-level framing is a prompt assumption, not a calibrated camera pose",
            "bounded left-to-right slots may repeat or miss objects; no geometric correction",
            "no confidence filtering, deduplication, depth or cross-frame identity",
        ),
        diagnostic_artifacts=("jev_response", "jev_prompt"),
    )

    def __init__(self, *, max_obstacles: int = 6, timeout_s: float = 15.0) -> None:
        self.max_obstacles = int(max_obstacles)
        if not 1 <= self.max_obstacles <= 12:
            raise ValueError("max_obstacles must be between 1 and 12")
        self.timeout_s = float(timeout_s)
        if not math.isfinite(self.timeout_s) or self.timeout_s <= 0:
            raise ValueError("timeout_s must be finite and positive")
        self._session = requests.Session()

    def perceive(self, inputs: PerceptionPluginInputs) -> PerceptionEvidenceBatch:
        frame = inputs.require("frame", CameraFrame)
        key = os.environ.get("JEV_API_KEY") or os.environ.get("TYPESAFE_API_KEY")
        if not key:
            raise RuntimeError("jev_discrete_obstacles requires JEV_API_KEY or TYPESAFE_API_KEY")
        started = time.perf_counter()
        image = Image.fromarray(frame.rgb)
        width, height = image.size
        scale = max(min(1.0, 640 / width, 1568 / height), 280 / min(width, height))
        size = (round(width * scale), round(height * scale))
        if min(size) < 280 or max(size) > 1568:
            raise ValueError("camera aspect ratio cannot fit Jev's image limits")
        if size != image.size:
            image = image.resize(size, Image.Resampling.LANCZOS)
        encoded = io.BytesIO()
        image.save(encoded, "JPEG", quality=90)
        ref = str(uuid.uuid4())
        request = {
            "model": "jev-1.14.0",
            "state": {"photo": ref, "camera_context": CAMERA_CONTEXT,
                      "task": OBSTACLE_TASK, "ordering": OBSTACLE_ORDERING},
            "assets": {ref: {"type": "image", "part": "photo"}},
            "questions": build_questions(self.max_obstacles),
        }
        prepared = time.perf_counter()
        try:
            response = self._session.post(
                "https://api.typesafe.ai/v1/systemone",
                headers={"Authorization": f"Bearer {key}", "TypeSafe-Danger-Zone-Images": "true"},
                files=[("request", (None, json.dumps(request), "application/json")),
                       ("photo", ("frame.jpg", encoded.getvalue(), "image/jpeg"))],
                timeout=self.timeout_s, allow_redirects=False,
            )
        except requests.RequestException:
            raise RuntimeError("Jev transport failed") from None
        completed = time.perf_counter()
        if response.status_code != 200:
            raise RuntimeError(f"Jev HTTP {response.status_code}")
        try:
            payload = response.json()
            if payload["model"] != "jev-1.14.0":
                raise ValueError("unexpected model")
            answers = payload["answers"]
            count_answer = answers["obstacle_count"]
            choice = count_answer["choice"]
            if choice not in request["questions"]["obstacle_count"]["criteria"]:
                raise ValueError("invalid count choice")
            count = int(choice)
            count_confidence = _probability(count_answer["confidence"])
            count_probabilities = {
                str(i): _probability(count_answer["probabilities"][str(i)])
                for i in range(self.max_obstacles + 1)
            }
            boxes = []
            for i in range(1, count + 1):
                answer = answers[f"obstacle_{i}"]
                confidence = _probability(answer["confidence"])
                b = answer["box"]
                bbox = tuple(_probability(b[k]) for k in ("left", "top", "right", "bottom"))
                if bbox[0] >= bbox[2] or bbox[1] >= bbox[3]:
                    raise ValueError("unordered box")
                boxes.append((i, bbox, confidence))
        except (KeyError, TypeError, ValueError):
            raise RuntimeError("Jev returned an invalid obstacle response") from None

        # Count gates the otherwise always-present Box answers. Keep all boxes
        # in that count, including low-confidence and overlapping answers.
        inputs.diagnostics.emit_json("jev_response", "jev-response.json", payload)
        inputs.diagnostics.emit_json("jev_prompt", "jev-prompt.json", {
            "model": request["model"],
            "state": {k: v for k, v in request["state"].items() if k != "photo"},
            "questions": request["questions"],
        })
        things = tuple(PerceivedThing(
            thing_id=f"jev_obstacle_{i:02d}", kind="obstruction_evidence",
            label=f"discrete obstacle {i}",
            location=ViewLocation(frame="image", zone=zone(bbox), bbox_xyxy_norm=bbox),
            confidence=confidence,
            properties={"evidence": "jev_vision", "slot": i, "model": payload["model"],
                        "box_confidence": confidence, "count_confidence": count_confidence},
        ) for i, bbox, confidence in boxes)
        return PerceptionEvidenceBatch(
            signals=(PerceptionSignal(
                signal_id="obstacle_in_sight", value=count > 0,
                confidence=1 - count_probabilities["0"] if count else count_probabilities["0"],
                properties={"obstacle_count": count},
            ),),
            things=things,
            measurements={
                "model": payload["model"], "obstacle_count": count,
                "count_confidence": count_confidence, "count_probabilities": count_probabilities,
                "max_obstacles": self.max_obstacles, "working_width": size[0], "working_height": size[1],
                "prepare_ms": round((prepared - started) * 1000, 3),
                "request_ms": round((completed - prepared) * 1000, 3),
                "usage": payload.get("usage", {}),
            },
        )
