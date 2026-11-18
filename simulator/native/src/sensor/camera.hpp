#pragma once
#include "core/image.hpp"

namespace sim {
struct CameraEffects {
    double barrel_distortion, vignette;
};

std::vector<unsigned char> apply_camera(const LinearRgbImage& image, const CameraEffects& effects);
}
