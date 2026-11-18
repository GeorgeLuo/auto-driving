#pragma once
#include "render/renderer.hpp"
#include <map>
#include <mutex>

namespace sim {
namespace software { class MeshBvh; }

class SoftwareRenderer final : public Renderer {
public:
    std::string_view id() const override { return "native-software-v2"; }
    LinearRgbImage render(const Map& scene, const CameraPose& camera, ImageSize size) const override;
private:
    mutable std::mutex mesh_mutex_;
    mutable std::map<std::weak_ptr<const TriangleMesh>,std::shared_ptr<const software::MeshBvh>,
                     std::owner_less<>> meshes_;
};
}
