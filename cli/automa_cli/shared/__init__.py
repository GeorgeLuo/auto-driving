"""Code every ``automa`` command shares, owned by none of them.

- ``command_result``: the exit code and message a command returns.
- ``paths``: the repository root and the path helpers built on it.
- ``timeout_input``: the ``--timeout-s`` check of the status and run commands.
- ``selection_args``: a step's ``--preset``/``--plugin`` selection, for ``workbench`` and ``plugins inspect``.
"""
