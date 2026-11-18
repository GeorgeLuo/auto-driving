#pragma once
#include "render/software/geometry.hpp"

namespace sim::software {
std::array<double,3> texture_color(const Material& material, const std::array<int,3>& base,
                                   const Surface& surface, Vec3 view_ray, double brightness);
}
