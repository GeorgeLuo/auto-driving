# `vehicle_dynamics` perception plugin

Windowed perception plugin that estimates yaw and longitudinal motion from consecutive front-camera frames and learns the vehicle's response to applied autonomous controls.

## Signals

- `visual_yaw_rate_rad_s` reports visual yaw rate. Its `motion_blocked` property flags a dead stall under throttle. `motion_impeded` also flags a sufficiently slow or opposite-sign turn relative to the prior turn fit, or a near-straight interval with strong throttle and near-zero or opposite-direction expansion relative to the learned throttle polarity. For turns with a supported prediction, `expected_yaw_rate_rad_s` is the predicted yaw rate and `motion_ratio` is observed yaw divided by predicted yaw. The ratio is available for predicted magnitudes of at least 0.40 rad/s. These diagnostics require settled autonomous controls and confident visual evidence.
- `turn_response_available` reports the learned turn response to commanded throttle and steering. Partial-contact diagnostics leave observations eligible for fitting so the model can recover when the vehicle's response changes.
- `visual_expansion_rate_per_s` reports median radial log-expansion from the existing inlier tracks, after leveling pitch and derotating into the mid-arc chord frame. `motion_direction` is `forward`, `reverse`, `stationary` near zero, or `unknown` when the estimate abstains. `forward_time_to_contact_s` estimates time remaining at the latest capture from the upper quartile of positive expansion in the forward corridor. It assumes continued motion and is available only for supported forward approaches within 20 seconds.
- The expansion signal reports `throttle_sign` as `normal` when positive throttle drives forward, `inverted` when it drives in reverse, or `unknown` before the votes agree. `throttle_sample_count` and `throttle_confidence` describe the bounded window of informative observations. Straight driving contributes to this fit.

The plugin publishes expansion, time to contact, throttle polarity, and contact diagnostics in `measurements["vehicle_dynamics"]`.

## Contract & conventions

- `FRONT_CAMERA_RGB_INPUT` supplies the images. The plugin reads applied commands from `action.control_applications` in host shared memory.
- Positive steering commands and observed yaw point right. The sign of `gain_per_s` and `turn_sign` describe yaw relative to the combination of commanded steering and throttle.
- Camera-only motion estimation works in manual and observe-only modes. Learning requires visual motion and a continuous history of applied autonomous commands through the image pair and the preceding `command_settle_ms` lookback. The default 45 ms covers the observed delay from command delivery to visible motion with a margin for render timing.
- The learning window accepts continuous steering with a maximum steering range of 0.12 and throttle range of 0.15, provided throttle stays in the same direction. Each command contributes to the control average in proportion to the time it was in force between the two captures.
- Tracking uses forward-backward PyrLK and falls back to ORB descriptor matching for large shifts. The turn-response fit favors well-supported higher gains to reject slowed turns from contact. It selects the densest part of that cluster so short and long capture intervals can both contribute.
- The angular estimate uses `vertical_fov_degrees` and `barrel_distortion`, which default to 70° and 0.12. A common focal scale cancels in the ratio of chord-frame radii. Intrinsic errors can still affect pitch and yaw recovery and which rays pass the corridor gates.
