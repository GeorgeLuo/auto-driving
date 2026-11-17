#pragma once
#include "scene/asset.hpp"

namespace sim {
struct ObjectInstance {
    std::string id;
    Pose2 pose;
    std::shared_ptr<const Asset> asset;
};

struct Light {
    Vec3 direction{-.5,1,.25};
    double shadow_strength = 2.4/(.9+2.4);
};

struct Map {
    double columns, rows;
    std::vector<ObjectInstance> objects;
    Materials materials;
    std::string floor_material = "floor";
    Light light;
};
}
