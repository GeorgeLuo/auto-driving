"""Stable, vehicle-agnostic autonomy contracts and controller primitives.

- vehicle: black-box car input/output contracts
- perception: sensor-to-evidence contracts; a plugin perceives, and the framework combines batches
- memory: observation-to-retained-evidence contracts; a snapshot is not the host map
- decision: the cycle's current-frame record, retained evidence, and action composition
- runtime: loadable engine contracts and lifecycle management
- plugins: step-independent plugin definitions, resolution, selection, and reporting
- shared_memory: the host-owned map passed between steps, not retained evidence
"""
