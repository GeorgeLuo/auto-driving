from __future__ import annotations
from pathlib import Path


SOURCES = Path(__file__).resolve().parents[1] / "sources" / "json"
RECURRENCE_SOURCE = SOURCES / "recurrence_sequence" / "sequence.json"
CONFLICT_SOURCE = SOURCES / "conflict_sequence" / "sequence.json"


class MemoryReplayFixture:
    def _record_payload(self, frames: list) -> dict:
        return {
            "schema": "vehicle_memory_replay_v0",
            "vehicle_id": "chase-sim-chaser",
            "frame_count": len(frames),
            "implementation_id": "bounded_evidence",
            "digest": "abc",
            "final": {
                "health": "healthy",
                "record_count": 1,
                "records": [
                    {
                        "record_id": "thing:1:14:floor-plane-v0:18:floor_boundary_000",
                        "kind": "floor_boundary",
                        "label": "boundary",
                        "confidence": 0.9,
                        "provenance": {
                            "frame_id": "frame_001",
                            "observation_id": "obs_001",
                            "evidence_id": "floor_boundary_000",
                        },
                    }
                ],
            },
        }
