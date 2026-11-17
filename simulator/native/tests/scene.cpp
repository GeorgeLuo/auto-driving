#include "scene/asset_loader.hpp"
#include "server/protocol.hpp"
#include "server/renderers.hpp"
#include <iostream>
#include <stdexcept>

using sim::Json;

static void check(bool condition, const char* message) {
    if (!condition) throw std::runtime_error(message);
}

static Json named_map(Json config) {
    auto& map = config.at("map");
    map["instances"] = Json::array();
    for (const auto& obstacle : map.at("obstacles")) {
        auto id = obstacle.at("id").get<std::string>();
        auto asset = id;
        if (obstacle.at("boundary").get<bool>())
            asset = id == "room-wall-north" || id == "room-wall-south" ? "room-wall-horizontal" : "room-wall-vertical";
        map["instances"].push_back({{"asset",asset},{"id",id},{"x",obstacle.at("x")},
            {"z",obstacle.at("z")},{"rotationRadians",obstacle.at("rotationRadians")}});
    }
    map.erase("obstacles");
    return config;
}

static void check_assets(const Json& config, const std::shared_ptr<const sim::AssetCatalog>& catalog,
                         const sim::Renderer& renderer) {
    sim::World legacy(config,catalog);
    sim::World named(named_map(config),catalog);
    auto pixels = sim::render_rgb(legacy,renderer,160,120);
    check(pixels == sim::render_rgb(named,renderer,160,120),"Named assets reproduce legacy visuals");

    auto visual_only_catalog = std::make_shared<sim::AssetCatalog>(*catalog);
    auto block = std::make_shared<sim::Asset>(*catalog->objects.at("middle-medium-block"));
    block->collision.reset();
    visual_only_catalog->objects.at("middle-medium-block") = block;
    sim::World visual_only(named_map(config),visual_only_catalog);
    check(pixels == sim::render_rgb(visual_only,renderer,160,120),"Collision changes preserve visuals");

    auto collision_only_catalog = std::make_shared<sim::AssetCatalog>(*catalog);
    auto hidden_block = std::make_shared<sim::Asset>(*catalog->objects.at("middle-medium-block"));
    hidden_block->visuals.clear();
    collision_only_catalog->objects.at("middle-medium-block") = hidden_block;
    sim::World collision_only(named_map(config),collision_only_catalog);
    check(pixels != sim::render_rgb(collision_only,renderer,160,120),"Visual changes affect pixels");

    auto drive = [](sim::World& world) {
        world.playing = false;
        sim::command(world,"set-chaser-input",{{"motion","forward"}});
        sim::command(world,"step",{{"frames",120}});
        return world.native_state().at("pose");
    };
    auto pose = drive(legacy);
    check(pose == drive(named),"Named assets reproduce legacy contact");
    check(pose == drive(collision_only),"Visual changes preserve contact");
    check(pose != drive(visual_only),"A visual-only object does not block the car");
}

class FlatRenderer final : public sim::Renderer {
public:
    std::string_view id() const override { return "test-flat"; }
    sim::LinearRgbImage render(const sim::Map&, const sim::CameraPose&, sim::ImageSize size) const override {
        return {size,std::vector<double>(static_cast<size_t>(size.width)*size.height*3,100.75)};
    }
};

int main(int argc, char** argv) {
    try {
        check(argc==3,"Expected map and asset paths");
        auto config = sim::read_config(argv[1]);
        auto assets = std::make_shared<const sim::AssetCatalog>(
            sim::load_asset_catalog(argv[2],sim::JsonAssetLoader{}));
        check_assets(config,assets,*sim::make_renderer("software"));

        auto shaded = config;
        shaded["map"]["light"] = {{"shadowStrength",0}};
        sim::World with_shadows(config,assets), without_shadows(shaded,assets);
        auto software = sim::make_renderer("software");
        check(sim::render_rgb(with_shadows,*software,160,120) != sim::render_rgb(without_shadows,*software,160,120),
              "Map light controls shadows");

        FlatRenderer flat;
        auto rgb = sim::render_rgb(with_shadows,flat,161,121);
        check(rgb[0]==90 && rgb[(60*161+80)*3]==100,"Camera effects apply to any renderer's linear RGB");
        auto capture = sim::capture(with_shadows,flat,{161,121,.8});
        check(capture.at("sensor").at("image").at("rendererId")=="test-flat","Capture uses selected renderer ID");
        std::cout << "PASS: asset geometry, map lighting, and camera pipeline\n";
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n'; return 1;
    }
}
