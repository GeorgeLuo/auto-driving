#pragma once
#include "core/math.hpp"
#include <array>
#include <cstdint>
#include <memory>
#include <optional>
#include <string>
#include <unordered_map>
#include <vector>

namespace sim {
enum class TextureKind { none, grain };

struct Texture {
    TextureKind kind = TextureKind::none;
    uint32_t seed = 0;
    double scale = 1;
};

struct Material {
    std::array<int,3> base_color;
    Texture texture;
};

struct VisualBox {
    Vec3 offset;
    double width, depth, height, rotation;
    std::string material;
};

struct CollisionBox {
    double width, depth;
};

struct Asset {
    std::vector<VisualBox> visuals;
    std::optional<CollisionBox> collision;
};

using Materials = std::unordered_map<std::string,Material>;

struct AssetCatalog {
    Materials materials;
    std::unordered_map<std::string,std::shared_ptr<const Asset>> objects;
};
}
