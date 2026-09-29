"""Mapper spec package.

Plugin execution lives in ``autonomy.perception.plugin_runner``. This package
keeps the existing mapper import on that class.
"""

from .plugin_runner import PluginPerceptionMapper

__all__ = ["PluginPerceptionMapper"]
