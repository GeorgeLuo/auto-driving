from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from autonomy.vehicle import CarInterface
from implementations.vehicle.chase_sim import ChaseSimCar
from implementations.vehicle.picar import create_local_car


@dataclass(frozen=True)
class VehicleAccess:
    car: CarInterface
    image_extension: str
    front_camera_endpoint: str


def create_vehicle_access(vehicle: dict[str, Any], *, timeout_s: float) -> VehicleAccess:
    vehicle_id = str(vehicle.get("vehicle_id") or "vehicle")
    provider = vehicle.get("provider")
    raw_connection = vehicle.get("connection")
    connection: dict[str, Any] = raw_connection if isinstance(raw_connection, dict) else {}

    if provider == "chase-sim":
        ws_url = connection.get("ws_url") if isinstance(connection.get("ws_url"), str) else None
        return VehicleAccess(
            car=ChaseSimCar(ws_url=ws_url, timeout_s=timeout_s, vehicle_id=vehicle_id),
            image_extension="png",
            front_camera_endpoint="atomic-evaluation-capture",
        )
    if provider == "picar":
        base_url = connection.get("base_url") if isinstance(connection.get("base_url"), str) else None
        if not base_url:
            raise ValueError(f"Vehicle {vehicle_id!r} has no Donkey HTTP base URL.")
        return VehicleAccess(
            car=create_local_car(base_url=base_url, timeout_s=timeout_s, vehicle_id=vehicle_id),
            image_extension="jpg",
            front_camera_endpoint="/frame.jpg",
        )
    raise ValueError(f"Vehicle {vehicle_id!r} has unsupported provider {provider!r}.")
