"""Shared upload, arming, disarming and status behind local and onboard transports."""

from __future__ import annotations

import base64
from typing import Any, Callable, Mapping

from autonomy.decision_cycle.steps import CLEARABLE_STEPS
from autonomy.plugins import LocalPluginCatalog, PluginDefinition
from autonomy.runtime.http_errors import error_payload

PLUGIN_CATALOG_PATH = "/api/plugins"


def _field(payload: Mapping[str, Any], name: str) -> Any:
    if name not in payload:
        raise ValueError(f"missing field: {name}")
    return payload[name]


def describe_plugin(definition: PluginDefinition) -> dict[str, Any]:
    return {
        "step": definition.step, "id": definition.plugin_id,
        "entrypoint": definition.entrypoint, "config": dict(definition.config),
        "metadata": dict(definition.metadata),
    }


class PluginCatalogAPI:
    def __init__(self, catalog: LocalPluginCatalog, *, arm: Callable | None = None, disarm: Callable | None = None,
                 selection_status: Callable | None = None, applied_decision: Callable | None = None) -> None:
        self.catalog = catalog
        self.arm = arm
        self.disarm = disarm
        self.selection_status = selection_status
        self.applied_decision = applied_decision

    def __call__(self, payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """Acknowledge registration or requested selection; loading stays downstream."""

        try:
            if payload is not None:
                if payload.get("operation") == "upload_collection":
                    name = _field(payload, "name")
                    definitions = self.catalog.upload_collection(
                        name, _field(payload, "plugins"), _field(payload, "sources"))
                    return {"ok": True, "status": "uploaded", "collection": name,
                            "plugins": [describe_plugin(item) for item in definitions],
                            "catalog_version": self.catalog.version}
                if payload.get("operation") == "arm_collection":
                    if self.arm is None:
                        raise ValueError("arming requires a vehicle runtime host; this catalog supports upload only")
                    name = _field(payload, "name")
                    result = self.arm(self.catalog.collection_selections(name))
                    return {**result, "collection": name}
                if payload.get("operation") == "disarm_collection":
                    if self.disarm is None:
                        raise ValueError("disarming requires a vehicle runtime host; this catalog supports upload only")
                    name = _field(payload, "name")
                    # A one-plugin step keeps its plugin until another is armed; emptying it refuses the request.
                    selections = {step: ids for step, ids in self.catalog.collection_selections(name).items()
                                  if step in CLEARABLE_STEPS}
                    if not selections:
                        raise ValueError(f"plugin collection {name!r} only arms one-plugin steps, which keep "
                                         "their plugin; arm a replacement instead")
                    return {**self.disarm(selections), "collection": name}
                if payload.get("operation") == "arm":
                    if self.arm is None:
                        raise ValueError("arming requires a vehicle runtime host; this catalog supports upload only")
                    return self.arm(_field(payload, "selections"))
                if payload.get("operation") == "disarm":
                    if self.disarm is None:
                        raise ValueError("disarming requires a vehicle runtime host; this catalog supports upload only")
                    return self.disarm(_field(payload, "selections"), all_plugins=payload.get("all", False))
                definition = self.catalog.upload(
                    step=_field(payload, "step"), plugin_id=_field(payload, "plugin_id"),
                    entrypoint=_field(payload, "entrypoint"),
                    source=base64.b64decode(_field(payload, "source_base64"), validate=True),
                    filename=payload.get("filename", "plugin.py"), config=payload.get("config"),
                )
                return {"ok": True, "status": "uploaded", "plugin": describe_plugin(definition),
                        "catalog_version": self.catalog.version}
            version, definitions = self.catalog.read()
            result = {
                "ok": True, "schema": "automa_plugin_catalog",
                "catalog_version": version,
                "plugins": [describe_plugin(item) for item in definitions],
                "collections": self.catalog.collections(),
            }
            if self.selection_status is not None:
                result["arming"] = self.selection_status()
            if self.applied_decision is not None:
                result["applied_decision"] = self.applied_decision()
            return result
        except (OSError, ValueError, TypeError, KeyError) as exc:
            return error_payload(str(exc), status="failed")
