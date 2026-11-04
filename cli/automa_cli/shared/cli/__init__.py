"""Argument and output adapters reused by more than one command.

These modules register flags or print a command's input error. They do not
import a feature package. Opening a browser stays in ``shared.open_view``
because services call it while a run is in progress.
"""
