"""Stable, vehicle-agnostic autonomy contracts and controller primitives.

- vehicle: black-box car input/output contracts
- perception: sensor-to-evidence contracts, activation, selection, and plugin execution
- memory: memory-step activation, selection, runner, protocol, and value contracts
- decision: observation shapes and the decision cycle
- runtime: loadable engine contracts and lifecycle management
- plugins: step-independent plugin definitions, resolution, selection, and reporting
- shared_memory: the host-owned map passed between steps
"""
