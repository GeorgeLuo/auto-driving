"""Perception step runner.

``PerceptionRunner`` resolves the feed inputs each selected perception
plugin's contract declares, runs the plugins in selection order on one sensor
frame, and merges their evidence into one ``PerceptionText``. It is a
``StepRunner``: ``validate_selection`` resolves a changed selection's feed
providers, and commit publishes them with the plugins.
"""

from __future__ import annotations

import importlib
from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass, replace
from typing import Any, ClassVar

from autonomy.decision_cycle.activation import StepActivation, step_activation
from autonomy.decision_cycle.perception.inputs import build_perception_request
from autonomy.decision_cycle.runner import (
    StepRunner,
    describe_exception,
    reset_plugin,
)
from autonomy.decision_cycle.timing import clock, elapsed_ms
from autonomy.plugins import PluginDefinition
from autonomy.decision_cycle.perception.feeds.context import PerceptionRequest
from autonomy.decision_cycle.perception.feeds.interface import FeedProvider
from autonomy.decision_cycle.perception.evidence.values import (
    PerceivedThing,
    PerceptionEvidenceBatch,
    PerceptionSignal,
)
from autonomy.decision_cycle.perception.interface import (
    FAILURE_POLICY,
    PERCEPTION_TEXT_SCHEMA,
    PerceptionPluginRun,
    PerceptionText,
    PluginResultStatus,
)
from autonomy.decision_cycle.perception.plugin import (
    PerceptionPluginContract,
    PerceptionPluginInputs,
    PerceptionPluginWarmingUp,
)


@dataclass(frozen=True)
class _PluginExecution:
    status: PluginResultStatus
    batch: PerceptionEvidenceBatch
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
        definitions: Sequence[PluginDefinition],
        *,
        activation: StepActivation | None = None,
        provided: Mapping[str, Any] | None = None,
    ) -> None:
        self.last_output: PerceptionText | None = None
        self.last_frame_index: int | None = None
        self._feed_providers: dict[str, FeedProvider] = {}
        super().__init__(definitions, activation=activation, provided=provided)

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
            try:
                self.last_output = self.perceive(
                    build_perception_request(
                        context.sensor_frame,
                        shared_memory=context.shared_memory,
                        metadata={
                            **context.metadata,
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
                self.last_frame_index = context.frame_index
            return self.last_output

    def status(self) -> dict[str, Any]:
        with self._runtime_lock:
            output = self.last_output
            return {
                **super().status(),
                "last_status": output.status if output is not None else None,
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
        if self.activation is not None:
            return dict(self.activation.plugin_specs)
        return {definition.plugin_id: definition.entrypoint for definition, _plugin in self.applied}

    @property
    def plugin_configs(self) -> dict[str, dict[str, Any]]:
        if self.activation is not None:
            return {key: deepcopy(dict(value)) for key, value in self.activation.plugin_configs.items()}
        return {
            definition.plugin_id: deepcopy(dict(definition.config))
            for definition, _plugin in self.applied
            if definition.config
        }

    def reset(self, shared_memory=None) -> None:
        with self._runtime_lock:
            self.last_output = None
            self.last_frame_index = None
            super().reset(shared_memory)

    def perceive(self, request: PerceptionRequest) -> PerceptionText:
        with self._runtime_lock:
            self.run_count += 1
            self._clear_plugin_records()
            try:
                output = self._perceive_selected(request)
            except Exception as exc:
                self.failure_count += 1
                self.last_error = describe_exception(exc)
                raise
            self.last_error = None
            return output

    def _perceive_selected(self, request: PerceptionRequest) -> PerceptionText:
        signals: list[PerceptionSignal] = []
        things: list[PerceivedThing] = []
        measurements: dict[str, dict[str, Any]] = {}
        limits: list[str] = []
        plugin_runs: list[PerceptionPluginRun] = []

        # Catalog plugin_id attributes results. The instance plugin_id is the
        # implementation identity and is not rewritten to repair provenance.
        for definition, plugin in self.applied:
            catalog_id = definition.plugin_id
            execution = self._execute_plugin(
                plugin, request, catalog_id, prior_things=tuple(things)
            )
            # The plugin report keeps timing and error; domain run fields stay
            # on the perception result.
            self._record_plugin(catalog_id, execution.duration_ms, execution.error)
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
                    error=execution.error,
                )
            )
            signals.extend(attributed_signals)
            things.extend(attributed_things)
            if execution.batch.measurements:
                measurements[catalog_id] = dict(execution.batch.measurements)
            limits.extend(plugin.contract.limitations)

        return PerceptionText(
            schema=PERCEPTION_TEXT_SCHEMA,
            plugin_id=self.plugin_id,
            status=_overall_status(plugin_runs),
            signals=tuple(signals),
            things=tuple(things),
            plugin_runs=tuple(plugin_runs),
            measurements=measurements,
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
        self._feed_providers = providers

    def _reset_plugin(self, plugin: Any, shared_memory=None) -> None:
        try:
            reset_plugin(plugin, shared_memory)
        except Exception:
            if FAILURE_POLICY.reset == "propagate":
                raise

    def _execute_plugin(
        self,
        plugin: Any,
        request: PerceptionRequest,
        catalog_id: str,
        prior_things: tuple[PerceivedThing, ...] = (),
    ) -> _PluginExecution:
        started = clock()
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
                metadata=request.metadata,
                shared_memory=request.shared_memory,
                prior_things=prior_things,
            )
            batch = plugin.perceive(inputs)
            if not isinstance(batch, PerceptionEvidenceBatch):
                raise TypeError(
                    f"plugin {catalog_id!r} must return PerceptionEvidenceBatch"
                )
            status: PluginResultStatus = "ok" if batch.signals or batch.things else "empty"
            return _execution(
                started,
                status=status,
                batch=batch,
            )
        except PerceptionPluginWarmingUp as exc:
            return _execution(
                started,
                status="warming_up",
                batch=PerceptionEvidenceBatch(measurements=exc.measurements),
                error=exc.reason,
            )
        except Exception as exc:
            if FAILURE_POLICY.update != "isolate_plugin":
                raise
            return _execution(
                started,
                status="error",
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
    error: str | None = None,
) -> _PluginExecution:
    return _PluginExecution(
        status=status,
        batch=batch or PerceptionEvidenceBatch(),
        duration_ms=elapsed_ms(started),
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
