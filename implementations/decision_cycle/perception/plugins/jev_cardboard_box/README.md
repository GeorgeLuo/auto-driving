# Jev cardboard box

`jev_cardboard_box` sends the current camera image to Jev 1.14 and asks two
questions in one request: whether a cardboard shipping box is visible, and
where it is. It emits `has_box`, `obstacle_in_sight`, and at most one
`obstruction_evidence` with normalized `(left, top, right, bottom)` coordinates.

Set `JEV_API_KEY` (or `TYPESAFE_API_KEY`) in the process environment, then run
all captures through the ordinary CLI:

```sh
.venv/bin/python cli/automa vehicles perception inspect \
  lab/runs/picar-stills-20261017 \
  --plugin jev_cardboard_box --record --json
```

The default image width is 640, JPEG quality is 90, and the request timeout is
10 seconds. The model is pinned to `jev-1.14.0`. A presence probability of at
least 0.5 sets `has_box`; a box confidence of at least 0.5 is also required to
publish coordinates. These are initial thresholds, not calibrated guarantees.
Jev always returns coordinates for a Box question, including when the target
is absent; the separate presence question suppresses those boxes.

The plugin reuses its HTTPS session and makes one multipart request per frame,
without retries or response caching. Measurements include preparation and
request latency, the model, probabilities, and token usage. Missing keys,
HTTP failures, and malformed detections are plugin errors rather than evidence
of an empty scene. Keys and request headers are not part of evidence or config.

This detects one cardboard shipping box per image, with no depth or temporal
identity. It uploads each selected frame to TypeSafe. Their
[Images](https://docs.typesafe.ai/images) and
[Box](https://docs.typesafe.ai/primitives/box) interfaces are experimental and
require the images opt-in header, which this plugin sends.
