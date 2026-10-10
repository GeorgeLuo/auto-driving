"""Shared upload, arming and status behind local and onboard transports."""

from __future__ import annotations

import base64
from typing import Any, Callable, Mapping

from autonomy.plugins import LocalPluginCatalog, PluginDefinition

PLUGIN_CATALOG_PATH = "/api/plugins"
ARMING_STEPS = ("perception", "memory", "proposal")


def _field(payload: Mapping[str, Any], name: str) -> Any:
    if name not in payload:
        raise ValueError(f"missing field: {name}")
    return payload[name]


def catalog_write_error(headers: Mapping[str, str], *, origin: str) -> tuple[int, dict[str, Any]] | None:
    """Allow JSON CLI writes and same-origin browser writes before reading a body."""

    supplied = headers.get("Origin")
    if (supplied is not None and supplied != origin
            or headers.get("Sec-Fetch-Site") == "cross-site"):
        return 403, {"ok": False, "status": "failed", "error": "catalog writes require the same origin"}
    if headers.get("Content-Type", "").partition(";")[0].strip().lower() != "application/json":
        return 415, {"ok": False, "status": "failed", "error": "catalog writes require application/json"}
    return None


def describe_plugin(definition: PluginDefinition) -> dict[str, Any]:
    return {
        "step": definition.step, "id": definition.plugin_id,
        "entrypoint": definition.entrypoint, "config": dict(definition.config),
        "metadata": dict(definition.metadata),
    }


class PluginCatalogAPI:
    def __init__(self, catalog: LocalPluginCatalog, *, arm: Callable | None = None,
                 selection_status: Callable | None = None, applied_decision: Callable | None = None) -> None:
        self.catalog = catalog
        self.arm = arm
        self.selection_status = selection_status
        self.applied_decision = applied_decision

    def __call__(self, payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """Acknowledge registration or requested selection; loading stays downstream."""

        try:
            if payload is not None:
                if payload.get("operation") == "arm":
                    if self.arm is None:
                        raise ValueError("arming requires a vehicle runtime host; this catalog supports upload only")
                    return self.arm(_field(payload, "selections"))
                definition = self.catalog.upload(
                    step=_field(payload, "step"), plugin_id=_field(payload, "plugin_id"),
                    entrypoint=_field(payload, "entrypoint"),
                    source=base64.b64decode(_field(payload, "source_base64"), validate=True),
                    filename=payload.get("filename", "plugin.py"), config=payload.get("config"),
                )
                return {"ok": True, "status": "uploaded", "plugin": describe_plugin(definition),
                        "catalog_version": self.catalog.version}
            version, definitions = self.catalog.snapshot()
            result = {
                "ok": True, "schema": "automa_plugin_catalog_v0",
                "catalog_version": version,
                "plugins": [describe_plugin(item) for item in definitions],
            }
            if self.selection_status is not None:
                result["arming"] = self.selection_status()
            if self.applied_decision is not None:
                result["applied_decision"] = self.applied_decision()
            return result
        except (OSError, ValueError, TypeError, KeyError) as exc:
            return {"ok": False, "status": "failed", "error": str(exc)}
