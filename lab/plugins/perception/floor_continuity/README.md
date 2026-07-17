# Floor Continuity

This stateless candidate asks whether a region remains connected to robust
lower-center floor seeds across locally compatible color and texture, without
crossing a strong image gradient. It then emits the first sustained
interruption encountered after supported floor in each image column.

The candidate is deliberately generic. It does not know simulator colors,
object classes, metric geometry, or whether the estimated floor is safe.
Normalized regions mean only that the current image contains evidence
consistent with visible floor being interrupted there.

The implementation uses the core NumPy and OpenCV runtime at 320x240 by
default. Diagnostic floor masks, boundary masks, overlays, and summaries are
written only by an explicitly recorded experiment.

Run it against an active simulator:

```sh
./cli/automa vehicles perception run \
  --id chase-sim-chaser --candidate floor_continuity --record
```

Apply it to one archived image or a whole capture directory locally:

```sh
./cli/automa vehicles perception apply path/to/frame.jpg \
  --candidate floor_continuity --record

./cli/automa vehicles perception apply path/to/frames \
  --candidate floor_continuity --record
```

Every manifest parameter can be overridden for one bounded run without
changing the candidate default:

```sh
./cli/automa vehicles perception apply path/to/frames \
  --candidate floor_continuity \
  --set minimum_boundary_confidence=0.7
```

Apply another strategy to the same recorded frames before comparing
behavior. Simulator success validates the integration shape and controlled
scene behavior; it does not qualify the candidate for physical promotion.

The current default includes a `0.65` confidence floor and a `0.24` absolute
gradient barrier. They reject weak carpet and image-edge fragments while
preserving the stronger pale-box contacts seen in both Chase and archived Pi
frames. A higher `0.75` confidence floor removed useful distant box evidence,
so it was not adopted.

With the current defaults, a five-frame `chaser-depth-obstacles` application emitted
20 boundaries without failures and measured a 40.320 ms unrecorded plugin
median on the development Mac. Six archived Pi capture sets contributed 50
frames with no processing failures and 190 emitted boundaries. Their
unrecorded plugin medians ranged from 37.263 to 65.531 ms. Recorded runs are
slower because diagnostic masks and overlays are encoded inside plugin timing.

These are preliminary operability measurements. The Pi frames are not labeled,
the processing ran on the development machine, and visible carpet fragments
remain. They do not establish Pi performance, semantic accuracy, or promotion.
