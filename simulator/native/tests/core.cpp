#include "server/protocol.hpp"
#include "server/renderers.hpp"
#include "scene/asset_loader.hpp"
#include <cmath>
#include <fstream>
#include <iostream>
#include <stdexcept>

static void check(bool condition, const std::string& message) {
    if (!condition) throw std::runtime_error(message);
}
static void close(double actual, double expected, const std::string& label) {
    check(std::abs(actual-expected)<1e-10,label+": "+std::to_string(actual)+" != "+std::to_string(expected));
}
template<class F> static void rejects(F operation) {
    try { operation(); } catch (const std::exception&) { return; }
    throw std::runtime_error("Expected rejected operation");
}
int main(int argc, char** argv) {
    try {
        check(argc==4,"Expected reference, map, and asset paths");
        auto assets = std::make_shared<const sim::AssetCatalog>(
            sim::load_asset_catalog(argv[3],sim::JsonAssetLoader{}));
        auto renderer = sim::make_renderer("software");
        auto config = sim::read_config(argv[2]);
        std::ifstream file(argv[1]); sim::Json reference; file>>reference;
        size_t frames = 0;
        for (const auto& test : reference.at("cases")) {
            auto c = config; c["spawn"] = test["spawn"];
            sim::World world(c,assets);
            for (const auto& expected : test["frames"]) {
                const auto& input = expected["input"];
                world.input = {input["forward"],input["reverse"],input["steering"],1};
                world.step(); ++frames;
                auto name = test["name"].get<std::string>()+" frame "+std::to_string(world.frame);
                close(world.pose.position.x,expected["position"]["x"],name+" x");
                close(world.pose.position.z,expected["position"]["z"],name+" z");
                close(world.pose.direction.x,expected["direction"]["x"],name+" dx");
                close(world.pose.direction.z,expected["direction"]["z"],name+" dz");
            }
        }
        sim::World world(config,assets);
        world.configure({{"vehicle",{{"speedUnitsPerFrame",.08}}}});
        close(world.configuration.document["vehicle"]["speedUnitsPerFrame"],.037,"configuration waits for tick");
        world.step();
        close(world.configuration.document["vehicle"]["speedUnitsPerFrame"],.08,"configuration applied");
        check(world.revision==1,"configuration revision");
        world.configure({{"vehicle",{{"speedUnitsPerFrame",.09}}}});
        world.playing = false;
        world.configure({{"vehicle",{{"speedUnitsPerFrame",.10}}}});
        world.step();
        close(world.configuration.document["vehicle"]["speedUnitsPerFrame"],.10,"paused update replaces pending update");
        auto before = world.native_state();
        rejects([&]{world.configure({{"vehicle",{{"wheelbase",0}}}});});
        rejects([&]{world.configure({{"typo",1}});});
        rejects([&]{world.configure({{"materials",{{"floor",{4294967296ULL,180,40}}}}});});
        rejects([&]{sim::command(world,"set-chaser-input",{{"steering",2}});});
        rejects([&]{auto invalid=config; invalid["spawn"]["x"]=100; sim::command(world,"load-map",invalid);});
        check(world.native_state()==before,"rejected updates must be atomic");
        auto image = sim::capture(world,*renderer,{160,120,.8});
        check(world.native_state()==before,"capture must not advance or mutate state");
        check(image["passiveObservation"]["preservation"]["before"]==world.fingerprint(),"capture receipt");
        auto rgb = sim::render_rgb(world,*renderer,160,120);
        check(rgb==sim::render_rgb(world,*renderer,160,120),"rendering must be deterministic");
        world.playing = false;
        world.configure({{"materials",{{"floor",{20,180,40}}}}});
        check(rgb!=sim::render_rgb(world,*renderer,160,120),"material update must change pixels");
        // An obstacle behind the camera still casts a shadow onto visible floor.
        auto shadow_config = config;
        shadow_config["map"]["obstacles"] = sim::Json::array();
        shadow_config["camera"]["pitchDownRadians"] = sim::pi/3;
        shadow_config["camera"]["barrelDistortion"] = 0;
        shadow_config["camera"]["vignette"] = 0;
        sim::World lit(shadow_config,assets);
        auto lit_rgb = sim::render_rgb(lit,*renderer,160,120);
        auto caster_config = shadow_config;
        caster_config["map"]["obstacles"].push_back(
            {{"id","offscreen-caster"},{"x",-.5},{"z",3.25},{"width",.2},{"depth",.4},
             {"height",2},{"rotationRadians",0},{"boundary",false}});
        sim::World caster(caster_config,assets);
        auto shadow_rgb = sim::render_rgb(caster,*renderer,160,120);
        size_t center = (60*160+80)*3;
        check(shadow_rgb[center] < lit_rgb[center],"offscreen obstacle casts a floor shadow");
        sim::World sensor(shadow_config,assets);
        sensor.playing = false;
        sensor.configure({{"camera",{{"vignette",.1}}}});
        auto vignette_rgb = sim::render_rgb(sensor,*renderer,160,120);
        check(vignette_rgb[center] == lit_rgb[center],"vignette preserves image center");
        check(vignette_rgb[0] == std::round(lit_rgb[0]*.9),"vignette reaches configured strength at corners");
        sensor.configure({{"camera",{{"barrelDistortion",.12}}}});
        check(sim::render_rgb(sensor,*renderer,160,120) != vignette_rgb,"radial distortion resamples the image");
        auto epoch = world.epoch;
        sim::command(world,"load-map",config);
        check(world.epoch!=epoch && world.frame==0 && !world.playing,"map load changes epoch and stays paused");
        std::cout << "PASS: " << frames << " JavaScript reference frames; atomic settings, capture, materials, and map reset\n";
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n'; return 1;
    }
}
