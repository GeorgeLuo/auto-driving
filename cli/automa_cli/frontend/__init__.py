"""The runtime viewer automa serves: its pages under ``pages/`` and the server behind them.

This package imports only itself, the standard library, PIL and ``autonomy``.
Automa feeds it host publications and passes in the host list and the workbench
replay runner (``automa_cli.hosts.views.runtime_view``), so the viewer serves wherever the
CLI runs.
"""
