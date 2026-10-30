"""Controller releases a host can run.

``store`` reads and reconstructs a release from the host's controller bundle.
``errors.RuntimeHostError`` is what a missing or unreadable release raises,
and what host commands raise when a vehicle runtime cannot be reached.
"""
