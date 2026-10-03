# Perception shared library

Plain code: functions, plus the working types they return. Code belongs here
when more than one plugin, a memory plugin, or a tool needs it.

## Topics

| Topic | Holds |
|---|---|
| `features/` | Feature-point matching across frames, and motion fits over a sequence. |
| `image/` | Whole-frame image operations: contrast, frame analysis. |
| `landmarks/` | Landmark distance estimates. |
| `motion/` | Scene motion between frames. |
| `obstructions/` | Box geometry: clamping, zones, center distance. |
| `regions/` | Color-region detection. |
| `serialization/` | Canonical JSON for digests and payloads. |

## Rules

- Organize by topic, two levels deep: `shared/<topic>/<module>.py`. Add a new
  topic folder rather than nesting deeper.
- Code here is never a plugin, is never resolved by the framework, and never
  reads the sensor frame. Callers pass it arrays and values.
