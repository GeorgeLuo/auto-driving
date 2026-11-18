#pragma once
#include "render/renderer.hpp"
#include "render/software/tile_pool.hpp"
#include <map>
#include <mutex>

namespace sim {
namespace software { class MeshBvh; }

class SoftwareRenderer final : public Renderer {
public:
    explicit SoftwareRenderer(unsigned workers);
    ~SoftwareRenderer() override;
    std::string_view id() const override { return "native-software-v2"; }
    LinearRgbImage render(const std::shared_ptr<const Map>& scene, const CameraPose& camera,
                          ImageSize size) const override;
private:
    struct ShadowCache;
    // A single renderer serves capture and streaming. Serialize frame setup
    // and tile submission so those callers share one worker budget.
    mutable std::mutex render_mutex_;
    mutable software::TilePool tiles_;
    mutable std::unique_ptr<const ShadowCache> shadows_;
    mutable std::map<std::weak_ptr<const TriangleMesh>,std::shared_ptr<const software::MeshBvh>,
                     std::owner_less<>> meshes_;
};
}
