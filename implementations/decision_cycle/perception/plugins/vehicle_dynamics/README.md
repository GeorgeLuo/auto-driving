# `vehicle_dynamics` perception plugin

Windowed perception plugin that estimates scale-free angular yaw rate (`visual_yaw_rate_rad_s`) from consecutive front-camera frames and fits the vehicle's steering-response curve (`turn_response_available`) against applied autonomous controls.

## Contract & conventions

- **Inputs**: `FRONT_CAMERA_RGB_INPUT` + host `shared_memory` (`action.control_applications`).
- **Sign convention**: Automa convention (`+1` steering = right, `+` yaw rate = right turn, signed throttle `+` forward / `-` reverse). An inverted-steering platform yields negative `gain_per_s` with `turn_sign: "inverted"`.
- **Observe-only / manual runs**: `visual_yaw_rate_rad_s` still runs purely from camera imagery; turn-response fitting updates only on intervals with a single uniform applied autonomous control (`control_applications_over`).
- **Calibration**: Scale-free — camera height and pitch are not required inputs; camera intrinsics (`vertical_fov_degrees`, `barrel_distortion`) default to the Chase/RC prior (`70°`, `0.12`).
