#include "scene/map_loader.hpp"
#include "scene/material.hpp"
#include <stdexcept>
#include <utility>

namespace sim {
namespace {
void add_obstacles(Map& map, const Json& obstacles) {
    if (!obstacles.is_array() || obstacles.size() > 1000)
        throw std::invalid_argument("Expected at most 1000 obstacles");
    for (const auto& b : obstacles) {
        require_keys(b,{"id","x","z","width","depth","height","rotationRadians","boundary"});
        auto id = b.at("id").get<std::string>();
        bool boundary = b.at("boundary").get<bool>();
        double x = number(b,"x",-1000,1000), z = number(b,"z",-1000,1000);
        double width = number(b,"width",.001,2000), depth = number(b,"depth",.001,2000);
        double height = number(b,"height",.001,1000), rotation = number(b,"rotationRadians",-2*pi,2*pi);
        auto asset = std::make_shared<Asset>();
        VisualPart part;
        part.width = width;
        part.depth = depth;
        part.height = height;
        part.material = boundary ? "wall" : "obstacle";
        asset->visuals.push_back(std::move(part));
        if (!boundary) asset->collision = CollisionBox{width,depth};
        map.objects.push_back({std::move(id),{{x,z},rotation},std::move(asset),{},{}});
    }
}

void add_instances(Map& map, const Json& instances, const AssetCatalog& assets) {
    if (!instances.is_array() || instances.size() > 1000-map.objects.size())
        throw std::invalid_argument("Expected at most 1000 map objects");
    for (const auto& instance : instances) {
        require_keys(instance,{"id","asset","x","z","rotationRadians","material","color"});
        auto name = instance.at("asset").get<std::string>();
        auto asset = assets.objects.find(name);
        if (asset == assets.objects.end()) throw std::invalid_argument("Unknown asset: " + name);
        ObjectInstance object{
            instance.at("id").get<std::string>(),
            {{number(instance,"x",-1000,1000),number(instance,"z",-1000,1000)},
             number(instance,"rotationRadians",-2*pi,2*pi)},
            asset->second,{},{},
        };
        if (instance.contains("material")) {
            object.material = instance.at("material").get<std::string>();
            if (!map.materials.count(*object.material))
                throw std::invalid_argument("Unknown instance material: " + *object.material);
        }
        if (instance.contains("color")) object.color = rgb_color(instance.at("color"));
        map.objects.push_back(std::move(object));
    }
}

DirectionalLight read_directional(const Json& json) {
    DirectionalLight light;
    if (json.contains("direction")) {
        const auto& direction = json.at("direction");
        require_keys(direction,{"x","y","z"});
        light.direction = {number(direction,"x",-1000,1000),number(direction,"y",.001,1000),
                           number(direction,"z",-1000,1000)};
    }
    if (json.contains("shadowStrength")) light.shadow_strength = number(json,"shadowStrength",0,1);
    if (json.contains("shadowSoftness")) light.shadow_softness = number(json,"shadowSoftness",0,1);
    if (json.contains("intensity")) light.intensity = number(json,"intensity",0,4);
    return light;
}

Lighting read_lighting(const Json& json) {
    if (!json.contains("directionals") && !json.contains("ambient")) {
        require_keys(json,{"direction","shadowStrength","shadowSoftness"});
        return {{read_directional(json)},std::nullopt};
    }
    require_keys(json,{"ambient","directionals"});
    Lighting lighting;
    lighting.ambient = json.contains("ambient") ? number(json,"ambient",0,4) : .4;
    if (json.contains("directionals")) {
        const auto& lights = json.at("directionals");
        if (!lights.is_array() || lights.empty() || lights.size() > 8)
            throw std::invalid_argument("Expected 1 to 8 directional lights");
        lighting.directionals.clear();
        for (const auto& light : lights) {
            require_keys(light,{"direction","shadowStrength","shadowSoftness","intensity"});
            lighting.directionals.push_back(read_directional(light));
        }
    }
    return lighting;
}
}

Map load_map(const Json& json, const AssetCatalog& assets) {
    require_keys(json,{"columns","rows","obstacles","instances","light","floorMaterial"});
    Map map{number(json,"columns",1,1000),number(json,"rows",1,1000),{},assets.materials,"floor",{}};
    if (json.contains("obstacles") || !json.contains("instances")) add_obstacles(map,json.at("obstacles"));
    if (json.contains("instances")) add_instances(map,json.at("instances"),assets);
    if (json.contains("light")) map.light = read_lighting(json.at("light"));
    if (json.contains("floorMaterial")) map.floor_material = json.at("floorMaterial").get<std::string>();
    if (!map.materials.count(map.floor_material))
        throw std::invalid_argument("Unknown floor material: " + map.floor_material);
    return map;
}

void apply_legacy_materials(Map& map, const Json& materials) {
    require_keys(materials,{"floor","obstacle","wall","seed","textureScale"});
    for (const auto* key : {"floor","obstacle","wall"})
        map.materials.at(key).base_color = rgb_color(materials.at(key));
    if (!materials.at("seed").is_number_unsigned()) throw std::invalid_argument("seed must be unsigned");
    auto seed = static_cast<uint32_t>(number(materials,"seed",0,4294967295.0));
    double scale = number(materials,"textureScale",.01,100);
    for (const auto* key : {"floor","obstacle"}) {
        auto& texture = map.materials.at(key).texture;
        texture.seed = seed;
        texture.scale = scale;
    }
}
}
