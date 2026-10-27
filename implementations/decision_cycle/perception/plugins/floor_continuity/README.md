# Floor Continuity

This stateless plugin asks whether a region remains connected to robust
lower-center floor seeds across locally compatible color and texture, without
crossing a strong image gradient. It then emits the first sustained
interruption encountered after supported floor in each image column.

The plugin is deliberately generic. It does not know simulator colors,
object classes, metric geometry, or whether the estimated floor is safe.
Normalized regions mean only that the current image contains evidence
consistent with visible floor being interrupted there.

The implementation uses the core NumPy and OpenCV runtime at 320x240 by
default.

Stage it on a host with the frame plugin:

```sh
./cli/automa hosts stage perception --name chase-sim-chaser --plugin frame --plugin floor_continuity
```

Apply it to one archived image or a whole capture directory:

```sh
./cli/automa plugins inspect perception path/to/frame.jpg \
  --plugin frame --plugin floor_continuity

./cli/automa plugins inspect perception path/to/frames \
  --plugin frame --plugin floor_continuity
```

Its default config is the `floor_continuity` entry in
`implementations/decision_cycle/perception/catalog.py`.

Apply another strategy to the same recorded frames before comparing
behavior. Simulator success validates the integration shape and controlled
scene behavior; it does not qualify the plugin for physical use.

The current default includes a `0.65` confidence floor and a `0.24` absolute
gradient barrier. They reject weak carpet and image-edge fragments while
preserving the stronger pale-box contacts seen in both Chase and archived Pi
frames. A higher `0.75` confidence floor removed useful distant box evidence,
so it was not adopted.

With the current defaults, a five-frame `chaser-depth-obstacles` application emitted
20 boundaries without failures and measured a 40.320 ms unrecorded plugin
median on the development Mac. Six archived Pi capture sets contributed 50
frames with no processing failures and 190 emitted boundaries. Their
unrecorded plugin medians ranged from 37.263 to 65.531 ms.

These are preliminary operability measurements. The Pi frames are not labeled,
the processing ran on the development machine, and visible carpet fragments
remain. They do not establish Pi performance, semantic accuracy, or promotion.
