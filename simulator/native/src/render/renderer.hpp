#pragma once
#include "core/image.hpp"
#include "scene/map.hpp"
#include <string_view>

namespace sim {
struct Projection {
    double pitch_down, vertical_fov_degrees, near, far;
};

struct CameraPose {
    Vec3 position;
    Vec direction;
    Projection projection;
};

class Renderer {
public:
    virtual ~Renderer() = default;
    virtual std::string_view id() const = 0;
    // Capture and streaming can call this concurrently.
    virtual LinearRgbImage render(const Map& scene, const CameraPose& camera, ImageSize size) const = 0;
};
}
