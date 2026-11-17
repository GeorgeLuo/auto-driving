#pragma once
#include "core/json.hpp"
#include <array>

namespace sim {
std::array<int,3> rgb_color(const Json& color);
}
