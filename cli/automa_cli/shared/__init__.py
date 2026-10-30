"""Code every ``automa`` command shares, owned by none of them.

- ``command_result``: the exit code and message a command returns.
- ``paths``: the repository and runtime roots and the path helpers built on them.
- ``controller_releases``: where a host's controller bundle lives, the releases ``hosts update`` packages into it, and ``RuntimeHostError``.
- ``json_files``: the JSON files written beside a vehicle's runtime and read back.
- ``processes``: whether a process the CLI started is still alive.
- ``timestamps``: the wall-clock milliseconds on run records and replay state.
- ``open_view``: a runtime view's page opened in a browser tab, for ``hosts run``,
  ``hosts viewer`` and ``workbench --open``.
- ``http_json``: one JSON request to a host or catalog route, for status, publication, probe and catalog reads.
- ``publication_fields``: the field checks the host publication readers share.
- ``timeout_input``: the ``--timeout-s`` check of the status and run commands.
- ``selection_args``: a step's ``--preset``/``--plugin`` selection, for ``workbench`` and ``plugins inspect``.
- ``inspection_runs``: the selection a vehicle ran, rebuilt from its release, for ``workbench``,
  ``plugins inspect`` and the host's decision records.
- ``step_hosting``: a hosted step runner's plugin report, for the worker and ``workbench``.
- ``memory_report``: memory plugin state read from a memory report or memory step status.
- ``step_formatting``: step records and cycle timing as text, for ``plugins inspect`` and live streams.
"""
