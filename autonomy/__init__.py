"""Stable, vehicle-agnostic autonomy contracts and controller primitives.

Subpackages are layered by dependency direction:

- vehicle: black-box car input/output contracts
- perception: sensor-to-evidence contracts and reusable primitives
- decision: observation shapes and the staged controller cycle
- runtime: loadable engine contracts and lifecycle management
"""
