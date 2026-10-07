"""Host the shared decision and execution runtime on a Chase car."""
from autonomy.runtime.car_target import CarControlTarget
from autonomy.runtime.cycle_host import AutonomyCycleHost
from autonomy.decision_cycle.cycle import DecisionSteps
from implementations.vehicle.chase_sim import ChaseSimCar


def create_host(car: ChaseSimCar, *, steps: DecisionSteps) -> AutonomyCycleHost:
    return AutonomyCycleHost(steps=steps, target=CarControlTarget(car))
