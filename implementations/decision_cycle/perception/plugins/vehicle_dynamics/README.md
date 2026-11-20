# `vehicle_dynamics` perception plugin

Windowed perception plugin that estimates scale-free angular yaw rate (`visual_yaw_rate_rad_s`) from consecutive front-camera frames and fits the vehicle's steering-response curve (`turn_response_available`) against applied autonomous controls.

## Contract & conventions

- **Inputs**: `FRONT_CAMERA_RGB_INPUT` + host `shared_memory` (`action.control_applications`).
- **Sign convention**: Automa convention (`+1` steering = right, `+` yaw rate = right turn, signed throttle `+` forward / `-` reverse). An inverted-steering platform yields negative `gain_per_s` with `turn_sign: "inverted"`.
- **Observe-only / manual runs**: `visual_yaw_rate_rad_s` still runs purely from camera imagery; turn-response fitting updates only on intervals with a single uniform applied autonomous control settled for at least `command_settle_ms` before the interval (`control_applications_over`) and non-stalled visual motion (`median_px_shift >= min_moving_px_shift`).
- **Large shifts & blocked contacts**: Primary tracking uses forward-backward `PyrLK` with an automatic ORB descriptor-matching fallback for large 5 Hz shifts (`max_step_yaw_radians = 0.90`). `fit_turn_response` uses one-sided upper-consensus clustering so partial furniture/wall contacts cannot pull the unimpeded curve down.
- **Calibration**: Scale-free — camera height and pitch are not required inputs; camera intrinsics (`vertical_fov_degrees`, `barrel_distortion`) default to the Chase/RC prior (`70°`, `0.12`). Configure actual vehicle camera intrinsics to avoid focal-length scale bias.
