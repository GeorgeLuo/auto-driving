#include "server/server.hpp"
#include "server/renderers.hpp"
#include "scene/asset_loader.hpp"
#include <csignal>
#include <fstream>
#include <iostream>

namespace {
volatile std::sig_atomic_t running = 1;
void stop(int) { running = 0; }
}
int main(int argc, char** argv) {
    try {
        int port = 5064;
        std::string config = DEFAULT_CONFIG, render, renderer_name = "software";
        bool benchmark = false;
        for (int i=1;i<argc;++i) {
            std::string flag = argv[i];
            if (flag == "--benchmark") { benchmark = true; continue; }
            if (flag == "--help") {
                std::cout << "automa-native-sim [--port N] [--config FILE] [--renderer NAME] [--render FILE.jpg] [--benchmark]\n"; return 0;
            }
            if (i+1 >= argc) throw std::invalid_argument("Missing value for " + flag);
            if (flag == "--port") port = std::stoi(argv[++i]);
            else if (flag == "--config") config = argv[++i];
            else if (flag == "--renderer") renderer_name = argv[++i];
            else if (flag == "--render") render = argv[++i];
            else throw std::invalid_argument("Unknown option: " + flag);
        }
        auto settings = sim::read_json(config);
        auto assets = std::make_shared<const sim::AssetCatalog>(
            sim::load_asset_catalog(DEFAULT_ASSETS));
        sim::World world(settings,std::move(assets));
        auto renderer = sim::make_renderer(renderer_name);
        if (!render.empty()) {
            auto bytes = sim::render_jpeg(world,*renderer,{});
            std::ofstream output(render,std::ios::binary);
            output.write(reinterpret_cast<const char*>(bytes.data()),bytes.size());
            if (!output) throw std::runtime_error("Cannot write image");
            return 0;
        }
        if (benchmark) {
            world.input.forward = true; world.input.steering = .35;
            for (int i=0;i<1000;++i) world.step();
            auto start = sim::monotonic_us();
            for (int i=0;i<100000;++i) world.step();
            double steps_ms = double(sim::monotonic_us()-start)/1000;
            start = sim::monotonic_us();
            size_t bytes = 0;
            for (int i=0;i<30;++i) {
                world.step(); bytes += sim::render_jpeg(world,*renderer,{}).size();
            }
            std::cout << sim::Json({{"physicsSteps",100000},{"physicsMs",steps_ms},
                {"cameraFrames",30},{"renderAndJpegMs",double(sim::monotonic_us()-start)/1000},
                {"jpegBytes",bytes}}).dump(2) << '\n'; return 0;
        }
        if (port < 1 || port > 65535) throw std::invalid_argument("Invalid port");
        std::signal(SIGINT,stop); std::signal(SIGTERM,stop);
        sim::Server server(port,std::move(world),std::move(renderer)); server.start();
        std::cout << "CHASE_UI_WS_URL=ws://127.0.0.1:" << port << "/ws/control" << std::endl;
        while (running) std::this_thread::sleep_for(std::chrono::milliseconds(25));
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "Native simulator: " << error.what() << '\n'; return 1;
    }
}
