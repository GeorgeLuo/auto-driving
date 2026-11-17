#include "scene/material.hpp"
#include <stdexcept>

namespace sim {
std::array<int,3> rgb_color(const Json& color) {
    if (!color.is_array() || color.size() != 3) throw std::invalid_argument("Expected RGB color");
    for (const auto& channel : color)
        if (!channel.is_number_integer() || channel.get<double>() < 0 || channel.get<double>() > 255)
            throw std::invalid_argument("Color channels must be integers in 0..255");
    return color.get<std::array<int,3>>();
}
}
