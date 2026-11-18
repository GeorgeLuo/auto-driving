#include "server/protocol.hpp"
#include "server/renderers.hpp"
#include "scene/asset_loader.hpp"
#include "core/overscan.hpp"
#include "server/arguments.hpp"
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

static void check_tour(sim::Json config, const std::shared_ptr<const sim::AssetCatalog>& assets) {
    sim::World plain(config,assets);
    const sim::Json start = {{"x",-2},{"z",1},{"headingRadians",0},{"steering",-.4}};
    config["tour"] = start;
    sim::World world(config,assets);
    for (auto* w : {&plain,&world}) {
        w->input.forward = true;
        w->step();
    }
    check(plain.native_state().at("pose")==world.native_state().at("pose") &&
          world.input.steering==0,"Chase uses spawn and its own steering");
    world.start_tour();
    close(world.pose.position.x,-2,"Tour x");
    close(world.pose.position.z,1,"Tour z");
    close(world.pose.direction.x,1,"Tour heading");
    close(world.input.steering,-.4,"Tour steering");
    world.start_tour({{"z",2},{"headingRadians",sim::pi/2},{"steering",0}});
    close(world.pose.position.x,-2,"Overrides preserve map x");
    close(world.pose.position.z,2,"Override z");
    close(world.pose.direction.z,1,"Override heading");
    close(world.input.steering,0,"Override steering");
    for (const auto& field : {"x","z","headingRadians","steering"}) {
        config["tour"] = start;
        config["tour"].erase(field);
        rejects([&]{sim::World invalid(config,assets);});
    }
    for (const auto& patch : {sim::Json{{"unknown",0}},sim::Json{{"x","2"}},sim::Json{{"z",100}},
                             sim::Json{{"headingRadians",7}},sim::Json{{"steering",1.1}}}) {
        config["tour"] = start;
        config["tour"].update(patch);
        rejects([&]{sim::World invalid(config,assets);});
    }
    config["tour"] = nullptr;
    rejects([&]{sim::World invalid(config,assets);});

    config["map"]["obstacles"] = sim::Json::array();
    for (const auto* field : {"spawn","tour"}) {
        auto settings = config;
        settings["tour"] = start;
        settings[field]["x"] = settings["map"]["columns"].get<double>()/2-.3;
        sim::World resized(settings,assets);
        resized.pose.position = {0,0};
        for (bool playing : {false,true}) {
            resized.playing = playing;
            auto before = resized.native_state();
            rejects([&]{resized.configure({{"vehicle",{{"radius",.4}}}});});
            check(resized.native_state()==before,"Radius update preserves spawn and tour clearance");
        }
    }
}

static void check_overscan() {
    for (int length : {1,2,65,640}) for (int other : {1,107,480}) {
        for (double distortion : {0.,.5}) {
            sim::OverscanAxis axis(length,other,distortion);
            double x = 1.-1./length, y = 1.-1./other;
            double extension = (length-1)*.5*distortion*(x*x+y*y);
            close(axis.ray_position(0),-extension,"Left corner ray");
            close(axis.ray_position(length-1),length-1+extension,"Right corner ray");
            for (int pixel=0;pixel<length;++pixel)
                close(axis.sample_position(axis.ray_position(pixel)),pixel,"Overscan inverse");
        }
    }
}

int main(int argc, char** argv) {
    try {
        check(argc==4,"Expected reference, map, and asset paths");
        auto assets = std::make_shared<const sim::AssetCatalog>(
            sim::load_asset_catalog(argv[3]));
        auto renderer = sim::make_renderer("software");
        auto config = sim::read_json(argv[2]);
        check_tour(config,assets);
        check_overscan();
        check(sim::integer_argument("65535",1,65535)==65535,"Port upper bound");
        for (const auto* text : {"","1x","2.5","65536","999999999999"})
            rejects([&]{sim::integer_argument(text,1,65535);});
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
        double before_fps_change = world.simulation_time;
        world.configure({{"fps",4}});
        close(world.next_step_interval(),.25,"scheduler uses the pending FPS");
        world.step();
        close(world.simulation_time-before_fps_change,.25,"simulation uses the same interval");
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
        auto boundary_config = config;
        boundary_config["map"]["columns"] = 1;
        boundary_config["map"]["obstacles"] = sim::Json::array();
        boundary_config["spawn"] = {{"x",.3},{"z",0},{"headingRadians",0}};
        boundary_config["vehicle"]["radius"] = .2;
        sim::World boundary(boundary_config,assets);
        boundary.pose.position.x = std::nextafter(.3,1.);
        boundary.configure({{"fps",20}});
        boundary.pose.position.x += 1e-8;
        rejects([&]{boundary.configure({{"fps",20}});});
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
        // This box lies wholly outside the pinhole view, but covers the
        // distorted ray at the right edge of the centre scanline.
        auto edge_config = shadow_config;
        edge_config["spawn"] = {{"x",0},{"z",0},{"headingRadians",0}};
        edge_config["camera"].update({{"pitchDownRadians",0},{"verticalFovDegrees",60}});
        edge_config["map"]["obstacles"].push_back({
            {"id","edge-marker"},{"x",2},{"z",1.6},{"width",.2},{"depth",.25},
            {"height",1},{"rotationRadians",0},{"boundary",false}});
        sim::World edge(edge_config,assets);
        edge.playing = false;
        const auto pinhole = sim::render_rgb(edge,*renderer,65,65);
        edge.configure({{"camera",{{"barrelDistortion",.42}}}});
        const auto distorted = sim::render_rgb(edge,*renderer,65,65);
        const size_t middle = (32*65+32)*3, right = (32*65+64)*3;
        check(pinhole[right]==pinhole[middle] && distorted[middle]==pinhole[middle] &&
              distorted[right]!=pinhole[right],"Distortion sees geometry beyond the pinhole edge");
        auto epoch = world.epoch;
        sim::command(world,"load-map",config);
        check(world.epoch!=epoch && world.frame==0 && !world.playing,"map load changes epoch and stays paused");
        std::cout << "PASS: " << frames << " JavaScript reference frames; atomic settings, capture, materials, and map reset\n";
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n'; return 1;
    }
}
