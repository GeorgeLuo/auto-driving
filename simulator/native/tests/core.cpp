#include "protocol.hpp"
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
        check(argc==3,"Expected reference and config paths");
        auto config = sim::read_config(argv[2]);
        std::ifstream file(argv[1]); sim::Json reference; file>>reference;
        size_t frames = 0;
        for (const auto& test : reference.at("cases")) {
            auto c = config; c["spawn"] = test["spawn"];
            sim::World world(c);
            for (const auto& expected : test["frames"]) {
                const auto& input = expected["input"];
                world.input = {input["forward"],input["reverse"],input["steering"],1};
                world.step(); ++frames;
                auto name = test["name"].get<std::string>()+" frame "+std::to_string(world.frame);
                close(world.position.x,expected["position"]["x"],name+" x");
                close(world.position.z,expected["position"]["z"],name+" z");
                close(world.direction.x,expected["direction"]["x"],name+" dx");
                close(world.direction.z,expected["direction"]["z"],name+" dz");
            }
        }
        sim::World world(config);
        world.configure({{"vehicle",{{"speedUnitsPerFrame",.08}}}});
        close(world.config["vehicle"]["speedUnitsPerFrame"],.037,"configuration waits for tick");
        world.step();
        close(world.config["vehicle"]["speedUnitsPerFrame"],.08,"configuration applied");
        check(world.revision==1,"configuration revision");
        world.configure({{"vehicle",{{"speedUnitsPerFrame",.09}}}});
        world.playing = false;
        world.configure({{"vehicle",{{"speedUnitsPerFrame",.10}}}});
        world.step();
        close(world.config["vehicle"]["speedUnitsPerFrame"],.10,"paused update replaces pending update");
        auto before = world.native_state();
        rejects([&]{world.configure({{"vehicle",{{"wheelbase",0}}}});});
        rejects([&]{world.configure({{"typo",1}});});
        rejects([&]{world.configure({{"materials",{{"floor",{4294967296ULL,180,40}}}}});});
        rejects([&]{sim::command(world,"set-chaser-input",{{"steering",2}});});
        rejects([&]{auto invalid=config; invalid["spawn"]["x"]=100; sim::command(world,"load-map",invalid);});
        check(world.native_state()==before,"rejected updates must be atomic");
        auto image = sim::capture(world,{160,120,.8});
        check(world.native_state()==before,"capture must not advance or mutate state");
        check(image["passiveObservation"]["preservation"]["before"]==world.fingerprint(),"capture receipt");
        auto rgb = sim::render_rgb(world,160,120);
        check(rgb==sim::render_rgb(world,160,120),"rendering must be deterministic");
        world.playing = false;
        world.configure({{"materials",{{"floor",{20,180,40}}}}});
        check(rgb!=sim::render_rgb(world,160,120),"material update must change pixels");
        auto epoch = world.epoch;
        sim::command(world,"load-map",config);
        check(world.epoch!=epoch && world.frame==0 && !world.playing,"map load changes epoch and stays paused");
        std::cout << "PASS: " << frames << " JavaScript reference frames; atomic settings, capture, materials, and map reset\n";
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n'; return 1;
    }
}
