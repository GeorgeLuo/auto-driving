"""``automa plugins``: the commands.

- ``commands``: the ``plugins`` parser and its handlers.
- ``streaming``: ``plugins stream`` for perception and memory, with ``decision_steps`` for
  proposal, plan and action and ``stream_output``; each feeds a ``hosts.viewer`` view.
- ``step_inspect``: ``plugins inspect``, with ``step_replay`` replaying the source through the cycle.
- ``catalog``: ``plugins list``, ``plugins upload``, ``plugins arm``,
  ``plugins disarm``, through ``hosts.plugin_catalog``.
"""
