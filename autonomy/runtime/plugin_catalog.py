"""The same upload/list operation behind local and onboard catalog transports."""

from __future__ import annotations

import base64
from typing import Any, Mapping

from autonomy.plugins import LocalPluginCatalog, PluginDefinition

PLUGIN_CATALOG_PATH = "/api/plugins"


def describe_plugin(definition: PluginDefinition) -> dict[str, Any]:
    return {
        "step": definition.step, "id": definition.plugin_id,
        "entrypoint": definition.entrypoint, "config": dict(definition.config),
        "metadata": dict(definition.metadata),
    }


class PluginCatalogAPI:
    def __init__(self, catalog: LocalPluginCatalog) -> None:
        self.catalog = catalog

    def __call__(self, payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """Acknowledge registration, never executable compatibility or activation."""

        try:
            if payload is not None:
                definition = self.catalog.upload(
                    step=payload["step"], plugin_id=payload["plugin_id"],
                    entrypoint=payload["entrypoint"],
                    source=base64.b64decode(payload["source_base64"], validate=True),
                    filename=payload.get("filename", "plugin.py"), config=payload.get("config"),
                )
                return {"ok": True, "status": "uploaded", "plugin": describe_plugin(definition),
                        "catalog_version": self.catalog.version}
            version, definitions = self.catalog.snapshot()
            return {
                "ok": True, "schema": "automa_plugin_catalog_v0",
                "catalog_version": version,
                "plugins": [describe_plugin(item) for item in definitions],
            }
        except (OSError, ValueError, TypeError, KeyError) as exc:
            return {"ok": False, "status": "failed", "error": str(exc)}
