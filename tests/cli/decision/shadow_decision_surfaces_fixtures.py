from __future__ import annotations
import json
import os
import tempfile
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch
from autonomy.decision.shadow_authority import AUTHORIZED_IDLE_REASON
from cli.automa_cli.decision import (
    DECISION_ENGINES,
    ENGINE_ID,
    strict_decode_apply_memory,
    strict_decode_apply_observation,
    update_vehicle_decision,
)
from implementations.decision.catalog import create_shadow_proposals_engine


SOURCES = Path(__file__).resolve().parents[1] / "sources" / "json"
ACTIVE_RUN = SOURCES / "apply_active_left"
NO_MEM_RUN = SOURCES / "apply_no_memory"
TWO_FRAME_RUN = SOURCES / "apply_two_frames"


class ShadowDecisionSurfaceFixture:
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.runtime_root = Path(self._tmp.name) / "vehicles"
        self.runtime_root.mkdir(parents=True)
        self._env_patch = patch.dict(
            os.environ,
            {"AUTOMA_RUNTIME_ROOT": str(self.runtime_root)},
        )
        self._env_patch.start()
        # decision module reads RUNTIME_ROOT at import time; rebind for tests.
        import cli.automa_cli.decision as decision_mod

        self._decision_mod = decision_mod
        self._old_runtime = decision_mod.RUNTIME_ROOT
        decision_mod.RUNTIME_ROOT = self.runtime_root

    def tearDown(self) -> None:
        self._decision_mod.RUNTIME_ROOT = self._old_runtime
        self._env_patch.stop()
        self._tmp.cleanup()

    def _stage(self, engine_id: str = ENGINE_ID, vehicle_id: str = "chase-sim-chaser"):
        return update_vehicle_decision(
            vehicle_id=vehicle_id,
            engine_id=engine_id,
            json_output=True,
        )

    def _sample_cycle(self):
        engine = create_shadow_proposals_engine()
        obs = strict_decode_apply_observation(
            json.loads((ACTIVE_RUN / "sequence.json").read_text())["frames"][0][
                "observation"
            ]
        )
        mem = strict_decode_apply_memory(
            json.loads((ACTIVE_RUN / "sequence.json").read_text())["frames"][0][
                "memory"
            ]
        )
        cycle, control = engine.run_cycle(
            frame_id="frame_001",
            frame_index=1,
            timestamp_ms=1000,
            observation=obs,
            memory=mem,
        )
        self.assertEqual(control.reason, AUTHORIZED_IDLE_REASON)
        return cycle

    def _physical_publication(
        self,
        *,
        published_at_ms: int = 2_000,
        engine_id: str = ENGINE_ID,
    ) -> dict:
        cycle = self._sample_cycle().to_dict()
        return {
            "schema": "automa_physical_decision_publication_v0",
            "ok": True,
            "status": "ready",
            "reason": "",
            "read_at_ms": published_at_ms,
            "result_age_ms": 0,
            "stale_after_ms": 1_000,
            "decision": {
                "vehicle_id": "piracer",
                "source_id": "donkeycar:piracer",
                "run_id": "donkey-run-fixture",
                "activation_engine_id": engine_id,
                "activation_activated_at_ms": 1_000,
                "generation_id": f"{engine_id}:1000",
                "frame_id": "frame_001",
                "frame_index": 1,
                "timestamp_ms": 1_000,
                "published_at_ms": published_at_ms,
                "activation": {
                    "engine_id": engine_id,
                    "activated_at_ms": 1_000,
                    "generation_id": f"{engine_id}:1000",
                    "engine_config": deepcopy(
                        DECISION_ENGINES[engine_id]["engine_config"]
                    ),
                },
                "cycle": cycle,
            },
        }
