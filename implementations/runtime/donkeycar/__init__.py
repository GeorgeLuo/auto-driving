"""DonkeyCar runtime host implementation."""

from autonomy.runtime.cycle_host import AutonomyCycleHost
from autonomy.runtime.session import DEFAULT_INTERVAL_S
from autonomy.decision_cycle.cycle import DecisionSteps

from .control import DonkeyControlTarget
from .donkey_part import (
    CAMERA_LATEST_FRAME_PATH,
    CAMERA_LATEST_JSON_PATH,
    CAMERA_PUBLICATION_SCHEMA,
    DECISION_LATEST_PATH,
    DECISION_PUBLICATION_SCHEMA,
    LATEST_FRAME_PATH,
    LATEST_JSON_PATH,
    OBSERVATION_PUBLICATION_SCHEMA,
    AutonomyPilotPart,
    LatestCameraFrame,
    LatestObservationState,
    ONBOARD_OBSERVATION_STATE_SCHEMA,
)

__all__ = [
    "AutonomyPilotPart",
    "CAMERA_LATEST_FRAME_PATH",
    "CAMERA_LATEST_JSON_PATH",
    "CAMERA_PUBLICATION_SCHEMA",
    "DEFAULT_INTERVAL_S",
    "DECISION_LATEST_PATH",
    "DECISION_PUBLICATION_SCHEMA",
    "LATEST_FRAME_PATH",
    "LATEST_JSON_PATH",
    "LatestCameraFrame",
    "LatestObservationState",
    "OBSERVATION_PUBLICATION_SCHEMA",
    "ONBOARD_OBSERVATION_STATE_SCHEMA",
    "create_host",
]


def create_host(*, steps: DecisionSteps) -> AutonomyCycleHost:
    return AutonomyCycleHost(steps=steps, target=DonkeyControlTarget())
