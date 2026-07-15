from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .manager import AutonomyManager


DECISION_ACTIVATION_SCHEMA = "automa_decision_activation_v0"


@dataclass(frozen=True)
class DecisionActivation:
    engine_id: str
    engine_spec: str
    engine_config: dict[str, Any]
    source_path: Path
    payload: dict[str, Any]


def read_decision_activation(path: Path) -> DecisionActivation:
    if not path.exists():
        raise FileNotFoundError(f"decision activation is missing: {path}")

    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"decision activation must be a JSON object: {path}")
    if payload.get("schema") != DECISION_ACTIVATION_SCHEMA:
        raise ValueError(
            f"decision activation has unsupported schema {payload.get('schema')!r}: {path}"
        )

    decision = payload.get("decision")
    if not isinstance(decision, dict):
        raise ValueError(f"decision activation has no decision section: {path}")

    engine_id = decision.get("engine_id")
    engine_spec = decision.get("engine_spec")
    engine_config = decision.get("engine_config")
    if not isinstance(engine_id, str) or not engine_id.strip():
        raise ValueError(f"decision activation has no engine_id: {path}")
    if not isinstance(engine_spec, str) or not engine_spec.strip():
        raise ValueError(f"decision activation has no engine_spec: {path}")
    if not isinstance(engine_config, dict):
        raise ValueError(f"decision activation has invalid engine_config: {path}")

    return DecisionActivation(
        engine_id=engine_id,
        engine_spec=engine_spec,
        engine_config=deepcopy(engine_config),
        source_path=path,
        payload=payload,
    )


def apply_decision_activation(
    manager: AutonomyManager,
    activation: DecisionActivation,
) -> dict[str, Any]:
    return manager.load_engine(
        activation.engine_spec,
        activation.engine_config,
    )
