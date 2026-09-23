"""Memory member of the Workbench obstruction pipeline.

Use with ``lab.plugins.perception.multi_obstruction_tracks.src.plugin``. Its
candidate signal supplies the selected detector's numerical tracking config.
This plugin associates candidates, retains obstacle records for the
``avoid_recent_obstruction`` proposal plugin, and publishes the tracked
observation used by Workbench. Optical flow reads the current camera frame
using the same luminance transform.
"""
from __future__ import annotations

from dataclasses import replace

from autonomy.decision.cycle import DecisionFrameContext
from autonomy.decision.memory import MemorySnapshot
from autonomy.decision.observation import Observation

from autonomy.perception import PerceivedThing, PerceptionSignal, build_perception_request
from implementations.memory.bounded_evidence import BoundedEvidenceLedger
from implementations.perception.components.camera import FRONT_CAMERA_RGB_INPUT, provide_camera_frame
from lab.plugins.perception.multi_obstruction_tracks.src.plugin import normalize_gray, _mean_confidence
from .tracker import ObstructionTrackState


class MultiObstructionMemory(BoundedEvidenceLedger):
    implementation_id = "multi_obstruction_tracks"

    def reset(self) -> MemorySnapshot:
        self.tracker = None
        self.tracking_config = None
        return super().reset()

    def update(self, context: DecisionFrameContext, observation: Observation | None) -> MemorySnapshot:
        memory = context.memory
        if memory is None:
            raise ValueError("tracking memory requires a host shared-memory map")
        memory.pop("decision.observation", None)
        marker = next((signal for signal in observation.signals
                       if signal.get("signal_id") == "multi_obstruction_candidates"), None) if observation else None
        if marker is None:
            self.tracker = None
            self.tracking_config = None
            memory.pop("multi_obstruction_tracks.history", None)
            return super().update(context, observation)

        properties = marker["properties"]
        config = properties["tracking_config"]
        if self.tracker is None or config != self.tracking_config:
            self.tracker = ObstructionTrackState(**config)
            self.tracking_config = config
        memory_tracks_read = self.tracker._restore_from_history(
            memory.get("multi_obstruction_tracks.history")
        )
        frame = provide_camera_frame(build_perception_request(context.sensor_snapshot), FRONT_CAMERA_RGB_INPUT)
        gray = normalize_gray(frame.rgb, **properties["normalization"])
        source = marker.get("source_plugin_id")
        candidates = [PerceivedThing.from_dict(thing) for thing in observation.things
                      if thing.get("source_plugin_id") == source]
        detector_summary = {"candidate_count": len(candidates)}
        active, events, association = self.tracker._associate(candidates, gray=gray)

        emitted_tracks = [
            track
            for track in active
            if self.tracker._should_emit_track(track, events.get(track.track_id, "matched"))
        ]
        things = tuple(
            self.tracker._materialize(track, events.get(track.track_id, "matched"))
            for track in emitted_tracks
        )
        lookback_tracks = self.tracker._lookback_tracks()
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
                    "floor_cutoff_y": self.tracker.floor_cutoff_y,
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
            "floor_cutoff_y": self.tracker.floor_cutoff_y,

            "memory_tracks_read": memory_tracks_read,
            "memory_tracks_written": len(lookback_tracks),
        }
        self.tracker._previous_gray = gray
        self.tracker._frame_index += 1
        tracked_observation = replace(
            observation,
            things=tuple(thing for thing in observation.things if thing.get("source_plugin_id") != source)
                   + tuple(replace(thing, source_plugin_id=source).to_dict() for thing in things),
            signals=tuple(signal for signal in observation.signals if signal is not marker)
                    + tuple(replace(signal, source_plugin_id=source).to_dict() for signal in signals),
            metadata={**observation.metadata, "tracking": measurements,
                      "tracking_implementation": self.implementation_id},
        )
        snapshot = super().update(context, tracked_observation)
        memory["multi_obstruction_tracks.history"] = lookback_tracks
        memory["decision.observation"] = tracked_observation
        return snapshot
