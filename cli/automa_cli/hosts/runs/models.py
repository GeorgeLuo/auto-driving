"""Identities that cross the run worker's process boundary.

``worker`` is the background entry point. Stop still recognizes a process
started as ``runs.automation`` or ``hosts.automation`` before that move.
The match is the exact module token, not a prefix.
"""

WORKER_MODULE = "cli.automa_cli.hosts.runs.worker"
RECOGNIZED_WORKER_MODULES = (
    WORKER_MODULE,
    "cli.automa_cli.hosts.runs.automation",
    "cli.automa_cli.hosts.automation",
)
