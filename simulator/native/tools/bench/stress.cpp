#include "stress.hpp"

namespace bench {
namespace {
constexpr double scale = .22;

std::shared_ptr<const sim::Asset> scaled_asset(const sim::Asset& source) {
    auto asset = std::make_shared<sim::Asset>(source);
    for (auto& part : asset->visuals) {
        part.offset = {part.offset.x*scale,part.offset.y*scale,part.offset.z*scale};
        part.width *= scale;
        part.height *= scale;
        part.depth *= scale;
        if (part.mesh) {
            auto mesh = std::make_shared<sim::TriangleMesh>(*part.mesh);
            for (auto& point : mesh->positions)
                point = {point.x*scale,point.y*scale,point.z*scale};
            part.mesh = std::move(mesh);
        }
    }
    if (asset->collision) {
        asset->collision->width *= scale;
        asset->collision->depth *= scale;
    }
    return asset;
}
}

sim::Map stress_map(const sim::World& showcase) {
    const auto& original = *showcase.configuration.map;
    sim::Map result = original;
    const auto& obstacles = showcase.configuration.document.at("map").at("obstacles");
    std::unordered_map<const sim::Asset*,std::shared_ptr<const sim::Asset>> scaled;
    int group = 0;
    // Keep the full-size showcase and its camera circuit. Place four small groups
    // of the same props in the free space at the room's corners.
    for (double x : {-3.33,3.33}) for (double z : {-3.19,3.19}) {
        ++group;
        for (size_t i=0;i<original.objects.size();++i) {
            if (i < obstacles.size() && obstacles[i].at("boundary").get<bool>()) continue;
            auto object = original.objects[i];
            auto& asset = scaled[object.asset.get()];
            if (!asset) asset = scaled_asset(*object.asset);
            object.asset = asset;
            object.id += "-stress-" + std::to_string(group);
            object.pose.position = {x+object.pose.position.x*scale,z+object.pose.position.z*scale};
            result.objects.push_back(std::move(object));
        }
    }
    return result;
}
}
