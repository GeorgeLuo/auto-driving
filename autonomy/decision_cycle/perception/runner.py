"""Perception step runner.

``PerceptionRunner`` resolves the feed inputs each selected perception
plugin's contract declares, runs the plugins in selection order on one sensor
frame, and merges their evidence into one ``PerceptionText``. It is a
``StepRunner``: ``validate_selection`` resolves a changed selection's feed
providers, and commit publishes them with the plugins.
"""

from __future__ import annotations

import importlib
import time
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, ClassVar

from autonomy.decision_cycle.activation import step_activation
from autonomy.decision_cycle.perception.inputs import build_perception_request
from autonomy.decision_cycle.runner import (
    StepRunner,
    describe_configuration,
    describe_exception,
    describe_plugin,
)
from autonomy.plugins import PluginDefinition, PluginManager
from autonomy.decision_cycle.perception.feeds.context import PerceptionRequest
from autonomy.decision_cycle.perception.feeds.interface import FeedProvider
from autonomy.decision_cycle.perception.diagnostics.sink import PerceptionDiagnosticSink
from autonomy.decision_cycle.perception.evidence.rendering import signal_line, thing_line
from autonomy.decision_cycle.perception.evidence.values import (
    PerceivedThing,
    PerceptionEvidenceBatch,
    PerceptionSignal,
)
from autonomy.decision_cycle.perception.interface import (
    FAILURE_POLICY,
    PERCEPTION_SCHEMA,
    PERCEPTION_TEXT_SCHEMA,
    PerceptionPluginRun,
    PerceptionText,
    PluginResultStatus,
    composition_declaration,
)
from autonomy.decision_cycle.perception.plugin import (
    PerceptionPluginContract,
    PerceptionPluginInputs,
    PerceptionPluginWarmingUp,
)


# ``context.metadata`` key naming the directory a recording host wants
# perception diagnostics written to for the frame.
PERCEPTION_OUTPUT_DIR_KEY = "perception_output_dir"


@dataclass(frozen=True)
class _PluginExecution:
    status: PluginResultStatus
    batch: PerceptionEvidenceBatch
    artifacts: dict[str, str]
    duration_ms: float
    error: str | None = None


