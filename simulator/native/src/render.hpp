#pragma once
#include "world.hpp"
#include <vector>

namespace sim {
struct ImageRequest { int width = 640, height = 480; double quality = .8; };
ImageRequest image_request(const Json& options);
std::vector<unsigned char> render_rgb(const World& world, int width, int height);
std::vector<unsigned char> encode_jpeg(const std::vector<unsigned char>& rgb, const ImageRequest& request);
std::string base64(const std::vector<unsigned char>& bytes);
Json sensor_image(const World& world, const ImageRequest& request);
}
