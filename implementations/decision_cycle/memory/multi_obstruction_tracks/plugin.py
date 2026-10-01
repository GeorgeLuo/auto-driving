"""Memory member of the Workbench obstruction pipeline.

Use with ``implementations.decision_cycle.perception.multi_obstruction_tracks.plugin``. Its
candidate signal supplies the selected detector's numerical tracking config.
This plugin associates candidates, retains obstacle records for the
``avoid_recent_obstruction`` proposal plugin, and publishes the tracked
observation used by Workbench. Optical flow reads the current camera frame
using the same luminance transform. All cross-frame state lives in the host's
shared map; the tracker and evidence reducer are recreated for each update.
The retained-evidence ledger is kept at ``LEDGER_KEY`` and its records are
published at ``EVIDENCE_KEY``, where ``avoid_recent_obstruction`` reads them.
"""
from __future__ import annotations

from dataclasses import replace
from uuid import uuid4

from autonomy.decision_cycle.context import DecisionFrameContext
from autonomy.decision_cycle.observation.values import Observation
from autonomy.decision_cycle.perception.evidence.values import (
    PerceivedThing,
    PerceptionSignal,
)
from autonomy.decision_cycle.perception.inputs import build_perception_request
from autonomy.shared_memory import SharedMemory

from implementations.decision_cycle.memory.bounded_evidence.ledger import (
    EVIDENCE_KEY,
    EvidenceLedger,
    bounds_from_config,
    detach_ledger,
    empty_ledger,
)
from implementations.decision_cycle.memory.bounded_evidence.plugin import (
    reduce_evidence,
)
from implementations.decision_cycle.perception.components.camera import (
    FRONT_CAMERA_RGB_INPUT,
    provide_camera_frame,
)
from implementations.decision_cycle.perception.multi_obstruction_tracks.plugin import normalize_gray, _mean_confidence
from .tracker import ObstructionTrackState


LEDGER_KEY = "multi_obstruction_tracks.ledger"


class MultiObstructionMemory:
    plugin_id = "multi_obstruction_tracks"
    history_keys = (
        "multi_obstruction_tracks.history",
        "multi_obstruction_tracks.previous_gray",
        "multi_obstruction_tracks.next_track_id",
    )

    def __init__(self, **config):
        self.config = config
        self.bounds = bounds_from_config(config)
        self._empty = empty_ledger(
            memory_id="memory-reset-1", epoch_id="epoch-1", bounds=self.bounds,
            created_at_ms=0, implementation_id=self.plugin_id,
        )

    def ledger(self, shared_memory: SharedMemory | None) -> EvidenceLedger:
        current = shared_memory.get(LEDGER_KEY) if shared_memory is not None else None
        return detach_ledger(current if isinstance(current, EvidenceLedger) else self._empty)

    def status(self, shared_memory: SharedMemory | None) -> dict:
        return self.ledger(shared_memory).to_dict()

    def reset(self, shared_memory: SharedMemory) -> None:
        epoch = f"epoch-{uuid4().hex}"
        for key in (*self.history_keys, "decision.observation"):
            shared_memory.pop(key, None)
        self._publish(
            shared_memory,
            replace(self._empty, memory_id=f"memory-reset-{epoch}", epoch_id=epoch),
        )

    def _publish(self, shared_memory: SharedMemory, ledger: EvidenceLedger) -> None:
        shared_memory[LEDGER_KEY] = ledger
        shared_memory[EVIDENCE_KEY] = ledger.records

    def _retain_evidence(self, context, observation) -> None:
        self._publish(
            context.shared_memory,
            reduce_evidence(
                self.ledger(context.shared_memory), context, observation,
                implementation_id=self.plugin_id, **self.config,
            ),
        )

    def update(self, context: DecisionFrameContext, observation: Observation | None) -> None:
        shared_memory = context.shared_memory
        if shared_memory is None:
            raise ValueError("tracking memory requires a host shared-memory map")
        shared_memory.pop("decision.observation", None)
        marker = next((signal for signal in observation.signals
                       if signal.get("signal_id") == "multi_obstruction_candidates"), None) if observation else None
        if marker is None:
            for key in self.history_keys:
                shared_memory.pop(key, None)
            self._retain_evidence(context, observation)
            return

        properties = marker["properties"]
        config = properties["tracking_config"]
        tracker = ObstructionTrackState(**config)
        memory_tracks_read = tracker._restore_from_history(
            shared_memory.get("multi_obstruction_tracks.history")
        )
        tracker._previous_gray = shared_memory.get("multi_obstruction_tracks.previous_gray")
        tracker._next_track_id = shared_memory.get(
            "multi_obstruction_tracks.next_track_id", tracker._next_track_id
        )
        frame = provide_camera_frame(build_perception_request(context.sensor_snapshot), FRONT_CAMERA_RGB_INPUT)
        gray = normalize_gray(frame.rgb, **properties["normalization"])
        source = marker.get("source_plugin_id")
        candidates = [PerceivedThing.from_dict(thing) for thing in observation.things
                      if thing.get("source_plugin_id") == source]
        detector_summary = {"candidate_count": len(candidates)}
        active, events, association = tracker._associate(candidates, gray=gray)

        emitted_tracks = [
            track
            for track in active
            if tracker._should_emit_track(track, events.get(track.track_id, "matched"))
        ]
        things = tuple(
            tracker._materialize(track, events.get(track.track_id, "matched"))
            for track in emitted_tracks
        )
        lookback_tracks = tracker._lookback_tracks()
        signals = (
            PerceptionSignal(
                "multi_obstruction_tracks_available",
                bool(things),
                _mean_confidence(things),
                {
                    "candidate_count": len(candidates),
                    "active_track_count": len(things),
                    "track_events": {
                        str(track_id): event for track_id, event in events.items()
                    },
                    "floor_cutoff_y": tracker.floor_cutoff_y,
                    "memory_tracks_read": memory_tracks_read,
                    "memory_tracks_written": len(lookback_tracks),
                },
            ),
        )
        measurements = {
            "candidate_count": len(candidates),
            "active_track_count": len(things),
            "track_ids": [thing.thing_id for thing in things],
            "track_events": events,
            "detector": detector_summary,
            "association": association,
            "floor_cutoff_y": tracker.floor_cutoff_y,

            "memory_tracks_read": memory_tracks_read,
            "memory_tracks_written": len(lookback_tracks),
        }
        tracked_observation = replace(
            observation,
            things=tuple(thing for thing in observation.things if thing.get("source_plugin_id") != source)
                   + tuple(replace(thing, source_plugin_id=source).to_dict() for thing in things),
            signals=tuple(signal for signal in observation.signals if signal is not marker)
                    + tuple(replace(signal, source_plugin_id=source).to_dict() for signal in signals),
            metadata={**observation.metadata, "tracking": measurements,
                      "tracking_implementation": self.plugin_id},
        )
        self._retain_evidence(context, tracked_observation)
        shared_memory["multi_obstruction_tracks.history"] = lookback_tracks
        shared_memory["multi_obstruction_tracks.previous_gray"] = gray
        shared_memory["multi_obstruction_tracks.next_track_id"] = tracker._next_track_id
        shared_memory["decision.observation"] = tracked_observation
