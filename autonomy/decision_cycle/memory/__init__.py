"""The memory step.

Memory plugins keep what later frames need in the host map and choose where
to publish it. ``plugin`` is the memory plugin protocol, ``interface`` the
step's report (each plugin's state and the evidence publisher) and the ledger
summary keys tooling reads, and ``runner`` the step runner, which also holds
the per-plugin runtime and records the evidence publisher. ``errors.MemoryUpdateError``
stops the cycle when a memory update cannot continue. ``publication`` names the
key for the retained evidence. ``evidence``
holds the retained-evidence record types plugins exchange.
"""