class PerceptionRunner(StepRunner[Any]):
    """Run the manager's selected perception plugins once per sensor frame.

    The cycle calls the runner with the frame context. ``perceive`` runs the
    selection on an already built ``PerceptionRequest`` for offline tools.
    A plugin's own errors are isolated into its run; ``run_count``,
    ``failure_count`` and ``last_error`` count frames and the errors that
    stopped one.
    """

    step: ClassVar[str] = "perception"
    plugin_id: ClassVar[str] = "autonomy.perception.plugin-runner-v0"

    def __init__(
        self,
        plugin_manager: PluginManager,
        *,
        provided: Mapping[str, Any] | None = None,
    ) -> None:
        self.last_output: PerceptionText | None = None
        self.last_duration_ms: float | None = None
        self.last_frame_index: int | None = None
        self._feed_providers: dict[str, FeedProvider] = {}
        # Providers a changed selection resolves while it validates, held with
        # the prepared plugins until commit publishes both.
        self._validated_providers: dict[str, FeedProvider] | None = None
        self._pending_providers: dict[str, FeedProvider] | None = None
        self._execution_runs: tuple[PerceptionPluginRun, ...] = ()
        super().__init__(plugin_manager, provided=provided)

    @classmethod
    def from_selection(
        cls,
        plugins: list[str] | tuple[str, ...],
        plugin_specs: dict[str, str],
        plugin_configs: dict[str, dict[str, Any]] | None = None,
    ) -> "PerceptionRunner":
        return cls.from_activation(
            step_activation(cls.step, plugins, plugin_specs, plugin_configs)
        )

    def __call__(self, context) -> PerceptionText | None:
        """Perceive the context's sensor frame; without one, reset and return None."""

        with self._runtime_lock:
            if context.sensor_frame is None:
                self.reset(context.shared_memory)
                self.last_frame_index = context.frame_index
                return None
            started = time.perf_counter()
            metadata = context.metadata if isinstance(context.metadata, dict) else {}
            # A host that records diagnostics names the directory per frame.
            output_dir = metadata.get(PERCEPTION_OUTPUT_DIR_KEY)
            try:
                self.last_output = self.perceive(
                    build_perception_request(
                        context.sensor_frame,
                        shared_memory=context.shared_memory,
                        output_dir=Path(output_dir) if isinstance(output_dir, str) else None,
                        metadata={
                            "runtime": "onboard",
                            "activation": (
                                str(self.activation.source_path)
                                if self.activation and self.activation.source_path
                                else None
                            ),
                            "frame_index": context.frame_index,
                        },
                    )
                )
            finally:
                self.last_duration_ms = round((time.perf_counter() - started) * 1000.0, 3)
                self.last_frame_index = context.frame_index
            return self.last_output

    def status(self) -> dict[str, Any]:
        with self._runtime_lock:
            output = self.last_output
            return {
                **super().status(),
                "last_status": output.status if output is not None else None,
                "last_duration_ms": self.last_duration_ms,
                "last_frame_index": self.last_frame_index,
                "last_thing_count": len(output.things) if output is not None else 0,
                "last_plugin_runs": (
                    [plugin_run.to_dict() for plugin_run in output.plugin_runs]
                    if output is not None
                    else []
                ),
            }

    @property
    def plugin_specs(self) -> dict[str, str]:
        return {item.plugin_id: item.entrypoint for item in self.plugin_manager.available}

    @property
    def plugin_configs(self) -> dict[str, dict[str, Any]]:
        return {
            item.plugin_id: deepcopy(dict(item.config))
            for item in self.plugin_manager.available
        }

    def reset(self, shared_memory=None) -> None:
        with self._runtime_lock:
            self._execution_runs = ()
            self.last_output = None
            self.last_duration_ms = None
            self.last_frame_index = None
            super().reset(shared_memory)

    def describe_schema(self) -> dict[str, Any]:
        with self._runtime_lock:
            return self._describe_schema()

    def _plugin_records(self) -> list[dict[str, Any]]:
        """Timing and error from the last published execution.

        They stay null before the first run and after reset. Domain run fields
        stay on the perception result, not in the plugin report.
        """

        runs = {run.plugin_id: run for run in self._execution_runs}
        records = []
        for definition, _plugin in self.applied:
            run = runs.get(definition.plugin_id)
            duration_ms = None
            error = None
            if run is not None:
                duration_ms = run.duration_ms
                error = run.error
            records.append(
                {
                    "plugin_id": definition.plugin_id,
                    "duration_ms": duration_ms,
                    "error": error,
                }
            )
        return records

    def _describe_schema(self) -> dict[str, Any]:
        feed_consumers: dict[str, list[str]] = {}
        feed_providers: dict[str, str] = {}
        plugin_schemas = []
        applied = self.applied
        for definition, plugin in applied:
            contract = plugin.contract
            for item in contract.inputs:
                feed_consumers.setdefault(item.feed_id, []).append(
                    definition.plugin_id
                )
                feed_providers[item.feed_id] = item.provider_spec
            plugin_schemas.append(
                {**describe_plugin(definition), "contract": contract.to_dict()}
            )
        return {
            "schema": PERCEPTION_SCHEMA,
            "plugin_id": self.plugin_id,
            "runner": f"{type(self).__module__}:{type(self).__name__}",
            "configuration": describe_configuration(self.plugin_manager, applied),
            "inputs": [
                {
                    "feed_id": feed_id,
                    "provider_spec": feed_providers[feed_id],
                    "required": True,
                    "required_by": plugin_ids,
                    "source": "resolved once by the framework and injected by plugin-local name",
                    "missing_behavior": (
                        "framework skips perceive and resets stateful plugins; "
                        "the plugin is unavailable unless its reset fails, "
                        "which is isolated as that plugin's frame error"
                    ),
                }
                for feed_id, plugin_ids in sorted(feed_consumers.items())
            ],
            "plugins": plugin_schemas,
            "output": {
                "schema": PERCEPTION_TEXT_SCHEMA,
                "format": "structured signals and spatial evidence with a derived text view",
                "records": [
                    {
                        "record": "signals[]",
                        "meaning": "structured boolean or scalar evidence signals",
                    },
                    {
                        "record": "things[]",
                        "meaning": "structured spatial evidence with source-plugin provenance",
                    },
                    {
                        "record": "plugin_runs[]",
                        "meaning": "framework-derived status, timing, counts, and errors",
                    },
                ],
                "limits": [
                    "plugin outputs are current evidence, not durable world facts",
                    "confidence remains local to each evidence record",
                    "no calibrated metric geometry unless a plugin states otherwise",
                ],
            },
            "composition": composition_declaration(),
            "failure_policy": FAILURE_POLICY.to_dict(),
        }

    def perceive(self, request: PerceptionRequest) -> PerceptionText:
        with self._runtime_lock:
            # Core snapshots the manager's selection once for this sensor frame.
            self.apply_selection(request.shared_memory)
            self.run_count += 1
            try:
                output = self._perceive_selected(request)
            except Exception as exc:
                self.failure_count += 1
                self.last_error = describe_exception(exc)
                raise
            self.last_error = None
            return output

    def _perceive_selected(self, request: PerceptionRequest) -> PerceptionText:
        lines = [
            f"schema={PERCEPTION_TEXT_SCHEMA}",
            f"plugin={self.plugin_id}",
            f"plugins={','.join(self.plugin_ids)}",
        ]
        signals: list[PerceptionSignal] = []
        things: list[PerceivedThing] = []
        measurements: dict[str, dict[str, Any]] = {}
        artifacts: dict[str, str] = {}
        limits: list[str] = []
        plugin_runs: list[PerceptionPluginRun] = []

        # Catalog plugin_id attributes results. The instance plugin_id is the
        # implementation identity and is not rewritten to repair provenance.
        for definition, plugin in self.applied:
            catalog_id = definition.plugin_id
            execution = self._execute_plugin(plugin, request, catalog_id)
            attributed_signals = tuple(
                replace(signal, source_plugin_id=catalog_id)
                for signal in execution.batch.signals
            )
            attributed_things = tuple(
                replace(thing, source_plugin_id=catalog_id)
                for thing in execution.batch.things
            )
            plugin_runs.append(
                PerceptionPluginRun(
                    plugin_id=catalog_id,
                    status=execution.status,
                    duration_ms=execution.duration_ms,
                    signal_count=len(attributed_signals),
                    thing_count=len(attributed_things),
                    artifact_count=len(execution.artifacts),
                    error=execution.error,
                )
            )
            lines.append(
                f"plugin_run id={catalog_id} status={execution.status} "
                f"duration_ms={execution.duration_ms:.3f} "
                f"signals={len(attributed_signals)} things={len(attributed_things)} "
                f"artifacts={len(execution.artifacts)}"
            )
            if execution.error:
                lines.append(
                    f"plugin_status id={catalog_id} status={execution.status} "
                    f"detail={_line_value(execution.error)}"
                )
            lines.extend(signal_line(signal) for signal in attributed_signals)
            lines.extend(thing_line(thing) for thing in attributed_things)
            signals.extend(attributed_signals)
            things.extend(attributed_things)
            if execution.batch.measurements:
                measurements[catalog_id] = dict(execution.batch.measurements)
            artifacts.update(
                {
                    f"{catalog_id}/{artifact_id}": path
                    for artifact_id, path in execution.artifacts.items()
                }
            )
            limits.extend(plugin.contract.limitations)

        self._execution_runs = tuple(plugin_runs)
        return PerceptionText(
            schema=PERCEPTION_TEXT_SCHEMA,
            plugin_id=self.plugin_id,
            status=_overall_status(plugin_runs),
            lines=tuple(lines),
            signals=tuple(signals),
            things=tuple(things),
            plugin_runs=tuple(plugin_runs),
            measurements=measurements,
            artifacts=artifacts,
            limits=tuple(dict.fromkeys(limits)),
        )

    # Selection hooks ------------------------------------------------------

    def validate_plugin(self, plugin: Any, definition: PluginDefinition) -> None:
        if not isinstance(getattr(plugin, "contract", None), PerceptionPluginContract):
            raise TypeError(
                f"plugin {definition.plugin_id!r} must expose PerceptionPluginContract as contract"
            )
        if not callable(getattr(plugin, "perceive", None)):
            raise TypeError(f"plugin {definition.plugin_id!r} must implement perceive()")

    def validate_selection(self, plugins: tuple[Any, ...]) -> None:
        """Resolve the feed provider each plugin declares; a feed has one provider."""

        super().validate_selection(plugins)
        provider_specs: dict[str, str] = {}
        providers: dict[str, FeedProvider] = {}
        for plugin in plugins:
            for item in plugin.contract.inputs:
                spec = item.provider_spec
                if spec not in providers:
                    provider = self._feed_providers.get(spec)
                    if provider is None:
                        provider = _load_symbol(spec)
                    if not callable(provider):
                        raise TypeError(f"feed provider {spec!r} is not callable")
                    providers[spec] = provider
                # Specs that name the same provider (a legacy and a canonical
                # path) agree.
                existing = provider_specs.get(item.feed_id)
                if existing is None:
                    provider_specs[item.feed_id] = spec
                elif providers[existing] is not providers[spec]:
                    raise ValueError(
                        f"feed {item.feed_id!r} declares conflicting providers: "
                        f"{existing!r} and {spec!r}"
                    )
        self._validated_providers = providers

    def _prepare_selection(self) -> None:
        self._validated_providers = None
        super()._prepare_selection()
        # An unchanged selection is not validated, so commit keeps the
        # published providers.
        self._pending_providers = self._validated_providers

    def _commit_selection(self, shared_memory=None) -> None:
        providers, self._pending_providers = self._pending_providers, None
        super()._commit_selection(shared_memory)
        if providers is not None:
            self._feed_providers = providers

    def _discard_selection(self) -> None:
        super()._discard_selection()
        self._pending_providers = None

    def _reset_plugin(self, plugin: Any, shared_memory=None) -> None:
        reset = getattr(plugin, "reset", None)
        if not callable(reset):
            return
        try:
            if plugin.contract.memory_required:
                reset(shared_memory)
            else:
                reset()
        except Exception:
            if FAILURE_POLICY.reset == "propagate":
                raise

    def _execute_plugin(
        self,
        plugin: Any,
        request: PerceptionRequest,
        catalog_id: str,
    ) -> _PluginExecution:
        started = time.perf_counter()
        diagnostics = PerceptionDiagnosticSink(
            output_dir=request.output_dir,
            plugin_id=catalog_id,
            allowed_artifacts=plugin.contract.diagnostic_artifacts,
        )
        if plugin.contract.diagnostics_required and not diagnostics.enabled:
            return _execution(
                started,
                status="unavailable",
                error="diagnostics are required but recording is disabled",
            )

        try:
            feeds, missing = self._resolve_inputs(plugin.contract, request)
            if missing and FAILURE_POLICY.missing_input == "skip_plugin":
                if plugin.contract.state_mode != "stateless":
                    self._reset_plugin(plugin, request.shared_memory)
                details = "; ".join(
                    f"{name}: {reason}" for name, reason in sorted(missing.items())
                )
                return _execution(
                    started,
                    status="unavailable",
                    error=f"required input unavailable: {details}",
                )
            inputs = PerceptionPluginInputs(
                frame_id=request.sensor_frame.read_id,
                captured_at_ms=request.sensor_frame.completed_at_ms,
                feeds=feeds,
                diagnostics=diagnostics,
                metadata=request.metadata,
                shared_memory=request.shared_memory,
            )
            batch = plugin.perceive(inputs)
            if not isinstance(batch, PerceptionEvidenceBatch):
                raise TypeError(
                    f"plugin {catalog_id!r} must return PerceptionEvidenceBatch"
                )
            status: PluginResultStatus = (
                "ok"
                if batch.signals or batch.things or diagnostics.artifacts
                else "empty"
            )
            return _execution(
                started,
                status=status,
                batch=batch,
                artifacts=diagnostics.artifacts,
            )
        except PerceptionPluginWarmingUp as exc:
            return _execution(
                started,
                status="warming_up",
                batch=PerceptionEvidenceBatch(measurements=exc.measurements),
                artifacts=diagnostics.artifacts,
                error=exc.reason,
            )
        except Exception as exc:
            if FAILURE_POLICY.update != "isolate_plugin":
                raise
            return _execution(
                started,
                status="error",
                artifacts=diagnostics.artifacts,
                error=f"{type(exc).__name__}: {exc}",
            )

    def _resolve_inputs(
        self,
        contract: PerceptionPluginContract,
        request: PerceptionRequest,
    ) -> tuple[dict[str, Any], dict[str, str]]:
        feeds: dict[str, Any] = {}
        missing: dict[str, str] = {}
        for item in contract.inputs:
            provider = self._feed_provider(item.provider_spec)
            feed = request.resolve_feed(
                item.feed_id,
                lambda provider=provider, item=item: provider(request, item),
            )
            if feed is None:
                missing[item.name] = (
                    request.feed_error(item.feed_id)
                    or "feed provider returned no value"
                )
            else:
                feeds[item.name] = feed
        return feeds, missing

    def _feed_provider(self, spec: str) -> FeedProvider:
        provider = self._feed_providers.get(spec)
        if provider is not None:
            return provider
        provider = _load_symbol(spec)
        if not callable(provider):
            raise TypeError(f"feed provider {spec!r} is not callable")
        self._feed_providers[spec] = provider
        return provider


