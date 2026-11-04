"""Controller releases a host can run.

``paths`` is where a host's controller bundle lives. ``store`` reads and
reconstructs a release from that bundle, and raises ``errors.ReleaseError``
when a release is missing or unreadable.
"""
