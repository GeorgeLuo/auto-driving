"""``automa hosts``: the commands, and the host code other commands share.

- ``commands``: the ``hosts`` parser and its handlers.
- ``create``, ``update``, ``delete``: ``deploy``, with ``donkeycar_vendor`` and ``bundles``.
- ``run``, ``restart``: ``automation``; ``stop``: ``automation_stop``; ``status``:
  ``automation_status``; with ``runtime_monitor``, ``run_record`` and ``host_status``.
- ``info``, ``viewer``: ``perception``, ``memory``, ``decision_steps`` and
  ``streaming``, with ``stream_output``, ``decision_live`` and ``view_discovery``.
- ``memory``: ``memory`` for reset.
- Read by several commands: ``runtime_hosts``, which finds, starts and replaces the host
  behind each vehicle; ``step_activations``, ``step_schema``, ``step_hosting``,
  ``host_publications`` with ``decision_publication`` and ``host_telemetry``, which check
  what it reads; ``vehicle_access``, ``decision``, ``decision_records``,
  ``memory_report``, ``step_formatting`` and ``inspection_runs``.

Outside ``hosts``: ``automa`` serves ``host_status`` in its viewer,
``simulators`` and ``vehicles`` find hosts through ``runtime_hosts``, ``plugins stream`` runs
``streaming`` and ``decision_steps``, and ``plugins inspect`` reads ``inspection_runs``,
``memory_report`` and ``step_formatting``. ``workbench`` shares ``step_hosting``,
``inspection_runs`` and ``memory_report`` for replay execution and presentation.
"""
