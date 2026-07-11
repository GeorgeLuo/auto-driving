from __future__ import annotations

from autonomy.vehicle import VehicleAction, VehiclePulse

from .types import StartupActionCheckInstruction, StartupActionCheckPlan


def _pulse(
    *,
    label: str,
    forward: bool = False,
    reverse: bool = False,
    steering: float = 0.0,
    throttle: float,
    duration_s: float,
    settle_s: float,
) -> VehiclePulse:
    return VehiclePulse(
        action=VehicleAction(forward=forward, reverse=reverse, steering=steering),
        throttle=throttle,
        duration_s=duration_s,
        settle_s=settle_s,
        recording=False,
        label=label,
    )


def build_basic_startup_action_check_plan(
    *,
    throttle: float = 0.22,
    duration_s: float = 0.3,
    settle_s: float = 0.35,
    steering: float = 0.6,
    frame_endpoint: str = "/frame.jpg",
    include_still_reference: bool = True,
    min_mean_abs_diff_norm: float = 0.008,
    min_changed_pixel_ratio: float = 0.005,
) -> StartupActionCheckPlan:
    """Build the first modular startup plan for command-registration checks."""
    checks: list[StartupActionCheckInstruction] = []
    if include_still_reference:
        checks.append(
            StartupActionCheckInstruction(
                label="still_reference",
                pulse=_pulse(
                    label="still_reference",
                    throttle=0.0,
                    duration_s=0.1,
                    settle_s=settle_s,
                ),
                expect_change=False,
                max_mean_abs_diff_norm=0.05,
                notes=("Measures camera noise and lighting drift without commanding movement.",),
            )
        )

    action_specs = (
        ("forward_center", True, False, 0.0),
        ("reverse_center", False, True, 0.0),
        ("forward_left", True, False, -abs(steering)),
        ("reverse_left", False, True, -abs(steering)),
        ("forward_right", True, False, abs(steering)),
        ("reverse_right", False, True, abs(steering)),
    )
    for label, forward, reverse, steering_value in action_specs:
        checks.append(
            StartupActionCheckInstruction(
                label=label,
                pulse=_pulse(
                    label=label,
                    forward=forward,
                    reverse=reverse,
                    steering=steering_value,
                    throttle=throttle,
                    duration_s=duration_s,
                    settle_s=settle_s,
                ),
                expect_change=True,
                min_mean_abs_diff_norm=min_mean_abs_diff_norm,
                min_changed_pixel_ratio=min_changed_pixel_ratio,
            )
        )

    return StartupActionCheckPlan(
        name="basic_startup_action_check",
        version=1,
        frame_endpoint=frame_endpoint,
        instructions=tuple(checks),
        default_min_mean_abs_diff_norm=min_mean_abs_diff_norm,
        default_min_changed_pixel_ratio=min_changed_pixel_ratio,
        metadata={
            "intent": "Verify that camera capture and basic vehicle action commands are registered.",
            "sequence_shape": "capture_before -> pulse -> capture_after -> image_change_score",
            "paired_actions": [
                ["forward_center", "reverse_center"],
                ["forward_left", "reverse_left"],
                ["forward_right", "reverse_right"],
            ],
        },
    )
