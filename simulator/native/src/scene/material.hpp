#pragma once
#include "core/json.hpp"
#include "scene/asset.hpp"
#include <filesystem>

namespace sim {
std::array<int,3> rgb_color(const Json& color);
Texture read_texture(const Json& json, const std::filesystem::path& directory);
std::shared_ptr<const TextureImage> load_texture_image(const std::filesystem::path& source);
std::shared_ptr<const TextureImage> load_texture_image(const uint8_t* data, size_t size);
}
