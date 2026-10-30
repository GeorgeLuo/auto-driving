"""Controller releases a host can run.

``paths`` is where a host's controller bundle lives. ``store`` reads and
reconstructs a release from that bundle. ``errors.RuntimeHostError`` is what
a missing or unreadable release raises, and what host commands raise when a
vehicle runtime cannot be reached.
"""
