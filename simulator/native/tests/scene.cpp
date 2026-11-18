#include "scene/asset_loader.hpp"
#include "server/protocol.hpp"
#include "server/renderers.hpp"
#include <cmath>
#include <fstream>
#include <iostream>
#include <stdexcept>

using sim::Json;

static void check(bool condition, const char* message) {
    if (!condition) throw std::runtime_error(message);
}

static void check_invalid_gltf(const std::filesystem::path& source) {
    const auto original = sim::read_json(source);
    const auto fixture = std::filesystem::current_path()/
        ("invalid-asset-"+std::to_string(sim::monotonic_us())+".gltf");
    auto reject = [&](const Json& document, std::string_view expected) {
        std::ofstream output(fixture);
        output << document;
        output.close();
        check(bool(output),"Wrote the glTF test asset");
        try {
            sim::GltfAssetLoader{}.load(fixture);
        } catch (const std::invalid_argument& error) {
            std::filesystem::remove(fixture);
            check(std::string_view(error.what()).find(expected) != std::string_view::npos,
                  "glTF was rejected for the expected reason");
            return;
        }
        std::filesystem::remove(fixture);
        throw std::runtime_error("Invalid glTF was accepted");
    };
    auto wrapped_view = original;
    wrapped_view["bufferViews"][2]["byteOffset"] = UINT64_MAX-31;
    reject(wrapped_view,"buffer view range");
    auto wrapped_accessor = original;
    wrapped_accessor["accessors"][2]["count"] = UINT64_MAX/2+1;
    reject(wrapped_accessor,"accessor range");
    auto missing_positions = original;
    missing_positions["accessors"][0].erase("bufferView");
    reject(missing_positions,"buffer-backed glTF POSITION");
    auto transparent = original;
    transparent["materials"][0]["alphaMode"] = "BLEND";
    reject(transparent,"opaque glTF material");
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

static void check_showcase(const char* extended, const char* original,
                           const std::shared_ptr<const sim::AssetCatalog>& catalog,
                           const sim::Renderer& renderer) {
    sim::World showcase(sim::read_json(extended),catalog), boxes(sim::read_json(original),catalog);
    showcase.input = boxes.input = {true,false,.57,1};
    check(sim::render_jpeg(showcase,renderer,{160,120,.8}) != sim::render_jpeg(boxes,renderer,{160,120,.8}),
          "Showcase changes the camera image");
    for (int i=0;i<420;++i) {
        showcase.step();
        boxes.step();
        auto a = showcase.pose, b = boxes.pose;
        check(a.position.x == b.position.x && a.position.z == b.position.z &&
              a.direction.x == b.direction.x && a.direction.z == b.direction.z,
              "The showcase leaves the tour circle clear");
    }
}

static void check_new_visuals(const sim::AssetCatalog& catalog, const sim::Renderer& renderer) {
    sim::Map map{8,8,{},catalog.materials,"floor",{}};
    map.light.ambient = 1;
    map.light.directionals.front().intensity = 0;
    sim::CameraPose camera{{0,.21,1.5},{0,-1},{0,55,.04,12}};
    auto render = [&] { return renderer.render(map,camera,{112,84}).channels; };

    auto asset = std::make_shared<sim::Asset>();
    auto part = catalog.objects.at("leather-ottoman")->visuals.front();
    part.offset.y = .02;
    part.material = "wall";
    part.shape = sim::Shape::box;
    asset->visuals.push_back(part);
    map.objects.push_back({"test",{{0,0},0},asset,{},{}});
    auto box = render();
    asset->visuals[0].shape = sim::Shape::cylinder;
    auto cylinder = render();
    asset->visuals[0].shape = sim::Shape::ellipsoid;
    auto ellipsoid = render();
    check(box != cylinder && ellipsoid != box && ellipsoid != cylinder,"Curved shapes have distinct silhouettes");
    asset->visuals[0].roll = .35;
    check(ellipsoid != render(),"A tilted part changes its silhouette");

    const auto& gltf = catalog.objects.at("tool-case");
    const auto& mesh = *gltf->visuals.front().mesh;
    check(mesh.triangles.size() == 12 && mesh.uv.size() == mesh.positions.size(),"glTF loads indexed triangles and UVs");
    check(std::abs(mesh.positions.front().y-.012) < 1e-6 &&
          std::abs(mesh.positions.front().z+.2112) < 1e-6,"glTF loads hierarchical transforms");
    check(gltf->collision && gltf->collision->width == .52,"glTF loads the separate collision footprint");
    map.objects[0].asset = gltf;
    auto textured = render();
    check(textured == render(),"Textured mesh renders deterministically");
    map.materials.at("case-label").texture = {};
    auto untextured = render();
    check(textured != untextured,"Mesh UVs sample the image material");
    map.objects[0].material = "room-carpet";
    auto overridden = render();
    check(untextured != overridden,"Material override applies to the mesh");
    map.objects[0].color = std::array<int,3>{205,33,87};
    check(overridden != render(),"Instance color overrides the selected material's base color");
    map.objects.clear();
    check(untextured != render(),"Triangles occlude the scene");
}

static void check_textures_and_lights(const sim::AssetCatalog& catalog, const sim::Renderer& renderer) {
    sim::Map map{8,8,{},catalog.materials,"floor",{}};
    sim::CameraPose camera{{0,.25,1.8},{0,-1},{.45,60,.04,12}};
    auto render = [&] { return renderer.render(map,camera,{96,72}).channels; };
    for (const char* name : {"room-carpet","walnut","brown-leather","bag-fabric","shipping-cardboard","blue-rug"}) {
        map.floor_material = name;
        auto pixels = render();
        auto& texture = map.materials.at(name).texture;
        ++texture.seed;
        check(pixels != render(),"Procedural textures respond to the seed");
        --texture.seed;
        check(pixels == render(),"Procedural textures reproduce the same pixels for the same seed");
    }

    map.floor_material = "room-carpet";
    map.objects.push_back({"carton",{{0,0},0},catalog.objects.at("taped-carton"),{},{}});
    auto carton = render();
    map.materials.at("shipping-cardboard").texture.print = false;
    auto no_print = render();
    map.materials.at("shipping-cardboard").texture.tape = false;
    check(carton != no_print && no_print != render(),"Cardboard print and tape change the visible pixels independently");

    sim::DirectionalLight key{{-.4,1,.2},.85,0,.8}, fill{{.7,1,.1},.6,.04,.45};
    map.light = {{key},.35};
    auto hard = render();
    map.light.directionals[0].shadow_softness = .14;
    auto soft = render();
    check(hard != soft,"Directional shadow softness changes the shadow");
    map.light.directionals.push_back(fill);
    auto two_lights = render();
    check(soft != two_lights,"A second directional light changes illumination");
    *map.light.ambient = .15;
    check(two_lights != render(),"Ambient light changes illumination");
}

class FlatRenderer final : public sim::Renderer {
public:
    std::string_view id() const override { return "test-flat"; }
    sim::LinearRgbImage render(const sim::Map&, const sim::CameraPose&, sim::ImageSize size) const override {
        return {size,std::vector<double>(static_cast<size_t>(size.width)*size.height*3,100.75)};
    }
};

static void check_room(const char* path, const std::shared_ptr<const sim::AssetCatalog>& catalog,
                       const sim::Renderer& renderer) {
    sim::World room(sim::read_json(path), catalog);
    check(room.configuration.document.at("scenarioId") == "room-run6", "Room map keeps its scenario id");
    bool target = false;
    for (const auto& object : room.configuration.map->objects)
        if (object.id == "far-tall-block") target = true;
    check(target, "Room map names the chase target like chaser-depth-obstacles");
    check(!sim::render_jpeg(room, renderer, {96, 72, .8}).empty(), "Room map renders");
}

int main(int argc, char** argv) {
    try {
        check(argc==6,"Expected map, asset, showcase, and room paths");
        auto config = sim::read_json(argv[1]);
        check_invalid_gltf(std::filesystem::path(argv[2])/"objects/tool-case.gltf");
        auto assets = std::make_shared<const sim::AssetCatalog>(
            sim::load_asset_catalog(argv[2]));
        auto software = sim::make_renderer("software");
        check_assets(config,assets,*software);
        check_showcase(argv[3],argv[4],assets,*software);
        check_new_visuals(*assets,*software);
        check_textures_and_lights(*assets,*software);
        check_room(argv[5],assets,*software);

        auto shaded = config;
        shaded["map"]["light"] = {{"shadowStrength",0}};
        sim::World with_shadows(config,assets), without_shadows(shaded,assets);
        check(sim::render_rgb(with_shadows,*software,160,120) != sim::render_rgb(without_shadows,*software,160,120),
              "Map light controls shadows");

        FlatRenderer flat;
        auto rgb = sim::render_rgb(with_shadows,flat,161,121);
        check(rgb[0]==90 && rgb[(60*161+80)*3]==100,"Camera effects apply to any renderer's linear RGB");
        auto capture = sim::capture(with_shadows,flat,{161,121,.8});
        check(capture.at("sensor").at("image").at("rendererId")=="test-flat","Capture uses selected renderer ID");
        std::cout << "PASS: shapes, meshes, textures, overrides, showcase clearance, lighting, and camera pipeline\n";
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n'; return 1;
    }
}
