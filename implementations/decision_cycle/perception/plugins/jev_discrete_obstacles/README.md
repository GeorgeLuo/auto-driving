# Jev discrete movement obstacles

This experimental alternative to `jev_cardboard_box` uses an eye-level-camera
framing and asks for discrete physical obstacles that would impede movement.
The same prompt is used for every image, without object-class names, example
boxes, or scene-specific hints. The exact wording lives in `plugin.py`.

With `JEV_API_KEY` or `TYPESAFE_API_KEY` set, run all the captures through CLI:

```sh
.venv/bin/python cli/automa vehicles perception inspect \
  lab/runs/picar-stills-20261017 \
  --plugin jev_discrete_obstacles --record --json
```

One multipart request asks Jev 1.14 for an obstacle count (zero through six,
where six means six or more), plus six Box answers in left-to-right order.
Only the first N Box answers, where N is the count Jev chose, become
`obstruction_evidence`. Coordinates are validated but never adjusted. There is
no confidence filtering, deduplication, temporal association, or depth estimate.
Object slot numbers refer only to that image. Count confidence and each box's
confidence are reported separately.

The count and localization questions are evaluated independently. A count of
three does not guarantee three distinct objects: the boxes can duplicate one
object, miss another, or disagree with the ordering. Keep that failure visible
when evaluating this experiment. The first 13-image run returned two to four
boxes per frame, with frequent overlapping duplicates and low confidence. It
was a weaker result for identifying separate objects than the class-specific
cardboard prompt.

`--record` keeps the full provider response (including speculative slots
outside the selected count) and the prompt as per-frame diagnostic artifacts.
Request headers and credentials are never saved. Images are sent to TypeSafe's
experimental [vision API](https://docs.typesafe.ai/images).
