#pragma once
#include "scene/map.hpp"

namespace sim {
struct Input {
    bool forward = false, reverse = false;
    double steering = 0, throttle = 1;
};

struct VehicleSettings {
    double speed, wheelbase, radius, max_steering_angle;
};

struct VehiclePose {
    Vec position, direction;
};

struct StepOutcome {
    VehiclePose pose;
    double intended_distance = 0;
    double signed_distance = 0;
    double yaw_delta = 0;
    double travel_fraction = 1;
    bool contact_limited = false;
};

void validate_vehicle_pose(Vec position, double radius, const Map& map);
StepOutcome step_vehicle_detailed(VehiclePose pose, const Input& input, const VehicleSettings& vehicle, const Map& map);
VehiclePose step_vehicle(VehiclePose pose, const Input& input, const VehicleSettings& vehicle, const Map& map);
}
