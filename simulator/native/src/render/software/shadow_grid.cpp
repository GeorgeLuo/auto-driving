#include "render/software/shadow_grid.hpp"

namespace sim::software {
ShadowGrid::ShadowGrid(const std::vector<Bounds>& parts, Vec3 direction)
    : slope_x_(direction.x/direction.y), slope_z_(direction.z/direction.y) {
    // Grazing directions and extreme bounds use the original BVH. Within
    // these limits, projection error fits well inside the padding below.
    if (parts.empty() || !(std::abs(slope_x_) <= 8 && std::abs(slope_z_) <= 8)) return;
    for (const auto& box : parts) for (int axis=0;axis<3;++axis)
        if (!(box.low[axis] >= -4096 && box.high[axis] <= 4096)) return;
    cells_.resize(side*side);

    const double margin_x = 1e-7*(1+std::abs(slope_x_));
    const double margin_z = 1e-7*(1+std::abs(slope_z_));
    std::vector<Bounds> projected;
    projected.reserve(parts.size());
    for (const auto& box : parts) {
        const double x0 = slope_x_*box.low[1], x1 = slope_x_*box.high[1];
        const double z0 = slope_z_*box.low[1], z1 = slope_z_*box.high[1];
        Bounds bounds{{box.low[0]-std::max(x0,x1)-margin_x,0,box.low[2]-std::max(z0,z1)-margin_z},
                      {box.high[0]-std::min(x0,x1)+margin_x,0,box.high[2]-std::min(z0,z1)+margin_z}};
        projected.push_back(bounds);
        domain_.extend(bounds);
    }
    for (int axis : {0,2}) factor_[axis] = side/(domain_.high[axis]-domain_.low[axis]);

    // Dense projections can put a part into every cell. Bound each grid's
    // storage and fall back to traversal when the index grows too large.
    constexpr size_t max_entries = 65536;
    size_t entries = 0;
    for (size_t i=0;i<projected.size();++i) {
        const auto& bounds = projected[i];
        const int x0 = cell(bounds.low[0],0), x1 = cell(bounds.high[0],0);
        const int z0 = cell(bounds.low[2],2), z1 = cell(bounds.high[2],2);
        entries += (z1-z0+1)*(x1-x0+1);
        if (entries > max_entries) {
            cells_.clear();
            return;
        }
        for (int z=z0;z<=z1;++z) for (int x=x0;x<=x1;++x)
            cells_[z*side+x].push_back(i);
    }
}

const std::vector<size_t>* ShadowGrid::candidates(Vec3 start) const {
    const double u = start.x-start.y*slope_x_, v = start.z-start.y*slope_z_;
    if (u < domain_.low[0] || u > domain_.high[0] || v < domain_.low[2] || v > domain_.high[2])
        return nullptr;
    return &cells_[cell(v,2)*side+cell(u,0)];
}

int ShadowGrid::cell(double value, int axis) const {
    return std::clamp(int((value-domain_.low[axis])*factor_[axis]),0,side-1);
}
}
