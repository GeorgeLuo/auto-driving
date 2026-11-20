// Generate a deterministic offline trajectory episode (images, applied controls, and evaluator truth).
#include "runner.hpp"
#include "scene/asset_loader.hpp"
#include "server/renderers.hpp"
#include <iomanip>
#include <iostream>
#include <stdexcept>
#include <string_view>

namespace {
class TimedRenderer final : public sim::Renderer {
public:
    explicit TimedRenderer(const sim::Renderer& renderer) : renderer_(renderer) {}
    std::string_view id() const override { return renderer_.id(); }
    sim::LinearRgbImage render(const std::shared_ptr<const sim::Map>& scene, const sim::CameraPose& camera,
                               sim::ImageSize size) const override {
        auto start = sim::monotonic_us();
        auto image = renderer_.render(scene, camera, size);
        elapsed_us_ += sim::monotonic_us() - start;
        return image;
    }
    double milliseconds() const { return double(elapsed_us_) / 1000.0; }

private:
    const sim::Renderer& renderer_;
    mutable uint64_t elapsed_us_ = 0;
};
} // namespace

int main(int argc, char** argv) {
    if (argc == 2 && std::string_view(argv[1]) == "--help") {
        std::cout << "usage: native-sim-dataset <spec.json> <out_dir>\n";
        return 0;
    }
    if (argc != 3) {
        std::cerr << "usage: native-sim-dataset <spec.json> <out_dir>\n";
        return 2;
    }
    try {
        auto assets = std::make_shared<const sim::AssetCatalog>(sim::load_asset_catalog(SIM_ASSETS));
        auto spec = sim::dataset::load_episode_spec_file(argv[1]);
        auto software = sim::make_renderer("software");
        TimedRenderer renderer(*software);
        auto result = sim::dataset::generate_episode(spec, argv[2], assets, renderer);
        const double ms_per_frame = result.captured_frames > 0
            ? renderer.milliseconds() / result.captured_frames
            : 0.0;
        std::cout << result.episode_id << ": " << result.total_steps << " steps, "
                  << result.captured_frames << " images, valid_for_turn_id="
                  << (result.valid_for_turn_id ? "true" : "false") << ", "
                  << std::fixed << std::setprecision(3) << ms_per_frame << " ms/frame render\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "Native simulator dataset: " << error.what() << '\n';
        return 1;
    }
}
