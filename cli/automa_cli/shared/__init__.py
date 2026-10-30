"""Code every ``automa`` command shares, owned by none of them.

- ``text``: shared command output formatting.
- ``command_result``: the exit code and message a command returns.
- ``paths``: the repository and runtime roots and the path helpers built on them.
- ``controller_releases``: where a host's controller bundle lives, the releases ``hosts update`` packages into it, and ``RuntimeHostError``.
- ``io.json_files``: the JSON files written beside a vehicle's runtime and read back.
- ``io.processes``: whether a process the CLI started is still alive.
- ``timestamps``: the wall-clock milliseconds on run records and replay state.
- ``open_view``: a runtime view's page opened in a browser tab, for ``hosts run``,
  ``hosts viewer`` and ``workbench --open``.
- ``io.http_json``: one JSON request to a host or catalog route, for status, publication, probe and catalog reads.
- ``timeout_input``: the ``--timeout-s`` check of the status and run commands.
- ``selection_args``: a step's ``--preset``/``--plugin`` selection, for ``workbench`` and ``plugins inspect``.
- ``plugin_catalog``: the packaged plugin definitions, defaults first, for ``plugins list``
  and ``workbench``; ``hosts.plugin_catalog`` reads a host's live catalog.
- ``replay``: image sources, frame execution and selections rebuilt from recorded releases.
- ``step_hosting``: a hosted step runner's plugin report, for the worker and ``workbench``.
- ``memory_report``: memory plugin state read from a memory report or memory step status.
"""
