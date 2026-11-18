# Run 6 close-range geometry

The spawn is the camera pose in frame 0. The fit follows the bed foot and backpack, the ottoman plinth and rug, then the open door, baseboard, cable and carton. All frames use one room layout. The original map and assets at `1807d284` provide the comparison.

## How the fit works

Frame numbers are zero-based positions in the sorted JPEG sequence. Each JPEG is joined to its report by frame ID. The [annotations](../tools/fit/run6-marks.json) pair real pixels with named landmarks on the native assets. For example, a bed leg has a distinct foot, while a mark along the rug edge can slide along the projected segment. Orthogonal lines through the bed corners give vertical fields of view of 83.0° and 80.2° in frames 0 and 8. The map uses 82°, a downward pitch of 0.181 rad, the recorded camera height of 0.09 m and the existing barrel coefficient of 0.30.

Furniture positions, orientations, dimensions and box footprints were adjusted across multiple views. For a given layout the fitter estimates poses from the marks, then replays the authorized commands through native vehicle physics between those poses. It holds each command until the next timestamp. A speed multiplier selected from short interval predictions (0.08 for this recording) and lower weights across longer gaps allow for stops and sparse reports. The final search renders nearby poses with the native renderer and scores both landmark displacement and the distance to rendered edges around each landmark.

The settled contacts are the backpack at frame 27, ottoman at 33 and carton at 61. A forward native tick moves less than 1 nm at each. The run-specific 0.046 m radius represents clearance from the camera to the contact face. The 0.012 m near plane keeps the carton visible at that clearance.

## Results and reproduction

At identical vehicle poses, mean reprojection error fell from **161.2 to 13.0 pixels** over 113 paired marks in the 15 requested frames. With the fitted camera used for both layouts, the means are 190.1 and 13.0 pixels. These are errors on the fitted observations. The recording gives no independent measurement of absolute pose. The backpack deforms on contact, the rug edge ripples, and the fixed camera leaves residuals at the door baseboard and carton flap.

From the repository root with the native build configured and the run 6 evidence in place, run

```sh
cmake --build simulator/native/build --target automa-native-sim native-fit-motion
/Users/gluo/Projects/auto-driving/.venv/bin/python simulator/native/tools/fit/fit.py
```

The fitter reads the checked-in geometry and writes [the per-frame measurements](../build/room-run6/fit-summary.md) and `fit-NN.jpg` in the same ignored directory. Each JPEG shows the real view, the native view and their 50/50 blend. The table records how many marks project in front of both cameras and how many fall inside each source image.
