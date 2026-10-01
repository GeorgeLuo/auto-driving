"""The perception step.

A perception plugin derives evidence from sensor data. ``plugin`` is the
perception plugin protocol and contract, ``interface`` the step's output and
request-level boundary, ``inputs`` request building, and ``runner`` the step
runner. ``feeds`` holds feed declarations and their resolution
context, ``diagnostics`` the diagnostic sink, and ``evidence`` the evidence
values and their text rendering.
"""
