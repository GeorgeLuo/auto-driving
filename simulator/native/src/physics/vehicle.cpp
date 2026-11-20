#include "physics/vehicle.hpp"
#include <algorithm>
#include <array>
#include <cmath>
#include <stdexcept>

namespace sim {
static double step_yaw(double distance, double steering, double max_angle, double wheelbase) {
    if (std::abs(distance) > .001 && steering != 0 && max_angle > 0)
        return distance / wheelbase * std::tan(steering * max_angle);
    return 0;
}
static Vec vehicle_motion(Vec position, Vec direction, double distance, double steering, double max_angle,
                   double wheelbase, Vec& next_direction) {
    next_direction = direction;
    Vec travel = direction;
    double yaw = step_yaw(distance, steering, max_angle, wheelbase);
    if (yaw != 0) {
        double angle = std::atan2(direction.z, direction.x);
        // Chase measures heading atan2(x,z); in our atan2(z,x) representation
        // positive Chase steering subtracts yaw (left when facing north).
        travel = {std::cos(angle - yaw/2), std::sin(angle - yaw/2)};
        next_direction = {std::cos(angle - yaw), std::sin(angle - yaw)};
    }
    return {position.x + travel.x*distance, position.z + travel.z*distance};
}
static Vec local(Vec p, const Pose2& pose) {
    double x = p.x-pose.position.x, z = p.z-pose.position.z;
    double c = std::cos(pose.rotation), s = std::sin(pose.rotation);
    return {x*c-z*s, x*s+z*c};
}
static double entry_fraction(Vec start, Vec end, double width, double depth) {
    double entry = -INFINITY, exit = INFINITY;
    for (auto axis : {std::array<double,3>{start.x, end.x-start.x, width/2},
                      std::array<double,3>{start.z, end.z-start.z, depth/2}}) {
        auto [a, delta, half] = axis;
        if (std::abs(delta) <= 1e-12) {
            if (a <= -half+1e-12 || a >= half-1e-12) return 1;
            continue;
        }
        double first = (-half-a)/delta, second = (half-a)/delta;
        entry = std::max(entry, std::min(first,second));
        exit = std::min(exit, std::max(first,second));
    }
    if (entry >= exit-1e-12 || exit <= 1e-12 || entry > 1) return 1;
    return std::max(0.0,entry);
}
static double contact_fraction(Vec end, Vec start, double radius, const Map& map) {
    double fraction = 1;
    for (auto axis : {std::array<double,3>{start.x, end.x, map.columns/2-radius},
                      std::array<double,3>{start.z, end.z, map.rows/2-radius}}) {
        auto [a,b,half] = axis;
        if (b < -half && b < a) fraction = std::min(fraction,(-half-a)/(b-a));
        else if (b > half && b > a) fraction = std::min(fraction,(half-a)/(b-a));
    }
    for (const auto& object : map.objects) {
        const auto& shape = object.asset->collision;
        if (!shape) continue;
        fraction = std::min(fraction,entry_fraction(local(start,object.pose),local(end,object.pose),
                                                    shape->width+2*radius,shape->depth+2*radius));
    }
    return std::max(0.0,fraction);
}
void validate_vehicle_pose(Vec position, double radius, const Map& map) {
    // Swept contact can land a rounding unit past the exact boundary.
    if (std::abs(position.x) > map.columns/2-radius+1e-12 || std::abs(position.z) > map.rows/2-radius+1e-12)
        throw std::invalid_argument("Vehicle pose is outside the map");
    for (const auto& object : map.objects) {
        const auto& shape = object.asset->collision;
        if (!shape) continue;
        auto point = local(position,object.pose);
        if (std::abs(point.x) < shape->width/2+radius-1e-12 &&
            std::abs(point.z) < shape->depth/2+radius-1e-12)
            throw std::invalid_argument("Vehicle pose intersects obstacle " + object.id);
    }
}

StepOutcome step_vehicle_detailed(VehiclePose pose, const Input& input, const VehicleSettings& vehicle, const Map& map) {
    auto [position,direction] = pose;
    double sign = input.forward ? 1 : input.reverse ? -1 : 0;
    double distance = vehicle.speed * input.throttle * sign;
    Vec next_direction;
    Vec intended = vehicle_motion(position,direction,distance,input.steering,
                                  vehicle.max_steering_angle,vehicle.wheelbase,next_direction);
    double swept_fraction = contact_fraction(intended,position,vehicle.radius,map);
    Vec next{position.x+(intended.x-position.x)*swept_fraction,
             position.z+(intended.z-position.z)*swept_fraction};
    double full = std::hypot(intended.x-position.x,intended.z-position.z);
    double traveled = std::hypot(next.x-position.x,next.z-position.z);
    double fraction = full > 0 ? std::min(1.0,traveled/full) : 0;
    double applied_distance = distance;
    if (fraction == 0) {
        next_direction = direction;
        applied_distance = 0;
    } else if (fraction < 1) {
        applied_distance = distance * fraction;
        vehicle_motion(position,direction,applied_distance,input.steering,
                       vehicle.max_steering_angle,vehicle.wheelbase,next_direction);
    }
    bool contact_limited = full > 0 && swept_fraction < 1;
    double yaw = step_yaw(applied_distance,input.steering,vehicle.max_steering_angle,vehicle.wheelbase);
    double yaw_delta = yaw == 0 ? 0.0 : -yaw;
    return {{next,next_direction},distance,applied_distance,yaw_delta,full > 0 ? swept_fraction : 1.0,contact_limited};
}

VehiclePose step_vehicle(VehiclePose pose, const Input& input, const VehicleSettings& vehicle, const Map& map) {
    return step_vehicle_detailed(pose,input,vehicle,map).pose;
}
}
