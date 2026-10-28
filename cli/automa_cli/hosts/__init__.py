"""``automa hosts``: the commands, and the host code other commands share.

- ``commands``: the ``hosts`` parser and its handlers.
- ``create``, ``update``, ``delete``: ``deploy``, with ``donkeycar_vendor`` and ``bundles``.
- ``run``, ``stop``, ``restart``, ``status``: ``automation``, with ``runtime_monitor``,
  ``run_record``, ``staged_bundle`` and ``host_status``.
- ``serve``: ``runtime_hosts``, which finds, starts and replaces the host behind each vehicle.
- ``stage``: ``step_activations``, ``step_schema`` and ``step_hosting``.
- ``info``, ``stream``, ``viewer``: ``perception``, ``memory``, ``decision_steps`` and
  ``streaming``, with ``stream_output``, ``decision_live`` and ``view_discovery``.
- ``memory``, ``perception``: ``viability`` for the viability measurements, ``memory`` for reset.
- ``plugins``: ``plugin_catalog``.
- ``operation``: ``operations``.
- ``workbench``: ``workbench`` and the ``workbench_*`` modules.
- Read by several commands: ``host_publications``, ``vehicle_access``, ``decision``,
  ``decision_records``, ``memory_report``, ``step_formatting`` and ``inspection_runs``.

Outside ``hosts``: ``automa`` serves ``host_status`` and ``workbench_runner`` in its viewer,
``simulators`` and ``vehicles`` find hosts through ``runtime_hosts``, and ``plugins inspect``
reads ``inspection_runs``, ``memory_report``, ``step_formatting``, ``workbench_frames`` and
``workbench_source``.
"""
