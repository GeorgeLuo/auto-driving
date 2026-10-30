"""``automa hosts``: the commands, and the host code other commands share.

- ``commands``: the ``hosts`` parser and its handlers.
- ``create``, ``update``, ``delete``: ``deploy``, with ``picar_deploy`` for a PiCar,
  ``donkeycar_vendor`` and ``bundles``.
- ``run``, ``restart``: ``automation``; ``stop``: ``automation_stop``; ``status``:
  ``automation_status``; with ``runtime_monitor``, ``run_record`` and ``host_status``.
- ``viewer``: ``viewer``, with ``decision_live`` and ``view_discovery``.
- ``runtime_view``: every viewer automa serves, with the host list and the workbench
  replay runner; ``automa`` with no command serves one with no host.
- Read by several commands: ``runtime_hosts``, which finds, starts and replaces the host
  behind each vehicle; ``step_activations``; ``host_publications`` with
  ``decision_publication`` and ``host_telemetry``, which check what it reads;
  ``plugin_catalog``, the client of its plugin catalog; ``vehicle_access``, ``decision``
  and ``decision_records``.

Outside ``hosts``: ``automa`` hands ``workbench`` the ``runtime_view`` builder,
``simulators`` finds hosts through ``runtime_hosts``, and ``plugins stream``
feeds a ``viewer`` view.
"""
