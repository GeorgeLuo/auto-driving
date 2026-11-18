// Drive a fixed circle and encode camera frames with the simulator's camera pipeline.
#include "scene/asset_loader.hpp"
#include "server/protocol.hpp"
#include "server/renderers.hpp"
#include <cmath>
#include <cstdio>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <stdexcept>

namespace {
class TimedRenderer final : public sim::Renderer {
public:
    explicit TimedRenderer(const sim::Renderer& renderer) : renderer_(renderer) {}
    std::string_view id() const override { return renderer_.id(); }
    sim::LinearRgbImage render(const sim::Map& scene, const sim::CameraPose& camera,
                               sim::ImageSize size) const override {
        auto start = sim::monotonic_us();
        auto image = renderer_.render(scene,camera,size);
        elapsed_us_ += sim::monotonic_us()-start;
        return image;
    }
    double milliseconds() const { return double(elapsed_us_)/1000; }

private:
    const sim::Renderer& renderer_;
    mutable uint64_t elapsed_us_ = 0;
};
}

int main(int argc, char** argv) {
    if (argc < 3 || argc > 8) {
        std::cerr << "usage: native-sim-tour <map.json> <out_dir> [frames] [every] [steering] [width] [height]\n";
        return 2;
    }
    try {
        int frames = argc > 3 ? std::stoi(argv[3]) : 420;
        int every = argc > 4 ? std::stoi(argv[4]) : 3;
        double steering = argc > 5 ? std::stod(argv[5]) : .57;
        sim::ImageRequest request;
        request.width = argc > 6 ? std::stoi(argv[6]) : 640;
        request.height = argc > 7 ? std::stoi(argv[7]) : 480;
        if (frames < 1 || every < 1 || !std::isfinite(steering) || std::abs(steering) > 1)
            throw std::invalid_argument("Frames and interval must be positive and steering must be in [-1,1]");
        if (request.width < 1 || request.width > 640 || request.height < 1 || request.height > 480)
            throw std::invalid_argument("Camera size must be between 1x1 and 640x480");

        auto assets = std::make_shared<const sim::AssetCatalog>(sim::load_asset_catalog(SIM_ASSETS));
        sim::World world(sim::read_config(argv[1]),std::move(assets));
        auto software = sim::make_renderer("software");
        TimedRenderer renderer(*software);
        auto out = std::filesystem::absolute(argv[2]);
        std::filesystem::create_directories(out);

        world.input = {true,false,steering,1};
        int written = 0;
        for (int i=0;i<frames;++i) {
            world.step();
            if (i % every) continue;
            auto jpeg = sim::render_jpeg(world,renderer,request);
            char name[32];
            std::snprintf(name,sizeof name,"frame_%05d.jpg",written++);
            auto path = out/name;
            std::ofstream output(path,std::ios::binary);
            output.write(reinterpret_cast<const char*>(jpeg.data()),jpeg.size());
            output.close();
            if (!output) throw std::runtime_error("Cannot write " + path.string());
        }
        std::cout << written << " frames in " << out << '\n'
                  << std::fixed << std::setprecision(3) << renderer.milliseconds()/written
                  << " ms/frame render (" << request.width << 'x' << request.height << ")\n";
    } catch (const std::exception& error) {
        std::cerr << "Native simulator tour: " << error.what() << '\n';
        return 1;
    }
}
