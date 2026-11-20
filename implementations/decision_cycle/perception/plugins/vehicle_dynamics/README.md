# `vehicle_dynamics` perception plugin

Windowed perception plugin that estimates scale-free angular yaw rate (`visual_yaw_rate_rad_s`) from consecutive front-camera frames and fits the vehicle's steering-response curve (`turn_response_available`) against applied autonomous controls.

## Contract & conventions

- `FRONT_CAMERA_RGB_INPUT` supplies the images. The plugin reads applied commands from `action.control_applications` in host shared memory.
- Positive steering and yaw point right. Positive throttle drives forward and negative throttle drives in reverse. An inverted-steering platform yields negative `gain_per_s` and an `"inverted"` `turn_sign`.
- Camera-only yaw estimation works in manual and observe-only modes. Learning requires visual motion and a continuous history of applied autonomous commands through the image pair and the preceding `command_settle_ms` lookback. The default 45 ms covers the observed delay from command delivery to visible motion with a margin for render timing.
- The learning window accepts continuous steering with a maximum steering range of 0.12 and throttle range of 0.15, provided throttle stays in the same direction. Each command contributes to the control average in proportion to the time it was in force between the two captures.
- Tracking uses forward-backward PyrLK and falls back to ORB descriptor matching for large shifts. The turn-response fit favors well-supported higher gains to reject slowed turns from contact. It selects the densest part of that cluster so short and long capture intervals can both contribute.
- The angular estimate uses `vertical_fov_degrees` and `barrel_distortion`, which default to 70° and 0.12. Set these to the actual camera intrinsics to avoid focal-length scale bias.
