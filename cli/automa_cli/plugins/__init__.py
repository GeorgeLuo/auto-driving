"""``automa plugins``: the commands.

- ``commands``: the ``plugins`` parser and its handlers.
- ``streaming``: ``plugins stream`` for perception and memory, with ``decision_steps`` for
  proposal, plan and action and ``stream_output``; each feeds a ``hosts.views.view_feed`` view.
- ``step_inspect``: ``plugins inspect``, with ``step_replay`` replaying the source through the cycle.
- ``catalog``: ``plugins list``, ``upload``, ``arm``, ``disarm`` and ``save``.
  ``catalog.commands`` registers them, ``catalog.service`` performs them, and
  ``catalog.render`` prints them, through ``hosts.publications.plugin_catalog``.
"""
