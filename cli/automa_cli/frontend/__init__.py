"""The runtime viewer automa serves.

- ``server``: HTTP routes and the viewer process.
- ``loopback_http``: the loopback transport those routes share.
- ``decision_view``, ``perception_view``, and ``workbench_view``: one page's payloads.
- ``workbench_contract``: the workbench action schema. It is not terminal text.
- ``plugin_catalog``: the catalog route.
- ``pages``: the HTML, CSS, and JavaScript each route serves. Those assets keep
  the names the pages already request.

This package imports only itself, the standard library, PIL and ``autonomy``.
Automa feeds it host publications and passes in the host list and the workbench
replay runner (``automa_cli.hosts.views.runtime_view``), so the viewer serves wherever the
CLI runs.
"""
