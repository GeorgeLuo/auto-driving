"""``automa plugins``: the commands.

- ``commands``: the ``plugins`` parser. It assembles ``catalog``, ``stream`` and ``inspect``.
- ``catalog``: ``plugins list``, ``upload``, ``arm``, ``disarm`` and ``save``.
  ``catalog.commands`` registers them, ``catalog.service`` performs them, and
  ``catalog.render`` prints them, through ``hosts.publications.plugin_catalog``.
- ``stream``: ``plugins stream``. ``stream.service`` dispatches the step,
  ``perception``, ``memory`` and ``decision`` interpret it, and ``stream.render``
  prints it. A running terminal stream feeds a ``hosts.views.view_feed`` view.
- ``inspect``: ``plugins inspect``. ``inspect.service`` replays the source,
  ``inspect.replay`` restores selections and records a run, and ``inspect.render``
  prints the report.
- ``step_formatting``: text shared by a live step stream and an inspection report.
"""
