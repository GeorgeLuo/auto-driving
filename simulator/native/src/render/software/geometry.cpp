#include "render/software/geometry.hpp"

namespace sim::software {
namespace {
double face_shade(Vec3 n) {
    return .80*n.x*n.x+1.08*n.y*n.y+.96*n.z*n.z;
}

void fill_surface(Surface& surface, double distance, Vec3 normal, UV coords, UV uv, bool top) {
    surface = {distance,normal,coords,uv,coords,face_shade(normal),top};
}

bool box_hit(const VisualPart& box, const Ray& ray, double near, double far, Surface* surface) {
    std::array<double,3> local_origin{ray.origin.x,ray.origin.y,ray.origin.z};
    std::array<double,3> local_ray{ray.direction.x,ray.direction.y,ray.direction.z};
    std::array<double,3> mins{-box.width/2,0,-box.depth/2}, maxs{box.width/2,box.height,box.depth/2};
    double entry = -INFINITY, exit = INFINITY;
    int axis = 0;
    for (int i=0;i<3;++i) {
        if (std::abs(local_ray[i]) < 1e-12) {
            if (local_origin[i] < mins[i] || local_origin[i] > maxs[i]) return false;
            continue;
        }
        double first = (mins[i]-local_origin[i])/local_ray[i];
        double second = (maxs[i]-local_origin[i])/local_ray[i];
        double n = std::min(first,second);
        if (n > entry) { entry = n; axis = i; }
        exit = std::min(exit,std::max(first,second));
        if (entry > exit) return false;
    }
    if (entry < near || entry > far) return false;
    if (!surface) return true;

    auto p = ray.at(entry);
    double sign = local_ray[axis] < 0 ? 1 : -1;
    if (axis == 1) {
        fill_surface(*surface,entry,{0,sign,0},{p.x,p.z},{p.x/box.width+.5,p.z/box.depth+.5},sign > 0);
    } else if (axis == 0) {
        fill_surface(*surface,entry,{sign,0,0},{p.z,p.y},{p.z/box.depth+.5,1-p.y/box.height},false);
    } else {
        fill_surface(*surface,entry,{0,0,sign},{p.x,p.y},{p.x/box.width+.5,1-p.y/box.height},false);
    }
    // Legacy grain is projected along the X+Z diagonal on every box face.
    surface->grain_uv = {ray.origin.x+entry*ray.direction.x+ray.origin.z+entry*ray.direction.z,p.y};
    return true;
}

std::optional<std::array<double,2>> quadratic_roots(double a, double half_b, double c) {
    if (a <= 0) return std::nullopt;
    double discriminant = half_b*half_b-a*c;
    if (discriminant < 0) return std::nullopt;
    double root = std::sqrt(discriminant);
    return std::array<double,2>{(-half_b-root)/a,(-half_b+root)/a};
}

bool cylinder_hit(const VisualPart& part, const Ray& ray, double near, double far, Surface* surface) {
    double rx = part.width/2, rz = part.depth/2;
    double x = ray.origin.x/rx, z = ray.origin.z/rz;
    double dx = ray.direction.x/rx, dz = ray.direction.z/rz;
    double closest = far;
    bool side = false;
    // Test both roots. The near side can be outside the finite height.
    if (auto roots = quadratic_roots(dx*dx+dz*dz,x*dx+z*dz,x*x+z*z-1)) {
        for (double t : *roots) {
            double y = ray.origin.y+t*ray.direction.y;
            if (t < near || t > far || y < 0 || y > part.height) continue;
            closest = t;
            side = true;
            break;
        }
    }
    bool cap = false;
    double cap_y = 0;
    if (std::abs(ray.direction.y) >= 1e-12) {
        for (double y : {0.,part.height}) {
            double t = (y-ray.origin.y)/ray.direction.y;
            if (t < near || t > closest) continue;
            double u = x+dx*t, v = z+dz*t;
            if (u*u+v*v > 1) continue;
            cap = true;
            cap_y = y;
            closest = t;
        }
    }
    if (!cap && !side) return false;
    if (!surface) return true;
    auto p = ray.at(closest);
    if (cap) {
        bool top = cap_y > 0;
        fill_surface(*surface,closest,{0,top ? 1. : -1.,0},{p.x,p.z},{p.x/part.width+.5,p.z/part.depth+.5},top);
    } else {
        double angle = std::atan2(p.z/rz,p.x/rx);
        fill_surface(*surface,closest,unit({p.x/(rx*rx),0,p.z/(rz*rz)}),
                     {angle*(rx+rz)/2,p.y},{angle/(2*pi)+.5,1-p.y/part.height},false);
    }
    return true;
}

bool ellipsoid_hit(const VisualPart& part, const Ray& ray, double near, double far, Surface* surface) {
    double rx = part.width/2, ry = part.height/2, rz = part.depth/2;
    Vec3 start{ray.origin.x/rx,(ray.origin.y-ry)/ry,ray.origin.z/rz};
    Vec3 direction{ray.direction.x/rx,ray.direction.y/ry,ray.direction.z/rz};
    auto roots = quadratic_roots(dot(direction,direction),dot(start,direction),dot(start,start)-1);
    if (!roots) return false;
    double distance = (*roots)[0] >= near ? (*roots)[0] : (*roots)[1];
    if (distance < near || distance > far) return false;
    if (surface) {
        auto p = start+direction*distance;
        double angle = std::atan2(p.z,p.x);
        double latitude = std::asin(std::clamp(p.y,-1.,1.));
        fill_surface(*surface,distance,unit({p.x/rx,p.y/ry,p.z/rz}),
                     {angle*(rx+rz)/2,latitude*ry},{angle/(2*pi)+.5,.5-latitude/pi},false);
    }
    return true;
}
}

Transform::Transform(Vec3 position, double yaw, double pitch, double roll)
    : center(position), cosine(std::cos(yaw)), sine(std::sin(yaw)),
      pitch_cosine(std::cos(pitch)), pitch_sine(std::sin(pitch)),
      roll_cosine(std::cos(roll)), roll_sine(std::sin(roll)), tilted(pitch != 0 || roll != 0) {}

Vec3 Transform::local_vector(Vec3 v) const {
    Vec3 local{v.x*cosine-v.z*sine,v.y,v.x*sine+v.z*cosine};
    if (!tilted) return local;
    Vec3 p{local.x,local.y*pitch_cosine+local.z*pitch_sine,-local.y*pitch_sine+local.z*pitch_cosine};
    return {p.x*roll_cosine+p.y*roll_sine,-p.x*roll_sine+p.y*roll_cosine,p.z};
}

Vec3 Transform::world_vector(Vec3 v) const {
    if (tilted) {
        Vec3 r{v.x*roll_cosine-v.y*roll_sine,v.x*roll_sine+v.y*roll_cosine,v.z};
        v = {r.x,r.y*pitch_cosine-r.z*pitch_sine,r.y*pitch_sine+r.z*pitch_cosine};
    }
    return {v.x*cosine+v.z*sine,v.y,-v.x*sine+v.z*cosine};
}

Bounds Transform::world_bounds(const Bounds& local) const {
    Bounds bounds;
    for (double x : {local.low[0],local.high[0]})
        for (double y : {local.low[1],local.high[1]})
            for (double z : {local.low[2],local.high[2]})
                bounds.extend(center+world_vector({x,y,z}));
    return bounds;
}

MeshBvh::MeshBvh(const TriangleMesh& mesh) : bvh_([&] {
    std::vector<Bounds> bounds;
    bounds.reserve(mesh.triangles.size());
    triangles_.reserve(mesh.triangles.size());
    for (const auto& indices : mesh.triangles) {
        auto a = mesh.positions[indices[0]], b = mesh.positions[indices[1]], c = mesh.positions[indices[2]];
        triangles_.push_back({a,b-a,c-a,unit(cross(b-a,c-a))});
        Bounds triangle;
        triangle.extend(a); triangle.extend(b); triangle.extend(c);
        bounds.push_back(triangle);
    }
    return bounds;
}()) {}

bool MeshBvh::intersect(const TriangleMesh& mesh, const Ray& ray, double near, double far, Surface* surface) const {
    size_t closest_index = mesh.triangles.size();
    auto visit = [&](size_t index) {
        const auto& triangle = triangles_[index];
        auto p = cross(ray.direction,triangle.edge2);
        double determinant = dot(triangle.edge1,p);
        if (std::abs(determinant) < 1e-12) return false;
        double inverse = 1/determinant;
        auto relative = ray.origin-triangle.corner;
        double b = dot(relative,p)*inverse;
        if (b < 0 || b > 1) return false;
        auto q = cross(relative,triangle.edge1);
        double c = dot(ray.direction,q)*inverse;
        if (c < 0 || b+c > 1) return false;
        double distance = dot(triangle.edge2,q)*inverse;
        if (distance < near || distance > far || (distance == far && index >= closest_index)) return false;
        closest_index = index;
        if (!surface) return true;
        auto point = ray.at(distance);
        UV uv{point.x,point.y};
        if (!mesh.uv.empty()) {
            const auto& triangle_indices = mesh.triangles[index];
            auto u0 = mesh.uv[triangle_indices[0]], u1 = mesh.uv[triangle_indices[1]], u2 = mesh.uv[triangle_indices[2]];
            double a = 1-b-c;
            uv = {a*u0.u+b*u1.u+c*u2.u,a*u0.v+b*u1.v+c*u2.v};
        }
        auto normal = determinant > 0 ? triangle.normal : triangle.normal*(-1);
        fill_surface(*surface,distance,normal,uv,uv,normal.y > .5);
        far = distance;
        return false;
    };
    bvh_.intersect(BoundsRay(ray.origin,ray.direction),near,far,visit);
    return closest_index != mesh.triangles.size();
}

Bounds RenderPart::bounds() const {
    if (mesh) return transform.world_bounds(mesh->bounds());
    return transform.world_bounds({{-part.width/2,0,-part.depth/2},{part.width/2,part.height,part.depth/2}});
}

bool RenderPart::intersect(const Ray& world, double near, double far, Surface* surface) const {
    Ray local{transform.local_vector(world.origin-transform.center),transform.local_vector(world.direction)};
    bool found = false;
    switch (part.shape) {
    case Shape::box:
    case Shape::strip: found = box_hit(part,local,near,far,surface); break;
    case Shape::cylinder: found = cylinder_hit(part,local,near,far,surface); break;
    case Shape::ellipsoid: found = ellipsoid_hit(part,local,near,far,surface); break;
    case Shape::mesh: found = mesh->intersect(*part.mesh,local,near,far,surface); break;
    }
    if (found && surface) surface->normal = transform.world_vector(surface->normal);
    return found;
}
}
