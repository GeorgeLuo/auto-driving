#pragma once
#include "core/math.hpp"
#include <algorithm>
#include <array>
#include <cmath>
#include <limits>
#include <numeric>
#include <vector>

namespace sim::software {
struct BoundsRay {
    std::array<double,3> origin, inverse;
    BoundsRay(Vec3 start, Vec3 direction)
        : origin{start.x,start.y,start.z}, inverse{1/direction.x,1/direction.y,1/direction.z} {}
    BoundsRay(Vec3 start, const std::array<double,3>& reciprocal)
        : origin{start.x,start.y,start.z}, inverse(reciprocal) {}
};

struct Bounds {
    static constexpr double infinity = std::numeric_limits<double>::infinity();
    std::array<double,3> low{infinity,infinity,infinity}, high{-infinity,-infinity,-infinity};

    void extend(Vec3 point) {
        std::array<double,3> coords{point.x,point.y,point.z};
        for (int i=0;i<3;++i) {
            low[i] = std::min(low[i],coords[i]);
            high[i] = std::max(high[i],coords[i]);
        }
    }

    void extend(const Bounds& bounds) {
        for (int i=0;i<3;++i) {
            low[i] = std::min(low[i],bounds.low[i]);
            high[i] = std::max(high[i],bounds.high[i]);
        }
    }

    bool intersects(const BoundsRay& ray, double near, double far) const {
        for (int i=0;i<3;++i) {
            if (std::isinf(ray.inverse[i])) {
                if (ray.origin[i] < low[i] || ray.origin[i] > high[i]) return false;
                continue;
            }
            double a = (low[i]-ray.origin[i])*ray.inverse[i], b = (high[i]-ray.origin[i])*ray.inverse[i];
            near = std::max(near,std::min(a,b));
            far = std::min(far,std::max(a,b));
            if (near > far) return false;
        }
        return true;
    }
};

// A median split keeps the tree shallow even when many bounds overlap.
class Bvh {
public:
    explicit Bvh(const std::vector<Bounds>& bounds) : indices_(bounds.size()) {
        std::iota(indices_.begin(),indices_.end(),size_t{0});
        if (!bounds.empty()) build(bounds,0,indices_.size());
    }

    const Bounds& bounds() const { return nodes_.front().bounds; }

    // The visitor can shorten far for closest-hit queries, or return true to stop at a blocker.
    template<class Visit>
    bool intersect(const BoundsRay& ray, double near, double& far, const Visit& visit) const {
        return !nodes_.empty() && traverse(0,ray,near,far,visit);
    }

private:
    struct Node {
        Bounds bounds;
        size_t begin, count, right = 0;
    };
    std::vector<Node> nodes_;
    std::vector<size_t> indices_;

    size_t build(const std::vector<Bounds>& bounds, size_t begin, size_t end) {
        Bounds total, centers;
        for (size_t i=begin;i<end;++i) {
            const auto& b = bounds[indices_[i]];
            total.extend(b);
            centers.extend(Vec3{b.low[0]+b.high[0],b.low[1]+b.high[1],b.low[2]+b.high[2]});
        }
        // Allow for rounding at transformed or coplanar bounds.
        for (int i=0;i<3;++i) {
            total.low[i] -= 1e-8;
            total.high[i] += 1e-8;
        }
        const size_t index = nodes_.size();
        nodes_.push_back({total,begin,end-begin});
        if (end-begin <= 4) return index;
        int axis = 0;
        for (int i=1;i<3;++i)
            if (centers.high[i]-centers.low[i] > centers.high[axis]-centers.low[axis]) axis = i;
        auto center = [&](size_t i) { return bounds[i].low[axis]+bounds[i].high[axis]; };
        size_t middle = begin+(end-begin)/2;
        std::nth_element(indices_.begin()+begin,indices_.begin()+middle,indices_.begin()+end,
            [&](size_t a, size_t b) { return center(a) == center(b) ? a < b : center(a) < center(b); });
        build(bounds,begin,middle);
        const size_t right = build(bounds,middle,end);
        nodes_[index].right = right;
        nodes_[index].count = 0;
        return index;
    }

    template<class Visit>
    bool traverse(size_t index, const BoundsRay& ray, double near, double& far, const Visit& visit) const {
        const auto& node = nodes_[index];
        if (!node.bounds.intersects(ray,near,far)) return false;
        if (!node.count)
            return traverse(index+1,ray,near,far,visit) || traverse(node.right,ray,near,far,visit);
        for (size_t i=node.begin;i<node.begin+node.count;++i)
            if (visit(indices_[i])) return true;
        return false;
    }
};
}
