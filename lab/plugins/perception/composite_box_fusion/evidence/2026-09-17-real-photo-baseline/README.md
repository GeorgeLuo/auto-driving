# Issue #219 evidence snapshot: real-photo baseline

This directory preserves the reproducible, sanitized evidence for the local
Issue #219 CV-ensemble/Jev experiment run on 2026-09-17. It is exploratory
evidence outside milestone work, not a production detector or an acceptance
record.

## Contents

- [`config.json`](config.json): final detector and fusion configuration.
- [`jev_prompt.txt`](jev_prompt.txt): the natural-language Jev instructions.
- [`input_manifest.json`](input_manifest.json): source image names, dimensions,
  hashes, and local-only provenance.
- [`results.json`](results.json): compact per-frame results and selected boxes.
- [`findings/`](findings/): sanitized full structured findings for the three
  recorded runs, including raw CV candidates, clusters, hypotheses, and Jev
  responses. Local cache and absolute source paths are removed or normalized.
- [`stitches/`](stitches/): pair-only visual comparisons with all CV cues next
  to the Jev aggregate.

The implementation is in [`src/plugin.py`](../../src/plugin.py), with the
stitch renderer in [`stitch_live.py`](../../../../../experiments/issue-219/stitch_live.py).

## Experiment shape

Six CV strategies ran: `classical_regions`, `canny_morph`,
`quad_rectangularity`, `partial_contour`, `photometric_windows`, and
`hough_fragments`. Hough remained visible in the CV audit but was excluded
from Jev fusion because it was dominated by room/carpet geometry. The fusion
packet was capped at 120 raw candidates, clustered at IoU 0.12 and center
distance 0.07, with up to 12 clusters and four hypotheses per cluster.

Jev received structured normalized CV evidence and the prompt in
[`jev_prompt.txt`](jev_prompt.txt); it did not receive the image or a
simulator-only target signal. The prompt was reused unchanged across all
three frames.

## Results

| frame | role | CV packet | Jev result | finding |
|---|---|---:|---:|---|
| `IMG_1001` | optimized | 120 raw / 100 fusion-eligible / 9 clusters | 4 boundaries | Both salient boxes recovered; two nuisance boundaries remained. |
| `IMG_1002` | generalization test | 120 / 100 / 11 clusters | 5 boundaries | Both visible boxes recovered; three small floor/background candidates remained. |
| `IMG_1003` | generalization test | 120 / 100 / 12 clusters | 5 boundaries | Rear/left box recovered; foreground boundary clipped right/lower extent; three nuisance candidates remained. |

There are no ground-truth annotations in this snapshot, so this is qualitative
feasibility evidence rather than an IoU, precision, or recall benchmark. The
main positive result is that complementary partial cues produced useful
object-sized aggregate hypotheses. The main failure is that Jev still accepts
nuisance clusters and `robust_extent` can trim a legitimate extreme.

## Reproduction

The original stills remain local and are identified by SHA-256 in
`input_manifest.json`. Re-run the candidate with the same configuration while
providing the Jev credential only through `JEV_API_KEY` or
`TYPESAFE_API_KEY`; never put the credential in a manifest, source file, cache,
or issue comment.

The raw CLI runs and response caches are intentionally not committed. The
sanitized findings and stitched visuals above are the durable evidence needed
to continue the experiment without depending on ignored run directories.
