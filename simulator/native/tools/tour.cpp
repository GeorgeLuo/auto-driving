// Deterministic visual tour of the native simulator: the car drives a fixed circle in the middle of a map and every
// Nth frame is rendered and encoded exactly as the simulator's camera would. Same map, same build => same images.
// Usage: tour <map.json> <out_dir> [frames=420] [every=3] [steering=0.57] [width=640] [height=480]
// Builds against either simulator layout: the flat prototype (render.hpp) or the layered one (server/protocol.hpp).
#include <cstdio>
#include <filesystem>
#include <fstream>
#include <iostream>
#if __has_include("server/protocol.hpp")
#include "server/protocol.hpp"
#include "server/renderers.hpp"
#include "scene/asset_loader.hpp"
#define LAYERED 1
#else
#include "render.hpp"
#endif

int main(int argc, char** argv) {
    if (argc < 3) { std::cerr << "usage: tour <map.json> <out_dir> [frames] [every] [steering] [width] [height]\n"; return 2; }
    auto config = sim::read_config(argv[1]);
    std::filesystem::path out(argv[2]);
    std::filesystem::create_directories(out);
    int frames = argc > 3 ? std::stoi(argv[3]) : 420, every = argc > 4 ? std::stoi(argv[4]) : 3;
    double steering = argc > 5 ? std::stod(argv[5]) : .57;
    sim::ImageRequest request;
    request.width = argc > 6 ? std::stoi(argv[6]) : 640; request.height = argc > 7 ? std::stoi(argv[7]) : 480;
#ifdef LAYERED
    sim::World world(config, std::make_shared<const sim::AssetCatalog>(
        sim::load_asset_catalog(SIM_ASSETS, sim::JsonAssetLoader{})));
    auto renderer = sim::make_renderer("software");
    auto jpeg_of = [&] { return sim::render_jpeg(world, *renderer, request); };
#else
    sim::World world(config);
    auto jpeg_of = [&] { return sim::encode_jpeg(sim::render_rgb(world, request.width, request.height), request); };
#endif
    world.input = {true, false, steering, 1};
    int written = 0;
    for (int i = 0; i < frames; ++i) {
        world.step();
        if (i % every) continue;
        auto jpeg = jpeg_of();
        char name[32]; std::snprintf(name, sizeof name, "frame_%05d.jpg", written++);
        std::ofstream(out / name, std::ios::binary).write(reinterpret_cast<const char*>(jpeg.data()), jpeg.size());
    }
    std::cout << written << " frames; final state " << world.native_state().dump().substr(0, 160) << "\n";
}
