#pragma once
#include "render/software/bvh.hpp"
#include "scene/asset.hpp"

namespace sim::software {
inline Vec3 operator+(Vec3 a, Vec3 b) { return {a.x+b.x,a.y+b.y,a.z+b.z}; }
inline Vec3 operator-(Vec3 a, Vec3 b) { return {a.x-b.x,a.y-b.y,a.z-b.z}; }
inline Vec3 operator*(Vec3 a, double b) { return {a.x*b,a.y*b,a.z*b}; }
inline double dot(Vec3 a, Vec3 b) { return a.x*b.x+a.y*b.y+a.z*b.z; }
inline Vec3 cross(Vec3 a, Vec3 b) { return {a.y*b.z-a.z*b.y,a.z*b.x-a.x*b.z,a.x*b.y-a.y*b.x}; }
inline Vec3 unit(Vec3 v) {
    double length = std::sqrt(dot(v,v));
    return length > 0 ? v*(1/length) : Vec3{};
}

struct Ray {
    Vec3 origin, direction;
    Vec3 at(double distance) const { return origin+direction*distance; }
};

struct Transform {
    Vec3 center;
    double cosine, sine;
    double pitch_cosine, pitch_sine, roll_cosine, roll_sine;
    bool tilted;

    Transform(Vec3 position, double yaw, double pitch, double roll);
    Vec3 local_vector(Vec3 v) const;
    Vec3 world_vector(Vec3 v) const;
    Bounds world_bounds(const Bounds& local) const;
};

struct Surface {
    double distance;
    Vec3 normal;
    UV surface_uv, uv, grain_uv;
    double legacy_shade;
    bool top;
};

class MeshBvh {
public:
    explicit MeshBvh(const TriangleMesh& mesh);
    const Bounds& bounds() const { return bvh_.bounds(); }
    bool intersect(const TriangleMesh& mesh, const Ray& ray, double near, double far, Surface* surface) const;
private:
    struct Triangle {
        Vec3 corner, edge1, edge2, normal;
    };
    std::vector<Triangle> triangles_;
    Bvh bvh_;
};

struct RenderPart {
    const VisualPart& part;
    const Material& material;
    const std::array<int,3>& color;
    Transform transform;
    std::shared_ptr<const MeshBvh> mesh;

    Bounds bounds() const;
    bool intersect(const Ray& world, double near, double far, Surface* surface) const;
};
}
