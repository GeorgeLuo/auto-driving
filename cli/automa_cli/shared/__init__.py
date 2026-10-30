"""Code every ``automa`` command shares, owned by none of them.

- ``command_result``: the exit code and message a command returns.
- ``paths``: the repository and runtime roots and the path helpers built on them.
- ``json_files``: the JSON files written beside a vehicle's runtime and read back.
- ``processes``: whether a process the CLI started is still alive.
- ``timestamps``: the wall-clock milliseconds on run records and replay state.
- ``http_json``: one JSON request to a host or catalog route, for status, publication, probe and catalog reads.
- ``publication_fields``: the field checks the host publication readers share.
- ``timeout_input``: the ``--timeout-s`` check of the status and run commands.
- ``selection_args``: a step's ``--preset``/``--plugin`` selection, for ``workbench`` and ``plugins inspect``.
"""
