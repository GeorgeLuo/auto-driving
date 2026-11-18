#pragma once

namespace sim {
constexpr double pi = 3.14159265358979323846;

struct Vec { double x = 0, z = 0; };
struct Vec3 { double x = 0, y = 0, z = 0; };

struct Pose2 {
    Vec position;
    double rotation;
};

inline Vec rotate(Vec point, double cosine, double sine) {
    return {point.x*cosine + point.z*sine, -point.x*sine + point.z*cosine};
}
}
