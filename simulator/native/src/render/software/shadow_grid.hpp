#pragma once
#include "render/software/bvh.hpp"

namespace sim::software {
// A ray keeps the same XZ coordinates when projected along its own direction.
// Cells store conservative candidate indices. The caller tests exact hits.
class ShadowGrid {
public:
    ShadowGrid(const std::vector<Bounds>& parts, Vec3 direction);
    bool indexed() const { return !cells_.empty(); }
    const std::vector<size_t>* candidates(Vec3 start) const;

private:
    static constexpr int side = 40;
    int cell(double value, int axis) const;

    double slope_x_, slope_z_;
    Bounds domain_;
    std::array<double,3> factor_{};
    std::vector<std::vector<size_t>> cells_;
};
}
