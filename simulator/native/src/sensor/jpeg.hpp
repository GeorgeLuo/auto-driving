#pragma once
#include "core/image.hpp"

namespace sim {
std::vector<unsigned char> encode_jpeg(const std::vector<unsigned char>& rgb, ImageSize size, double quality);
}
