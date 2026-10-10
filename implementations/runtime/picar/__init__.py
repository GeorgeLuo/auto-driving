"""PiCar runtime host implemented through DonkeyCar."""

from autonomy.runtime.cycle_host import AutonomyCycleHost
from autonomy.decision_cycle.cycle import DecisionSteps

from .control import DonkeyControlTarget
from .donkey_part import AutonomyPilotPart

__all__ = ["AutonomyPilotPart", "create_host"]


def create_host(*, steps: DecisionSteps) -> AutonomyCycleHost:
    return AutonomyCycleHost(steps=steps, target=DonkeyControlTarget())
