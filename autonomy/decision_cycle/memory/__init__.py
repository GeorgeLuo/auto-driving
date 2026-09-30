"""Memory step within the decision cycle.

Memory plugins keep what later frames need in the host map and choose where
to publish it. The root modules manage the step: the plugin protocol
(``plugin``), selection, activation, and the runner that applies the selection
(``plugin_runner``). ``errors.MemoryUpdateError`` stops the cycle when
remember cannot continue. ``publication`` names the key for a replacement
observation. ``evidence`` holds the retained-evidence record types plugins
exchange, and ``execution.plugin_runtime`` runs one applied plugin.
"""
