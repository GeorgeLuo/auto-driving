#include "scene/asset_loader.hpp"
#include "scene/material.hpp"
#include "core/json.hpp"
#include <fstream>
#include <stdexcept>

namespace sim {
namespace {
Json read_asset_json(const std::filesystem::path& source) {
    std::ifstream input(source);
    if (!input) throw std::invalid_argument("Cannot open asset: " + source.string());
    Json result; input >> result; return result;
}

Texture read_texture(const Json& json) {
    const auto& kind = json.at("kind");
    if (kind == "none") {
        require_keys(json,{"kind"});
        return {};
    }
    if (kind == "grain") {
        require_keys(json,{"kind","seed","scale"});
        if (!json.at("seed").is_number_unsigned()) throw std::invalid_argument("seed must be unsigned");
        return {TextureKind::grain,static_cast<uint32_t>(number(json,"seed",0,4294967295.0)),
                number(json,"scale",.01,100)};
    }
    throw std::invalid_argument("Unknown procedural texture: " + kind.dump());
}
}

bool JsonAssetLoader::supports(const std::filesystem::path& source) const {
    return source.extension() == ".json";
}

Asset JsonAssetLoader::load(const std::filesystem::path& source) const {
    const auto json = read_asset_json(source);
    require_keys(json,{"visuals","collision"});
    if (!json.at("visuals").is_array()) throw std::invalid_argument("Expected an array of visual parts");
    Asset asset;
    for (const auto& part : json.at("visuals")) {
        require_keys(part,{"type","x","y","z","width","depth","height","rotationRadians","material"});
        if (part.at("type") != "box") throw std::invalid_argument("Unknown visual part type");
        asset.visuals.push_back({
            {number(part,"x",-1000,1000),number(part,"y",-1000,1000),number(part,"z",-1000,1000)},
            number(part,"width",.001,2000),number(part,"depth",.001,2000),number(part,"height",.001,1000),
            number(part,"rotationRadians",-2*pi,2*pi),part.at("material").get<std::string>(),
        });
    }
    const auto& collision = json.at("collision");
    if (!collision.is_null()) {
        require_keys(collision,{"type","width","depth"});
        if (collision.at("type") != "box") throw std::invalid_argument("Unknown collision shape type");
        asset.collision = CollisionBox{number(collision,"width",.001,2000),number(collision,"depth",.001,2000)};
    }
    return asset;
}

AssetCatalog load_asset_catalog(const std::filesystem::path& directory, const AssetLoader& loader) {
    AssetCatalog catalog;
    const auto materials = read_asset_json(directory/"materials.json");
    if (!materials.is_object()) throw std::invalid_argument("Expected named materials");
    for (const auto& [name,json] : materials.items()) {
        require_keys(json,{"baseColor","texture"});
        catalog.materials.emplace(name,Material{rgb_color(json.at("baseColor")),read_texture(json.at("texture"))});
    }
    for (const auto* name : {"floor","obstacle","wall","sky"})
        if (!catalog.materials.count(name))
            throw std::invalid_argument("Missing material: " + std::string(name));
    for (const auto& entry : std::filesystem::directory_iterator(directory/"objects")) {
        const auto& source = entry.path();
        if (!loader.supports(source) || !entry.is_regular_file()) continue;
        auto asset = loader.load(source);
        for (const auto& part : asset.visuals)
            if (!catalog.materials.count(part.material))
                throw std::invalid_argument("Unknown material in " + source.string() + ": " + part.material);
        catalog.objects.emplace(source.stem().string(),std::make_shared<const Asset>(std::move(asset)));
    }
    return catalog;
}
}
