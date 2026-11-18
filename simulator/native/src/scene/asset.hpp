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
enum class TextureKind { none, grain, carpet, cardboard, leather, wood, fabric, patterned_rug, image };
enum class TextureWrap { repeat, clamp, mirror };

struct TextureImage {
    int width, height;
    std::vector<uint8_t> rgb;
};

struct Texture {
    TextureKind kind = TextureKind::none;
    uint32_t seed = 0;
    double scale = 1;
    double strength = 1;
    bool print = false, tape = false;
    std::shared_ptr<const TextureImage> image;
    TextureWrap wrap_u = TextureWrap::repeat, wrap_v = TextureWrap::repeat;
};

struct Material {
    std::array<int,3> base_color;
    Texture texture;
};

struct UV { double u = 0, v = 0; };

struct TriangleMesh {
    std::vector<Vec3> positions;
    std::vector<UV> uv;
    std::vector<std::array<uint32_t,3>> triangles;
};

enum class Shape { box, cylinder, ellipsoid, strip, mesh };

struct VisualPart {
    Vec3 offset;
    double width = 0, depth = 0, height = 0, rotation = 0;
    std::string material;
    Shape shape = Shape::box;
    double pitch = 0, roll = 0;
    std::shared_ptr<const TriangleMesh> mesh;
    std::optional<Material> embedded_material;
};

struct CollisionBox {
    double width, depth;
};

struct Asset {
    std::vector<VisualPart> visuals;
    std::optional<CollisionBox> collision;
};

using Materials = std::unordered_map<std::string,Material>;

struct AssetCatalog {
    Materials materials;
    std::unordered_map<std::string,std::shared_ptr<const Asset>> objects;
};
}
