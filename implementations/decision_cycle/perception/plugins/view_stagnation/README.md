# View stagnation

`ViewStagnationPlugin` compares fresh front-camera captures with a grayscale
anchor image. It runs in the `chase-v1-detect-by-color` perception preset and
can also be selected as `view_stagnation` on any RGB camera feed.

The plugin emits the `view_static` perception signal and rewrites
`shared_memory["perception.view_stagnation"]` every update. The result contains
`view_static`, `status`, `reason`, `frame_id`, `captured_at_ms`, `change_score`,
`unchanged_for_ms`, `sample_count`, and `confidence`.

- `true` / `static`: the view has remained below the change threshold for the
  required duration and sample count.
- `false` / `moving`: a fresh capture differs from the anchor above the
  threshold. The current capture starts the next unchanged interval.
- `null` / `warming_up` or `unknown`: evidence is insufficient or discontinuous.

The change score is mean absolute grayscale difference divided by 255.
Comparing against the interval's anchor lets small changes accumulate instead
of repeatedly discarding them between adjacent frames. Confidence is an
uncalibrated measure of distance from the change threshold: `1 - score / threshold`
for static evidence and `1 - threshold / score` for moving evidence.

| Configuration | Default |
|---|---|
| `thumbnail_width`, `thumbnail_height` | `64`, `48` pixels |
| `change_threshold` | `0.01` |
| `static_after_ms` | `1000` |
| `min_samples` | `3` |
| `max_gap_ms` | `2000` |

History lives at `perception.view_stagnation.history` and retains one thumbnail,
timestamps, counters, the input/configuration signature, and the last result.
It survives plugin replacement and shared-memory JSON round trips. Missing
input or reset clears history and publishes an unknown result. Repeated frame
IDs, nonincreasing capture timestamps, long capture gaps, changed image size,
or changed configuration require fresh history. Consumers should check the
result's frame and capture timestamp before using it.

Visual stagnation alone does not establish that a vehicle is stuck. Movement
commands are interpreted by later decision steps. Weak texture, very slow
movement, camera shake, lighting changes, and moving objects affect this cue.
Fresh IDs and timestamps also cannot establish that camera pixels are live.
