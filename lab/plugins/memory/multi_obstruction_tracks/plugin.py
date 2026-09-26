"""Memory member of the Workbench obstruction pipeline.

Use with ``lab.plugins.perception.multi_obstruction_tracks.src.plugin``. Its
candidate signal supplies the selected detector's numerical tracking config.
This plugin associates candidates, retains obstacle records for the
``avoid_recent_obstruction`` proposal plugin, and publishes the tracked
observation used by Workbench. Optical flow reads the current camera frame
using the same luminance transform. All cross-frame state lives in the host's
shared map; the tracker and evidence reducer are recreated for each update.
"""
from __future__ import annotations

from dataclasses import replace
from uuid import uuid4

from autonomy.decision.cycle import DecisionFrameContext
from autonomy.decision.activation import bounds_from_config
from autonomy.decision.memory import MemorySnapshot, detach_memory_snapshot, empty_memory_snapshot
from autonomy.decision.observation import Observation
from autonomy.memory import SharedMemory

from autonomy.perception import PerceivedThing, PerceptionSignal, build_perception_request
from implementations.memory.bounded_evidence import reduce_evidence
from implementations.perception.components.camera import FRONT_CAMERA_RGB_INPUT, provide_camera_frame
from lab.plugins.perception.multi_obstruction_tracks.src.plugin import normalize_gray, _mean_confidence
from .tracker import ObstructionTrackState


class MultiObstructionMemory:
    implementation_id = "multi_obstruction_tracks"
    history_keys = (
        "multi_obstruction_tracks.history",
        "multi_obstruction_tracks.previous_gray",
        "multi_obstruction_tracks.next_track_id",
    )

    def __init__(self, **config):
        self.config = config
        self.bounds = bounds_from_config(config)
        self._shared_memory = None  # Reference to the host's map, never a private copy.
        self._empty = empty_memory_snapshot(
            memory_id="memory-reset-1", epoch_id="epoch-1", bounds=self.bounds,
            created_at_ms=0, implementation_id=self.implementation_id,
        )

    def snapshot(self) -> MemorySnapshot:
        snapshot = self._empty
        if self._shared_memory is not None:
            snapshot = self._shared_memory.get("decision.snapshot") or self._empty
        return detach_memory_snapshot(snapshot)

    def reset(self, shared_memory: SharedMemory | None = None) -> MemorySnapshot:
        if shared_memory is not None:
            self._shared_memory = shared_memory
        if self._shared_memory is None:
            raise ValueError("tracking memory reset requires a shared-memory map")
        epoch = f"epoch-{uuid4().hex}"
        for key in (*self.history_keys, "decision.observation"):
            self._shared_memory.pop(key, None)
        self._shared_memory["decision.snapshot"] = replace(
            self._empty, memory_id=f"memory-reset-{epoch}", epoch_id=epoch,
        )
        return self.snapshot()

    def _retain_evidence(self, context, observation):
        snapshot = reduce_evidence(
            self.snapshot(), context, observation,
            implementation_id=self.implementation_id, **self.config,
        )
        context.shared_memory["decision.snapshot"] = snapshot
        return snapshot

    def update(self, context: DecisionFrameContext, observation: Observation | None) -> MemorySnapshot:
        shared_memory = context.shared_memory
        if shared_memory is None:
            raise ValueError("tracking memory requires a host shared-memory map")
        self._shared_memory = shared_memory
        shared_memory.pop("decision.observation", None)
        marker = next((signal for signal in observation.signals
                       if signal.get("signal_id") == "multi_obstruction_candidates"), None) if observation else None
        if marker is None:
            for key in self.history_keys:
                shared_memory.pop(key, None)
            return self._retain_evidence(context, observation)

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
                      "tracking_implementation": self.implementation_id},
        )
        snapshot = self._retain_evidence(context, tracked_observation)
        shared_memory["multi_obstruction_tracks.history"] = lookback_tracks
        shared_memory["multi_obstruction_tracks.previous_gray"] = gray
        shared_memory["multi_obstruction_tracks.next_track_id"] = tracker._next_track_id
        shared_memory["decision.observation"] = tracked_observation
        return snapshot
