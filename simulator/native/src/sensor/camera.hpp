#pragma once
#include "core/image.hpp"
#include <memory>
#include <mutex>

namespace sim {
struct CameraEffects {
    double barrel_distortion, vignette;
};

// Shared by captures of one configuration. Each application holds its own
// immutable sampling map, even when another capture requests a different size.
class CameraSampler {
public:
    explicit CameraSampler(CameraEffects effects) : effects_(effects) {}
    std::vector<unsigned char> apply(const LinearRgbImage& image) const;

private:
    struct SamplingMap;
    std::shared_ptr<const SamplingMap> sampling_map(ImageSize size) const;

    const CameraEffects effects_;
    mutable std::mutex mutex_;
    mutable std::shared_ptr<const SamplingMap> samples_;
};
}
