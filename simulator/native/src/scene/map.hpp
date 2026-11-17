#pragma once
#include "scene/asset.hpp"

namespace sim {
struct ObjectInstance {
    std::string id;
    Pose2 pose;
    std::shared_ptr<const Asset> asset;
    std::optional<std::string> material;
    std::optional<std::array<int,3>> color;
};

struct DirectionalLight {
    Vec3 direction{-.5,1,.25};
    double shadow_strength = 2.4/(.9+2.4);
    double shadow_softness = .002;
    double intensity = 1;
};

struct Lighting {
    std::vector<DirectionalLight> directionals{DirectionalLight{}};
    std::optional<double> ambient;
};

struct Map {
    double columns, rows;
    std::vector<ObjectInstance> objects;
    Materials materials;
    std::string floor_material = "floor";
    Lighting light;
};
}
