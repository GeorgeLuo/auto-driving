# Perception plugins

One folder per plugin, named for its `plugin_id`. For example, the `frame`
plugin lives in `plugins/frame/`.

## Rules

- `plugin.py` holds the plugin class, which declares `plugin_id`. Ids are
  unique within the perception step: registering a second definition under an
  existing id raises `DuplicatePluginIdError`.
- Register a plugin with one entry in `../catalog.py`: its `spec`
  (`module.path:Class`), a description, and its default config.
- Declare each input through a feed, such as `FRONT_CAMERA_RGB_INPUT` from
  `../feeds/camera.py`. Never read sensors directly.
- Emit only the perception evidence types from
  `autonomy/decision_cycle/perception/evidence/`: `PerceivedThing`,
  `PerceptionSignal` and `ViewLocation`, in a `PerceptionEvidenceBatch`.
- How several plugins share one frame, and what a failure does, is declared in
  `autonomy/decision_cycle/perception/interface.py`: `composition_declaration`
  and `failure_policy`. `PerceptionRunner` reads those values. A plugin error
  is isolated, a missing feed skips that plugin, and an explicit reset or
  selection-removal reset error propagates. Missing feeds also reset stateful
  plugins inside frame execution; an error in that reset is isolated as the
  plugin's frame error. A `partial` frame can include an empty or warming-up
  plugin alongside an error or unavailable plugin; it need not contain evidence.
- Reuse goes through `../shared/`. Subclassing another plugin's public class
  is allowed. Importing another plugin's private names is not.
- Variants of a plugin, such as `floor_continuity_temporal`, get their own
  folder.
- A `cache/` folder inside a plugin folder is ignored by git.
