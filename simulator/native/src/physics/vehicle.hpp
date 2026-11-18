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

void validate_vehicle_pose(Vec position, double radius, const Map& map);
VehiclePose step_vehicle(VehiclePose pose, const Input& input, const VehicleSettings& vehicle, const Map& map);
}
