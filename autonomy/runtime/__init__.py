"""Shared decision hosting and vehicle control execution.

AutonomyCycleHost starts/stops a RunConfiguration and runs sensor contexts.
ControlExecution owns mode, freshness, delivery, and command expiry.
ControlTarget implementations only bridge a vehicle's command transport.
"""
