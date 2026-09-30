"""Perception step within the decision cycle.

A perception plugin derives evidence from sensor data. The root modules manage
the step: the plugin contract (``plugin``), the whole-step boundary
(``interface``), selection, activation, request building, and plugin
execution (``plugin_runner``). ``components`` holds component declarations and
their resolution context, ``diagnostics`` the diagnostic sink, and
``evidence`` the evidence values and their text rendering.
"""
