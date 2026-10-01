# Perception feeds

A feed turns one sensor's reading into a typed plugin input. `camera.py`
turns a camera reading into a `CameraFrame`.

## How feeds run

- A plugin declares each input as a `PerceptionPluginInput`: the name it reads,
  a shared component id, and the feed's provider spec.
- The framework resolves each declared input once per frame and shares the
  result with every plugin that declares it.
- A provider raises `PerceptionComponentUnavailable` when the sensor data is
  missing or has the wrong kind.

## Adding a feed

1. Add a module here with the input type, a helper that builds its
   `PerceptionPluginInput`, and the provider function.
2. Plugins declare that input in their `inputs`.
3. Add a test next to `tests/implementations/decision_cycle/perception/test_camera_feed.py`.

The core contract in `autonomy/decision_cycle/perception/components/` still
calls these components.
