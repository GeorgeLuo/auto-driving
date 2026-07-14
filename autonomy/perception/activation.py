from __future__ import annotations

import importlib
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .inputs import build_perception_request
from .interface import PerceptionMapper, PerceptionText


PERCEPTION_ACTIVATION_SCHEMA = "automa_perception_activation_v0"


@dataclass(frozen=True)
class PerceptionActivation:
    algorithm: str
    mapper_spec: str
    mapper_config: dict[str, Any]
    source_path: Path
    payload: dict[str, Any]


def read_perception_activation(path: Path) -> PerceptionActivation:
    if not path.exists():
        raise FileNotFoundError(f"perception activation is missing: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"perception activation must be a JSON object: {path}")
    if payload.get("schema") != PERCEPTION_ACTIVATION_SCHEMA:
        raise ValueError(
            f"perception activation has unsupported schema {payload.get('schema')!r}: {path}"
        )
    perception = payload.get("perception")
    if not isinstance(perception, dict):
        raise ValueError(f"perception activation has no perception section: {path}")
    algorithm = perception.get("algorithm")
    mapper_spec = perception.get("mapper_spec")
    mapper_config = perception.get("mapper_config")
    if not isinstance(algorithm, str) or not algorithm:
        raise ValueError(f"perception activation has no algorithm: {path}")
    if not isinstance(mapper_spec, str) or not mapper_spec:
        raise ValueError(f"perception activation has no mapper_spec: {path}")
    if not isinstance(mapper_config, dict):
        raise ValueError(f"perception activation has invalid mapper_config: {path}")
    return PerceptionActivation(
        algorithm=algorithm,
        mapper_spec=mapper_spec,
        mapper_config=dict(mapper_config),
        source_path=path,
        payload=payload,
    )


def load_perception_mapper(
    activation: PerceptionActivation,
    *,
    reload_module: bool = False,
) -> PerceptionMapper:
    return instantiate_perception_mapper(
        activation.mapper_spec,
        activation.mapper_config,
        reload_module=reload_module,
    )


def instantiate_perception_mapper(
    mapper_spec: str,
    mapper_config: dict[str, Any],
    *,
    reload_module: bool = False,
) -> PerceptionMapper:
    module_name, separator, class_name = mapper_spec.partition(":")
    if not separator or not module_name or not class_name:
        raise ValueError("perception mapper spec must be 'module.path:ClassName'")
    importlib.invalidate_caches()
    module = importlib.import_module(module_name)
    if reload_module:
        module = importlib.reload(module)
    mapper_cls = getattr(module, class_name)
    mapper = mapper_cls(**mapper_config)
    if not isinstance(mapper, PerceptionMapper):
        raise TypeError(f"configured perception mapper does not satisfy PerceptionMapper: {mapper_spec}")
    mapper.reset()
    return mapper


class ActivatedPerceptionStage:
    """Decision-cycle perception stage backed by one activated mapper."""

    def __init__(self, activation: PerceptionActivation) -> None:
        self.activation = activation
        self.mapper = load_perception_mapper(activation)
        self.last_output: PerceptionText | None = None
        self.last_duration_ms: float | None = None
        self.last_frame_index: int | None = None

    def reset(self) -> None:
        self.mapper.reset()
        self.last_output = None
        self.last_duration_ms = None
        self.last_frame_index = None

    def __call__(self, context) -> PerceptionText | None:
        if context.sensor_snapshot is None:
            self.last_output = None
            self.last_duration_ms = None
            self.last_frame_index = context.frame_index
            return None
        started = time.perf_counter()
        try:
            self.last_output = self.mapper.perceive(
                build_perception_request(
                    context.sensor_snapshot,
                    metadata={
                        "runtime": "onboard",
                        "algorithm": self.activation.algorithm,
                        "activation": str(self.activation.source_path),
                        "frame_index": context.frame_index,
                    },
                )
            )
        finally:
            self.last_duration_ms = round(
                (time.perf_counter() - started) * 1000.0,
                3,
            )
            self.last_frame_index = context.frame_index
        return self.last_output

    def status(self) -> dict[str, Any]:
        output = self.last_output
        return {
            "algorithm": self.activation.algorithm,
            "mapper_spec": self.activation.mapper_spec,
            "last_status": output.status if output is not None else None,
            "last_duration_ms": self.last_duration_ms,
            "last_frame_index": self.last_frame_index,
            "last_thing_count": (
                len(output.things) if output is not None else 0
            ),
            "last_plugin_runs": (
                [plugin_run.to_dict() for plugin_run in output.plugin_runs]
                if output is not None
                else []
            ),
        }
