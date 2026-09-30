"""Mapper spec path for plugin execution.

``PluginPerceptionMapper`` is defined in ``autonomy.decision_cycle.perception.plugin_runner``.
Existing activation specs that name this module receive that class.
"""

from autonomy.decision_cycle.perception.plugin_runner import PluginPerceptionMapper

__all__ = ["PluginPerceptionMapper"]
