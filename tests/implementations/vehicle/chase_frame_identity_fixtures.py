from __future__ import annotations
import base64
from io import BytesIO
from PIL import Image
from implementations.vehicle.chase_sim.car import (
    CHASE_ATOMIC_EVALUATION_QUERY,
    CHASE_PASSIVE_CAMERA_ID,
)


_PNG_DATA_URL = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


def _atomic_capture(
    *,
    frame_index: int = 42,
    simulation_epoch: str = "chase-run:test",
    action_frame_index: int | None = None,
) -> dict:
    return {
        "contractVersion": 1,
        "captureId": f"chase:evaluation:{simulation_epoch}:chaser:{frame_index}",
        "actorId": "chaser",
        "frameIdentity": {
            "gameId": "chase",
            "simulationEpoch": simulation_epoch,
            "frameIndex": frame_index,
        },
        "playback": {"advanced": False},
        "sensor": {
            "image": {
                "contentType": "image/png",
                "rendererId": "chase-actor-view-threejs-v1",
                "width": 1,
                "height": 1,
                "dataUrl": _PNG_DATA_URL,
            }
        },
        "evaluator": {
            "classification": "non-sensor",
            "shadow": {
                "kind": "visible-observation-summary",
                "visibleWallCount": 99,
                "map": {"privileged": True},
            },
            "reference": {
                "kind": "actor-control-reference",
                "scenarioId": "chaser-depth-obstacles",
                "controlSource": "programmatic",
                "phase": "after-actions",
                "actionFrameIndex": (
                    frame_index if action_frame_index is None else action_frame_index
                ),
                "input": {
                    "source": "programmatic",
                    "forward": True,
                    "reverse": False,
                    "steering": 0.25,
                },
                "action": {
                    "source": "programmatic",
                    "forward": True,
                    "reverse": False,
                    "steering": 0.2,
                    "selectedActionProposalId": "proposal-1",
                },
            },
        },
    }


def _session_state(
    *,
    scenario: str = "chaser-depth-obstacles",
    control_source: str = "programmatic",
    playing: bool = True,
) -> dict:
    return {
        "gameId": "chase",
        "playback": {"isPlaying": playing, "playbackRate": 1},
        "playSidebarSections": [
            {
                "rows": [
                    {"id": "scenario-select", "value": scenario},
                    {"id": "chaser-control-source", "value": control_source},
                ]
            }
        ],
    }


def _session_debug(
    *,
    simulation_epoch: str = "chase-run:test",
    control_source: str = "programmatic",
) -> dict:
    return {
        "gameId": "chase",
        "simulationEpoch": simulation_epoch,
        "actions": {
            "chaserInput": {
                "source": control_source,
                "motion": "forward",
                "forward": True,
                "reverse": False,
                "steering": 0.25,
            }
        },
    }


def _with_passive_receipt(
    capture: dict,
    *,
    control_input: dict | None = None,
) -> dict:
    identity = capture["frameIdentity"]
    fingerprint = {
        "gameId": identity["gameId"],
        "scenarioId": "chaser-depth-obstacles",
        "simulationEpoch": identity["simulationEpoch"],
        "playback": {
            "frameIndex": identity["frameIndex"],
            "phase": "running",
            "pendingAction": False,
        },
        "controlSource": "programmatic",
        "controlInput": control_input,
        "actorId": capture["actorId"],
        "cameraId": CHASE_PASSIVE_CAMERA_ID,
    }
    capture["passiveObservation"] = {
        "supported": True,
        "queryId": CHASE_ATOMIC_EVALUATION_QUERY,
        "actorId": capture["actorId"],
        "cameraId": CHASE_PASSIVE_CAMERA_ID,
        "preservedFields": [
            "gameId",
            "scenarioId",
            "simulationEpoch",
            "playback",
            "controlSource",
            "controlInput",
            "actorId",
            "cameraId",
        ],
        "preservation": {
            "preserved": True,
            "before": fingerprint,
            "after": dict(fingerprint),
        },
    }
    return capture


class ChaseFrameIdentityFixture:
    def _raster_data_url(self, image_format: str, mime: str) -> str:
        output = BytesIO()
        Image.new("RGB", (2, 3), (20, 40, 60)).save(output, format=image_format)
        encoded = base64.b64encode(output.getvalue()).decode("ascii")
        return f"data:{mime};base64,{encoded}"
