#include "scene/asset_loader.hpp"
#include "scene/asset_json.hpp"
#include "scene/material.hpp"
#include <stdexcept>

namespace sim {
namespace {
VisualPart read_part(const Json& json) {
    require_keys(json,{"type","x","y","z","width","depth","height","rotationRadians",
                       "pitchRadians","rollRadians","material"});
    static const std::unordered_map<std::string,Shape> shapes{
        {"box",Shape::box}, {"cylinder",Shape::cylinder}, {"ellipsoid",Shape::ellipsoid},
        {"strip",Shape::strip},
    };
    auto type = json.at("type").get<std::string>();
    auto shape = shapes.find(type);
    if (shape == shapes.end()) throw std::invalid_argument("Unknown visual part type: " + type);
    VisualPart part;
    part.shape = shape->second;
    part.offset = {number(json,"x",-1000,1000),number(json,"y",-1000,1000),number(json,"z",-1000,1000)};
    part.width = number(json,"width",.001,2000);
    part.depth = number(json,"depth",.001,2000);
    part.height = number(json,"height",.001,1000);
    part.rotation = number(json,"rotationRadians",-2*pi,2*pi);
    if (json.contains("pitchRadians")) part.pitch = number(json,"pitchRadians",-2*pi,2*pi);
    if (json.contains("rollRadians")) part.roll = number(json,"rollRadians",-2*pi,2*pi);
    part.material = json.at("material").get<std::string>();
    return part;
}
}

std::optional<CollisionBox> read_collision(const Json& json) {
    if (json.is_null()) return std::nullopt;
    require_keys(json,{"type","width","depth"});
    if (json.at("type") != "box") throw std::invalid_argument("Unknown collision shape type");
    return CollisionBox{number(json,"width",.001,2000),number(json,"depth",.001,2000)};
}

bool JsonAssetLoader::supports(const std::filesystem::path& source) const {
    return source.extension() == ".json";
}

Asset JsonAssetLoader::load(const std::filesystem::path& source) const {
    const auto json = read_json(source);
    require_keys(json,{"visuals","collision"});
    if (!json.at("visuals").is_array()) throw std::invalid_argument("Expected an array of visual parts");
    Asset asset;
    for (const auto& part : json.at("visuals")) asset.visuals.push_back(read_part(part));
    asset.collision = read_collision(json.at("collision"));
    return asset;
}

AssetCatalog load_asset_catalog(const std::filesystem::path& directory) {
    const JsonAssetLoader json_loader;
    const GltfAssetLoader gltf_loader;
    const std::array<const AssetLoader*,2> loaders{&json_loader,&gltf_loader};
    AssetCatalog catalog;
    const auto materials = read_json(directory/"materials.json");
    if (!materials.is_object()) throw std::invalid_argument("Expected named materials");
    for (const auto& [name,json] : materials.items()) {
        require_keys(json,{"baseColor","texture"});
        catalog.materials.emplace(name,Material{rgb_color(json.at("baseColor")),
                                               read_texture(json.at("texture"),directory)});
    }
    for (const auto* name : {"floor","obstacle","wall","sky"})
        if (!catalog.materials.count(name))
            throw std::invalid_argument("Missing material: " + std::string(name));

    for (const auto& entry : std::filesystem::directory_iterator(directory/"objects")) {
        if (!entry.is_regular_file()) continue;
        const auto& source = entry.path();
        for (const auto* loader : loaders) {
            if (!loader->supports(source)) continue;
            try {
                auto asset = loader->load(source);
                for (const auto& part : asset.visuals) {
                    if (!part.embedded_material && !catalog.materials.count(part.material))
                        throw std::invalid_argument("Unknown material: " + part.material);
                }
                if (!catalog.objects.emplace(source.stem().string(),std::make_shared<const Asset>(std::move(asset))).second)
                    throw std::invalid_argument("Duplicate asset name: " + source.stem().string());
            } catch (const std::exception& error) {
                throw std::invalid_argument(source.string() + ": " + error.what());
            }
            break;
        }
    }
    return catalog;
}
}
