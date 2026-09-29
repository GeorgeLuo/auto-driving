"""Stable, vehicle-agnostic autonomy contracts and controller primitives.

Subpackages are layered by dependency direction:

- vehicle: black-box car input/output contracts
- perception: sensor-to-evidence contracts and reusable primitives
- decision: observation shapes, memory values, and decision cycle steps
- memory: shared run state and memory-step activation/selection
- runtime: loadable engine contracts and lifecycle management
- plugins: step-independent plugin definitions, resolution, and selection
"""
