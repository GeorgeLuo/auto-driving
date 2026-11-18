#include "server/protocol.hpp"
#include "server/renderers.hpp"
#include "scene/asset_loader.hpp"
#include <cmath>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <sstream>
#include <stdexcept>

using sim::Json;
using sim::World;

static void check(bool condition, const std::string& message) {
    if (!condition) throw std::runtime_error(message);
}

static std::string image_hash(const std::vector<unsigned char>& rgb) {
    // FNV-1a over pre-JPEG RGB bytes, independent of the installed JPEG library.
    uint64_t hash = 14695981039346656037ULL;
    for (auto byte : rgb) hash = (hash ^ byte) * 1099511628211ULL;
    std::ostringstream hex;
    hex << std::hex << std::setfill('0') << std::setw(16) << hash;
    return hex.str();
}

// The two triangles cover the same ray but select different texels. The first
// triangle in the asset owns the tie, regardless of the BVH partition order.
static World coplanar_mesh(Json config, const std::shared_ptr<const sim::AssetCatalog>& assets) {
    auto mesh = std::make_shared<sim::TriangleMesh>();
    mesh->positions = {{1,-1,-1},{1,2,0},{1,-1,1},{1,-1,-1},{1,2,0},{1,-1,1}};
    mesh->triangles = {{0,1,2},{3,4,5}};
    mesh->uv = {{.25,0},{.25,0},{.25,0},{.75,0},{.75,0},{.75,0}};
    sim::Texture texture;
    texture.kind = sim::TextureKind::image;
    texture.image = std::make_shared<const sim::TextureImage>(sim::TextureImage{2,1,{255,0,0,0,0,255}});
    sim::VisualPart part;
    part.shape = sim::Shape::mesh;
    part.mesh = mesh;
    part.embedded_material = sim::Material{{255,255,255},texture};
    auto asset = std::make_shared<sim::Asset>();
    asset->visuals.push_back(part);
    auto catalog = std::make_shared<sim::AssetCatalog>(*assets);
    catalog->objects.emplace("overlap",asset);
    config["map"].erase("obstacles");
    config["map"]["instances"] = {{{"id","overlap"},{"asset","overlap"},{"x",0},{"z",0},{"rotationRadians",0}}};
    config["map"]["light"] = {{"ambient",1},{"directionals", {{{"intensity",0}}}}};
    config["spawn"] = {{"x",0},{"z",0},{"headingRadians",0}};
    config["camera"]["pitchDownRadians"] = 0;
    return World(config,catalog);
}

static Json images(const Json& config, const std::shared_ptr<const sim::AssetCatalog>& assets) {
    const auto renderer = sim::make_renderer("software");
    Json result;
    auto record = [&](const char* name, const World& world, int width, int height) {
        auto capture = sim::capture(world,*renderer,{width,height,.8});
        result[name] = {
            {"rgbFNV1a64",image_hash(sim::render_rgb(world,*renderer,width,height))},
            {"rendererId",capture.at("sensor").at("image").at("rendererId")},
        };
    };
    record("default-spawn",World(config,assets),640,480);
    record("coplanar-mesh",coplanar_mesh(config,assets),1,1);

    auto corner = config;
    corner["spawn"] = {{"x",2.25},{"z",-1.1},{"headingRadians",2.4}};
    record("default-corner",World(corner,assets),161,121);

    auto shadow = config;
    shadow["map"]["obstacles"] = Json::array({
        {{"id","offscreen-caster"},{"x",-.5},{"z",3.25},{"width",.2},{"depth",.4},
         {"height",2},{"rotationRadians",0},{"boundary",false}},
    });
    shadow["camera"].update({{"pitchDownRadians",sim::pi/3},{"barrelDistortion",0},{"vignette",0}});
    record("offscreen-shadow",World(shadow,assets),160,120);

    auto lens = config;
    lens["camera"].update({{"barrelDistortion",.42},{"vignette",.65}});
    record("distortion-and-vignette",World(lens,assets),193,107);
    return result;
}

static Json trajectory(const Json& config, const std::shared_ptr<const sim::AssetCatalog>& assets) {
    auto track = config;
    track["spawn"] = {{"x",.15},{"z",1.5},{"headingRadians",-sim::pi/2}};
    World world(track,assets);
    world.playing = false;
    Json result = Json::array();
    auto drive = [&](int frames, const Json& command) {
        sim::command(world,"set-chaser-input",command);
        for (int i=0;i<frames;++i) {
            sim::command(world,"step",{{"frames",1}});
            result.push_back({world.pose.position.x,world.pose.position.z,world.pose.direction.x,world.pose.direction.z});
        }
    };
    drive(8,{{"motion","forward"},{"throttle",.75}});
    sim::command(world,"configure",{{"vehicle",{{"speedUnitsPerFrame",.25}}}});
    drive(8,{{"motion","forward"},{"steering",.08}});
    drive(8,{{"motion","reverse"},{"steering",-.18},{"throttle",.8}});
    drive(12,{{"motion","reverse"}});
    drive(4,{{"motion","none"},{"steering",1}});
    return result;
}

int main(int argc, char** argv) {
    try {
        check(argc==4,"Expected map, native baseline, and asset paths");
        auto config = sim::read_json(argv[1]);
        auto assets = std::make_shared<const sim::AssetCatalog>(
            sim::load_asset_catalog(argv[3]));
        auto actual_images = images(config,assets);
        auto actual_trajectory = trajectory(config,assets);
        std::ifstream input(argv[2]);
        check(bool(input),"Cannot open native baseline");
        Json baseline; input >> baseline;
        for (auto it = actual_images.begin(); it != actual_images.end(); ++it)
            check(it.value()==baseline.at("images").at(it.key()),"Image changed for " + it.key() + ": " + it.value().dump());
        const auto& expected = baseline.at("trajectory");
        check(actual_trajectory.size()==expected.size(),"Trajectory length changed");
        for (size_t frame=0;frame<expected.size();++frame)
            for (size_t component=0;component<4;++component)
                check(std::abs(actual_trajectory[frame][component].get<double>()-
                               expected[frame][component].get<double>())<1e-12,
                      "Pose changed at frame " + std::to_string(frame+1) + ": " + actual_trajectory[frame].dump());
        std::cout << "PASS: " << actual_images.size() << " image hashes and " << expected.size() << " native poses\n";
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n'; return 1;
    }
}
