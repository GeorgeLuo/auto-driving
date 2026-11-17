#pragma once
#include "render/renderer.hpp"

namespace sim {
class SoftwareRenderer final : public Renderer {
public:
    std::string_view id() const override { return "native-software-v2"; }
    LinearRgbImage render(const Map& scene, const CameraPose& camera, ImageSize size) const override;
};
}
