"""What the host publishes from the action step for later cycles.

Before each cycle, a host with control execution writes its recent
``ControlApplication`` records at ``CONTROL_APPLICATIONS_KEY``: a tuple, oldest
first, of at most ``CONTROL_APPLICATION_LIMIT`` target writes, each with the
mode it ran in. ``()`` means nothing has been applied since the run started or
memory was reset. The key is absent when the host has no execution or a replay
source recorded none, so readers treat the car's motion as unknown.

Manual and observe-only applications do not describe the operator's motion.
What ``delivered_at_ms`` marks on each vehicle, and why it can step backwards,
is described in ``autonomy/runtime/execution.py``.
"""

CONTROL_APPLICATIONS_KEY = "action.control_applications"
