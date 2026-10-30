"""A host's plugin catalog, read and written over HTTP at ``PLUGIN_CATALOG_PATH``."""

from __future__ import annotations

from typing import Any

from autonomy.runtime.plugin_catalog import PLUGIN_CATALOG_PATH

from ..shared.io import http_json


class PluginCatalogClient:
    def __init__(self, url: str, *, timeout_s: float = 5.0) -> None:
        self.url = url.rstrip("/")
        if not self.url.endswith(PLUGIN_CATALOG_PATH):
            self.url += PLUGIN_CATALOG_PATH
        self.timeout_s = timeout_s

    def __call__(self, payload: dict[str, Any] | None = None, *, timeout_s: float | None = None) -> dict[str, Any]:
        """GET the catalog, or POST ``payload`` to it; a refusal's JSON body is the answer."""
        timeout_s = self.timeout_s if timeout_s is None else timeout_s
        return http_json.request_json(self.url, timeout_s=timeout_s, payload=payload)[1]
