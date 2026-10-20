"""The memory step.

Memory plugins keep what later frames need in the host map and choose where
to publish it. ``plugin`` is the memory plugin protocol, ``interface`` the
step's backend, composition, failure policy, report, and the ledger summary
keys tooling reads, and ``runner`` the step runner, which also holds
the per-plugin runtime and records the evidence publisher. ``errors.MemoryUpdateError``
stops the cycle when a memory update cannot continue. ``publication`` names the
keys for the retained evidence and for each belief. ``evidence``
holds the retained-evidence record types plugins exchange, and ``beliefs`` the
checked values a plugin learns.
"""
