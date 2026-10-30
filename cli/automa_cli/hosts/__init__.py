"""``automa hosts``: the commands, and the host code other commands share.

- ``commands``: the ``hosts`` parser and its handlers.
- ``create``, ``update``, ``delete``: ``deploy``, with ``donkeycar_vendor`` and ``bundles``.
- ``run``, ``restart``: ``automation``; ``stop``: ``automation_stop``; ``status``:
  ``automation_status``; with ``runtime_monitor``, ``run_record`` and ``host_status``.
- ``viewer``: ``viewer``, with ``decision_live`` and ``view_discovery``.
- Read by several commands: ``runtime_hosts``, which finds, starts and replaces the host
  behind each vehicle; ``step_activations``,
  ``host_publications`` with ``decision_publication`` and ``host_telemetry``, which check
  what it reads; ``vehicle_access``, ``decision`` and ``decision_records``.

Outside ``hosts``: ``automa`` serves ``host_status`` in its viewer,
``simulators`` and ``vehicles`` find hosts through ``runtime_hosts``, and ``plugins stream``
feeds a ``viewer`` view.
"""
