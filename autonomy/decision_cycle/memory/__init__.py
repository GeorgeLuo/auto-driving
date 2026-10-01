"""The memory step.

Memory plugins keep what later frames need in the host map and choose where
to publish it. ``plugin`` is the memory plugin protocol and ``runner`` the
step runner. ``errors.MemoryUpdateError`` stops the cycle when a memory
update cannot continue. ``publication`` names the key for a replacement
observation. ``evidence`` holds the retained-evidence record types plugins
exchange, and ``execution.plugin_runtime`` runs one applied plugin.
"""
