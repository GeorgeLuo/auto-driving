# Run 6 close-range geometry

The map spawn is the original registration estimate for frame 0. The fit follows the bed foot and backpack, the ottoman plinth and rug, then the open door, baseboard, cable and carton. All frames use one room layout. The original map and assets at `1807d284` provide the comparison.

## How the fit works

Frame numbers are zero-based positions in the sorted JPEG sequence. Each annotated position must match its recorded frame ID. The fitter joins JPEGs to reports by that ID. The [annotations](../tools/fit/run6-marks.json) pair real pixels with named landmarks on the native assets. For example, a bed leg has a distinct foot, while a mark along the rug edge can slide along the projected segment. Orthogonal lines through the bed corners give vertical fields of view of 83.0° and 80.2° in frames 0 and 8. The map uses 82°, a downward pitch of 0.181 rad, the recorded camera height of 0.09 m and the existing barrel coefficient of 0.30.

Furniture positions, orientations, dimensions and box footprints were adjusted across multiple views. For a given layout the fitter estimates poses from the marks, then replays the authorized commands through native vehicle physics between those poses. It holds each command until the next timestamp rounded to a physics tick relative to the start of that interval. A speed multiplier selected from short interval predictions (0.08 for this recording) and lower weights across longer gaps allow for stops and sparse reports. The final search renders nearby poses with the native renderer and scores both landmark displacement and the distance to rendered edges around each landmark.

The settled contacts are the backpack at frame 27, ottoman at 33 and carton at 61. A forward native tick moves less than 1 nm at each. The run-specific 0.046 m radius represents clearance from the camera to the contact face. The 0.012 m near plane keeps the carton visible at that clearance.

## Results and reproduction

At identical vehicle poses, mean reprojection error fell from **160.5 to 13.0 pixels** over 113 paired marks in the 15 requested frames. With the fitted camera used for both layouts, the means are 190.2 and 13.0 pixels. The overscan-aware fit places frame 0 one search increment (0.012 m along Z) from the map spawn. These are errors on the fitted observations. The recording gives no independent measurement of absolute pose. The backpack deforms on contact, the rug edge ripples, and the fixed camera leaves residuals at the door baseboard and carton flap.

Reproduction requires NumPy, OpenCV and Pillow (in `requirements.txt`), the native build, and local Git history containing `1807d284`. Place the original 640×480 front-camera JPEG sequence in `simulator/native/build/run6/frames/` and the matching JSON reports in `simulator/native/build/run6/reports/`. From the repository root, run

```sh
cmake --build simulator/native/build --target automa-native-sim native-fit-motion
.venv/bin/python simulator/native/tests/test_fit.py
.venv/bin/python simulator/native/tools/fit/fit.py
```

The fitter reads the checked-in geometry and writes `fit-summary.md` and `fit-NN.jpg` under the ignored `simulator/native/build/room-run6/` directory. Each JPEG shows the real view, the native view and their 50/50 blend. The table records how many marks project in front of both cameras and how many fall inside each distorted output image. The paired residuals use projected landmarks and segments. The triptychs allow visual checks for occlusion.
