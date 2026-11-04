"""Code every ``automa`` command shares, owned by none of them.

Generic helpers:

- ``command_result``: the exit code and message an operation returns.
- ``paths``: the repository and runtime roots, and path sanitization. A host's
  controller bundle lives in ``releases.paths``.
- ``text``: indented command text.
- ``timestamps``: wall-clock milliseconds on run records and replay state.
- ``io.json_files``: JSON files written beside a runtime and read back.
- ``io.processes``: whether a process the CLI started is still alive.
- ``io.http_json``: one JSON request, for status, publication, probe, and catalog reads.

CLI adapters, in ``cli``:

- ``cli.selection_args``: a step's ``--preset``/``--plugin`` selection.
- ``cli.timeout_input``: the ``--timeout-s`` check of the status and run commands.
- ``open_view``: open a runtime view in a browser. Services call this while a
  run or replay is still going, so it stays beside the helpers rather than in
  ``cli``.

Shared readers that more than one capability uses:

- ``plugin_catalog``: packaged plugin definitions, defaults first.
- ``step_hosting``: a hosted step runner's plugin report.
- ``memory_report``: memory plugin state read from a memory report or step status.

``cli`` does not import a feature package.
"""
