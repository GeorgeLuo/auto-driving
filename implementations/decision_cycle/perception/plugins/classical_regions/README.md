# Classical Color Regions

This plugin is a dependency-free region-proposal baseline. It smooths the
image with OpenCV mean shift, applies fixed Lab color bins, extracts connected
components, and emits accepted contours as generic `region_proposal` evidence.

It does not identify objects, obstacles, floor, or depth. The algorithm is
deliberately scene-agnostic and uses no map or model data. Generated overlays
are written only when the experiment runner is invoked with `--record`.
