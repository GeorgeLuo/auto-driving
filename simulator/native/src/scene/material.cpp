#include "scene/material.hpp"
#include <stdexcept>

namespace sim {
std::array<int,3> rgb_color(const Json& color) {
    if (!color.is_array() || color.size() != 3) throw std::invalid_argument("Expected RGB color");
    for (const auto& channel : color)
        if (!channel.is_number_integer() || channel.get<double>() < 0 || channel.get<double>() > 255)
            throw std::invalid_argument("Color channels must be integers in 0..255");
    return color.get<std::array<int,3>>();
}

Texture read_texture(const Json& json, const std::filesystem::path& directory) {
    const auto kind = json.at("kind").get<std::string>();
    Texture texture;
    if (kind == "none") {
        require_keys(json,{"kind"});
        return texture;
    }
    if (kind == "image") {
        require_keys(json,{"kind","file","scale"});
        texture.kind = TextureKind::image;
        if (json.contains("scale")) texture.scale = number(json,"scale",.01,100);
        texture.image = load_texture_image(directory/json.at("file").get<std::string>());
        return texture;
    }

    static const std::unordered_map<std::string,TextureKind> kinds{
        {"grain",TextureKind::grain}, {"carpet",TextureKind::carpet},
        {"cardboard",TextureKind::cardboard}, {"leather",TextureKind::leather},
        {"wood",TextureKind::wood}, {"fabric",TextureKind::fabric},
        {"patterned_rug",TextureKind::patterned_rug},
    };
    auto found = kinds.find(kind);
    if (found == kinds.end()) throw std::invalid_argument("Unknown texture kind: " + kind);
    texture.kind = found->second;
    if (texture.kind == TextureKind::grain) {
        require_keys(json,{"kind","seed","scale"});
    } else if (texture.kind == TextureKind::cardboard) {
        require_keys(json,{"kind","seed","scale","strength","print","tape"});
        texture.print = json.value("print",false);
        texture.tape = json.value("tape",false);
    } else {
        require_keys(json,{"kind","seed","scale","strength"});
    }
    if (!json.at("seed").is_number_unsigned()) throw std::invalid_argument("seed must be unsigned");
    texture.seed = static_cast<uint32_t>(number(json,"seed",0,4294967295.0));
    texture.scale = number(json,"scale",.01,100);
    if (json.contains("strength")) texture.strength = number(json,"strength",0,1);
    return texture;
}
}