def _execution(
    started: float,
    *,
    status: PluginResultStatus,
    batch: PerceptionEvidenceBatch | None = None,
    artifacts: dict[str, str] | None = None,
    error: str | None = None,
) -> _PluginExecution:
    return _PluginExecution(
        status=status,
        batch=batch or PerceptionEvidenceBatch(),
        artifacts=dict(artifacts or {}),
        duration_ms=round((time.perf_counter() - started) * 1000.0, 3),
        error=error,
    )


def _overall_status(plugin_runs: list[PerceptionPluginRun]) -> str:
    if not plugin_runs:
        return "empty"
    statuses = {run.status for run in plugin_runs}
    usable_statuses = {"ok", "empty", "warming_up"}
    if not statuses.intersection(usable_statuses):
        return "error" if "error" in statuses else "unavailable"
    if statuses.intersection({"error", "unavailable"}):
        return "partial"
    if "ok" in statuses:
        return "ok"
    if "warming_up" in statuses:
        return "warming_up"
    return "empty"


def _load_symbol(spec: str) -> Any:
    module_name, separator, name = spec.partition(":")
    if not separator or not module_name or not name:
        raise ValueError(f"import spec must be 'module.path:name', got {spec!r}")
    module = importlib.import_module(module_name)
    return getattr(module, name)


def _line_value(value: str) -> str:
    return value.replace("\n", " ").replace(" ", "_")
