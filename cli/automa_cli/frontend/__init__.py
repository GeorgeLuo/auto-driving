"""The runtime viewer automa serves.

- ``server``: HTTP routes and the viewer process: the v2 page, its command routes and the workbench.
- ``loopback_http``: the loopback transport those routes share.
- ``decision_view`` and ``perception_view``: the host frame payloads the v2 page reads;
  ``workbench_view``: the workbench's.
- ``workbench_contract``: the workbench action schema. It is not terminal text.
- ``plugin_catalog``: the workbench's catalog route.
- ``pages``: the HTML, CSS, and JavaScript each route serves. Those assets keep
  the names the pages already request.

This package imports only itself, the standard library, PIL and ``autonomy``.
Automa feeds it host publications and passes in the host list and the workbench
replay runner (``automa_cli.hosts.views.runtime_view``), so the viewer serves wherever the
CLI runs.
"""
