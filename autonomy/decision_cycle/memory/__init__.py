"""Memory step within the decision cycle.

A memory plugin retains evidence from the current observation. The root
modules manage the step: the plugin protocol (``plugin``), selection,
activation, and the runner that applies the selection (``plugin_runner``).
``errors.MemoryUpdateError`` stops the cycle when remember cannot continue.
``publication`` names the shared-memory keys where memory publishes its
snapshot and a replacement observation. ``execution.plugin_runtime`` runs one
applied plugin, and ``snapshots`` holds retained-evidence values and the
framework's fallback snapshots.
"""
