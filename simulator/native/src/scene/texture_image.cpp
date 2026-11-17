#include "scene/material.hpp"
#define STB_IMAGE_IMPLEMENTATION
#define STBI_MAX_DIMENSIONS 8192
#include <stb_image.h>
#include <limits>
#include <stdexcept>

namespace sim {
namespace {
std::shared_ptr<const TextureImage> copy_image(stbi_uc* pixels, int width, int height) {
    std::unique_ptr<stbi_uc,decltype(&stbi_image_free)> owned(pixels,stbi_image_free);
    if (!owned) throw std::invalid_argument(std::string("Cannot decode texture image: ") + stbi_failure_reason());
    return std::make_shared<const TextureImage>(
        TextureImage{width,height,{pixels,pixels+size_t(width)*height*3}});
}
}

std::shared_ptr<const TextureImage> load_texture_image(const std::filesystem::path& source) {
    int width = 0, height = 0;
    auto* pixels = stbi_load(source.string().c_str(),&width,&height,nullptr,STBI_rgb);
    try {
        return copy_image(pixels,width,height);
    } catch (const std::invalid_argument& error) {
        throw std::invalid_argument(source.string() + ": " + error.what());
    }
}

std::shared_ptr<const TextureImage> load_texture_image(const uint8_t* data, size_t size) {
    if (size > std::numeric_limits<int>::max()) throw std::invalid_argument("Texture image is too large");
    int width = 0, height = 0;
    auto* pixels = stbi_load_from_memory(data,static_cast<int>(size),&width,&height,nullptr,STBI_rgb);
    return copy_image(pixels,width,height);
}
}
