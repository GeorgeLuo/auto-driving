"""The CLI uses the public vehicle factory without its own provider policy."""
from implementations.vehicle.access import VehicleAccess, create_vehicle_access

__all__ = ["VehicleAccess", "create_vehicle_access"]
