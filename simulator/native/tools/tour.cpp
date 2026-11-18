// Drive a fixed circle and encode camera frames with the simulator's camera pipeline.
#include "scene/asset_loader.hpp"
#include "server/arguments.hpp"
#include "server/protocol.hpp"
#include "server/renderers.hpp"
#include <cstdio>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <limits>
#include <stdexcept>

namespace {
double tour_number(const std::string& value) {
    size_t end = 0;
    double result = std::stod(value,&end);
    if (end != value.size()) throw std::invalid_argument("Invalid tour value: " + value);
    return result;
}

class TimedRenderer final : public sim::Renderer {
public:
    explicit TimedRenderer(const sim::Renderer& renderer) : renderer_(renderer) {}
    std::string_view id() const override { return renderer_.id(); }
    sim::LinearRgbImage render(const std::shared_ptr<const sim::Map>& scene, const sim::CameraPose& camera,
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
    if (argc < 3) {
        std::cerr << "usage: native-sim-tour <map.json> <out_dir> [frames] [every] [steering] [width] [height]"
                     " [--x X] [--z Z] [--heading-radians A] [--steering S]\n";
        return 2;
    }
    try {
        int first_option = 3;
        while (first_option < argc && std::string_view(argv[first_option]).substr(0,2) != "--") ++first_option;
        if (first_option > 8) throw std::invalid_argument("Too many positional tour arguments");
        int frames = first_option > 3 ? sim::integer_argument(argv[3],1,std::numeric_limits<int>::max()) : 420;
        int every = first_option > 4 ? sim::integer_argument(argv[4],1,std::numeric_limits<int>::max()) : 3;
        sim::Json overrides = sim::Json::object();
        if (first_option > 5) overrides["steering"] = tour_number(argv[5]);
        sim::ImageRequest request;
        request.width = first_option > 6 ? sim::integer_argument(argv[6],1,640) : 640;
        request.height = first_option > 7 ? sim::integer_argument(argv[7],1,480) : 480;
        for (int i=first_option;i<argc;++i) {
            std::string flag = argv[i];
            if (flag != "--x" && flag != "--z" && flag != "--heading-radians" && flag != "--steering")
                throw std::invalid_argument("Unknown tour option: " + flag);
            if (++i == argc) throw std::invalid_argument("Missing value for " + flag);
            overrides[flag == "--heading-radians" ? "headingRadians" : flag.substr(2)] = tour_number(argv[i]);
        }

        auto assets = std::make_shared<const sim::AssetCatalog>(sim::load_asset_catalog(SIM_ASSETS));
        sim::World world(sim::read_json(argv[1]),std::move(assets));
        world.start_tour(overrides);
        auto software = sim::make_renderer("software");
        TimedRenderer renderer(*software);
        auto out = std::filesystem::absolute(argv[2]);
        std::filesystem::create_directories(out);

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
